#!/usr/bin/env python3
"""Turn Njord race data into a report folder for the coaching debrief.

Usage:
  python analyze.py <race.csv> [<race.csv> ...] --out <dir> [--tws KTS] [--tz America/Los_Angeles]

Each CSV is Njord get_data output at 1 Hz (ISODateTimeUTC, SecondsSince1970, Lat, Lon, SOG,
Heading, Heel, ..., ideally TimeToGunCalc, BelowLineCalc, Leg, VMC). Optional sidecar files next
to it, found by name:
  <stem>-race.json      race name, timezone, startTime (gun), course elements
  <stem>-raceInfo.json  Njord detected events (Tack, Gybe, Gun, LineCrossed, ...)

Writes, per race, <out>/<stem>/:
  report.md       human-and-Claude-readable summary (read this first)
  summary.json    every number in report.md, machine-readable
  legs.csv        one row per leg
  maneuvers.csv   one row per tack/gybe
  targets.csv     upwind vs. Etchells card by wind band (only when wind is trustworthy)
  track.png, timeline.png, start.png, maneuvers.png, shifts.png, downwind.png
  plotdata.json   1 Hz series for the interactive HTML charts
  overlay.json    every comparable tack and gybe, aligned at the middle of the turn (maneuver_overlay.py)
  drift.json      steady-sailing COG vs heading samples for the current analysis (current.py)
and, across all races, <out>/executive.md (one factual line overall, per day and per race,
with flags for outliers) and <out>/event.md (the comparison table). With --html, also <out>/report.html: one self-contained
page (plots embedded) with an optional --debrief Markdown file at the top. html_report.py can
re-render it later without recomputing.

Numbers only. No coaching judgments here; that's the debrief's job.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from coach_calcs import BANDS, KT_TO_MS, interp_targets
import current
import ladder
import maneuver_overlay

EARTH_R_M = 6_371_000
STEADY_TRIM_S = 30  # drop this long after the gun and each leg start before steady-state stats
MANEUVER_EXCLUDE_S = (-10, 20)  # window around a tack/gybe excluded from steady state
RECOVERY_PCT = 0.95  # speed back to this share of entry speed counts as recovered
RECOVERY_CAP_S = 60
OCS_TOLERANCE_M = 1.0  # within this of the line at the gun counts as "on the line" (GPS error)


# ---------------------------------------------------------------- geometry


def bearing(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(np.asarray(lon2) - np.asarray(lon1))
    x = np.sin(dl) * np.cos(p2)
    y = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return np.degrees(np.arctan2(x, y)) % 360


def dist_m(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    h = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * np.arcsin(np.sqrt(h))


def adiff(a, b):
    """Signed smallest angle b - a, degrees in (-180, 180]."""
    d = (np.asarray(b) - np.asarray(a)) % 360
    return np.where(d > 180, d - 360, d)


def magnetic_variation(df) -> float | None:
    """Degrees east (true = magnetic + variation), from the boat's own true and magnetic channels.
    Everything is computed in true (GPS geometry is true); directions are shown magnetic."""
    for tru, mag_ in (("Heading", "Heading_Mag"), ("COG", "COG_Mag")):
        if tru in df and mag_ in df:
            ok = df[tru].notna() & df[mag_].notna()
            if ok.sum() >= 60:
                return round(float(np.median(adiff(df[mag_][ok], df[tru][ok]))), 1)
    if "MagneticVariation" in df and df.MagneticVariation.notna().any():
        return round(float(df.MagneticVariation.median()), 1)
    return None


def mag(v, res: dict):
    """A true direction as the sailor reads it: magnetic when the variation is known."""
    var = res.get("mag_var") if res else None
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float((v - var) % 360), 1) if var is not None else v


def north(res: dict) -> str:
    return "magnetic" if res and res.get("mag_var") is not None else "true"


def circ_mean(deg):
    r = np.radians(np.asarray(deg, dtype=float))
    r = r[~np.isnan(r)]
    if not len(r):
        return None
    return float(np.degrees(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) % 360)


def local_xy(lat, lon, lat0, lon0):
    """Metres east/north of (lat0, lon0); fine at race-course scale."""
    x = np.radians(np.asarray(lon) - lon0) * EARTH_R_M * math.cos(math.radians(lat0))
    y = np.radians(np.asarray(lat) - lat0) * EARTH_R_M
    return x, y


# ---------------------------------------------------------------- loading


@dataclass
class Race:
    name: str
    stem: str
    df: pd.DataFrame
    tz: ZoneInfo
    gun: pd.Timestamp | None
    course: list[dict]
    events: list[dict]
    meta: dict = field(default_factory=dict)


def load_race(csv_path: Path, tz_name: str | None) -> Race:
    df = pd.read_csv(csv_path, na_values=[""])
    if "SecondsSince1970" not in df.columns:
        sys.exit(f"{csv_path}: not a Njord get_data CSV (no SecondsSince1970 column)")
    df["t"] = pd.to_datetime(df.SecondsSince1970, unit="s", utc=True)
    df = df.sort_values("t").reset_index(drop=True)

    stem = csv_path.stem
    side = lambda suffix: csv_path.with_name(f"{stem}-{suffix}.json")
    meta = json.loads(side("race").read_text()) if side("race").exists() else {}
    events = []
    if side("raceInfo").exists():
        raw = json.loads(side("raceInfo").read_text())
        t0, t1 = df.t.iloc[0], df.t.iloc[-1]
        for e in raw.get("events", raw if isinstance(raw, list) else []):
            e = dict(e, t=pd.Timestamp(e["time"]))
            if t0 <= e["t"] <= t1:  # Njord returns events outside the window too
                events.append(e)

    gun = None
    if meta.get("startTime"):
        st = meta["startTime"]
        # Njord gives epoch milliseconds; an ISO string works too
        gun = (
            pd.Timestamp(st, unit="ms", tz="UTC") if isinstance(st, (int, float)) else pd.Timestamp(st)
        ).floor("s")
    elif "TimeToGunCalc" in df and df.TimeToGunCalc.notna().any():
        ttg = df.TimeToGunCalc
        i = int((ttg.abs()).idxmin())
        gun = df.t[i] + pd.Timedelta(seconds=float(ttg[i]))
    else:
        guns = [e["t"] for e in events if e.get("eventType") == "Gun"]
        gun = guns[0] if guns else None

    return Race(
        name=meta.get("race", stem),
        stem=stem,
        df=df,
        tz=ZoneInfo(tz_name or meta.get("timezone") or "UTC"),
        gun=gun,
        course=meta.get("course") or [],
        events=events,
        meta=meta,
    )


# ---------------------------------------------------------------- analysis


def upwind_axis(race: Race) -> float | None:
    """Bearing from the start line toward the first mark = the course's upwind direction."""
    line = next((c for c in race.course if c["type"] == "StartLine"), None)
    mark = next((c for c in race.course if c["type"] in ("Mark", "Gate")), None)
    if not (line and mark):
        return None
    mid_lat = (line["coord1"]["lat"] + line["coord2"]["lat"]) / 2
    mid_lon = (line["coord1"]["lon"] + line["coord2"]["lon"]) / 2
    m = mark["coord1"]
    if mark.get("coord2"):
        m = {
            "lat": (m["lat"] + mark["coord2"]["lat"]) / 2,
            "lon": (m["lon"] + mark["coord2"]["lon"]) / 2,
        }
    return float(bearing(mid_lat, mid_lon, m["lat"], m["lon"]))


def wind_quality(df: pd.DataFrame, upwind_mask: pd.Series) -> dict:
    """Decide whether logged TWS/TWA can be trusted (Etchells usually have no wind sensor)."""
    if "TWS" not in df or df.TWS.notna().sum() < 60:
        return {"trusted": False, "reason": "no TWS channel"}
    tws = df.TWS.dropna()
    top_share = float(tws.round(2).value_counts(normalize=True).iloc[0])
    reasons = []
    smooth = float((tws.diff().dropna().abs() < 0.02).mean())
    if smooth > 0.9:  # a real sensor jumps tenths of a knot a second; a model feed barely moves
        reasons.append(
            f"TWS changes by under 0.02 kt a second {smooth:.0%} of the time "
            "(a smoothed model feed, not measured on board)"
        )
    if top_share > 0.4:
        reasons.append(f"{top_share:.0%} of samples are exactly {tws.round(2).mode()[0]:g} kt")
    up_sog = df.SOG[upwind_mask].median()
    up_tws = df.TWS[upwind_mask].median()
    if pd.notna(up_sog) and pd.notna(up_tws) and up_sog > 1.5 * up_tws:
        reasons.append(f"upwind SOG {up_sog:.1f} kt is far above TWS {up_tws:.1f} kt")
    return {
        "trusted": not reasons,
        "reason": "; ".join(reasons) or "passes checks",
        "tws_median": round(float(tws.median()), 1),
    }


def detect_maneuvers(race: Race) -> list[dict]:
    kinds = {"Tack", "Gybe"}
    evs = [e for e in race.events if e.get("eventType") in kinds]
    if evs:
        return [{"t": e["t"], "kind": e["eventType"], "onto": e.get("direction")} for e in evs]
    # Fallback (no Njord maneuver events): heading swing > 70 deg within 15 s, 30 s debounce.
    # Heel tells tack from gybe: a tack swings the heel from one side to the other with
    # upwind heel on at least one side. Njord heel is negative on starboard tack.
    df = race.df
    out, last = [], None
    hdg = df.Heading if "Heading" in df else df.COG
    heel = df.Heel if "Heel" in df else None
    for i in range(15, len(df)):
        if df.SOG[i] < 1.5 or pd.isna(hdg[i]) or pd.isna(hdg[i - 15]):
            continue
        if abs(float(adiff(hdg[i - 15], hdg[i]))) > 70 and (
            last is None or (df.t[i] - last).total_seconds() > 30
        ):
            mid = i - 7
            last = df.t[mid]
            kind, onto = "Maneuver", None
            if heel is not None:
                before = heel[max(0, mid - 20) : max(0, mid - 5)].mean()
                after = heel[mid + 8 : mid + 23].mean()
                if pd.notna(before) and pd.notna(after):
                    upwind = max(abs(before), abs(after)) >= UPWIND_HEEL_DEG
                    kind = "Tack" if upwind and before * after < 0 else "Gybe"
                    if kind == "Tack":
                        onto = "Stbd" if after < 0 else "Port"
            out.append({"t": last, "kind": kind, "onto": onto})
    return out


def analyze_race(race: Race, tws_override: float | None) -> dict:
    df = race.df
    gun = race.gun
    df["tg"] = (df.t - gun).dt.total_seconds() if gun is not None else np.nan
    has_leg = "Leg" in df and df.Leg.nunique() > 1
    axis = upwind_axis(race)

    # --- legs: from Njord's course when it has marks, else detected from heel
    legs = []
    legs_source = "Njord course"
    n_marks = sum(c["type"] in ("Mark", "Gate") for c in race.course)
    if n_marks and gun is not None and (not has_leg or df.Leg.nunique() < n_marks + 1):
        legs = course_legs(race) or []
        if legs:
            has_leg = False
            legs_source = "from the course marks (Njord's leg split was incomplete)"
    if not has_leg and not legs and gun is not None:
        legs = detect_legs(race)
        legs_source = "detected from heel (course has no marks)"
        if axis is None:
            up = df[df.leg_type_detected == "upwind"]
            axis = circ_mean(up[up.SOG > 2].Heading if "Heading" in up else up[up.SOG > 2].COG)
    if has_leg:
        for leg, g in df[df.Leg.notna()].groupby("Leg"):
            leg_axis = float(bearing(g.Lat.iloc[0], g.Lon.iloc[0], g.Lat.iloc[-1], g.Lon.iloc[-1]))
            ref = axis if axis is not None else legs[0]["axis"] if legs else leg_axis
            upwind = abs(float(adiff(ref, leg_axis))) < 90
            legs.append(
                {
                    "leg": int(leg),
                    "type": "upwind" if upwind else "downwind",
                    "start": g.t.iloc[0],
                    "end": g.t.iloc[-1],
                    "axis": leg_axis,
                }
            )
    df["leg_type"] = None
    for lg in legs:
        df.loc[(df.t >= lg["start"]) & (df.t <= lg["end"]), "leg_type"] = lg["type"]
    upwind_mask = df.leg_type == "upwind"

    # --- maneuvers (only inside the race)
    mans = detect_maneuvers(race)
    if legs:
        r0, r1 = legs[0]["start"], legs[-1]["end"]
        mans = [m for m in mans if r0 <= m["t"] <= r1]

    # --- steady state mask: in a leg, past the trim, away from maneuvers
    steady = df.leg_type.notna()
    for lg in legs:
        steady &= ~(
            (df.t >= lg["start"]) & (df.t < lg["start"] + pd.Timedelta(seconds=STEADY_TRIM_S))
        )
    for m in mans:
        lo = m["t"] + pd.Timedelta(seconds=MANEUVER_EXCLUDE_S[0])
        hi = m["t"] + pd.Timedelta(seconds=MANEUVER_EXCLUDE_S[1])
        steady &= ~((df.t >= lo) & (df.t <= hi))
    df["steady"] = steady

    # --- wind: logged if trustworthy, else estimated from tacking headings
    wind = wind_quality(df, upwind_mask & steady)
    hdg_col = "Heading" if "Heading" in df else "COG"
    twd_est = estimate_twd(df, legs, hdg_col, axis)
    wind["twd_estimated"] = twd_est
    df["twd_used"] = df.TWD if wind["trusted"] else np.nan
    for lg in legs:
        if lg["type"] == "upwind" and lg.get("twd") is not None and not wind["trusted"]:
            sel = (df.t >= lg["start"]) & (df.t <= lg["end"])
            df.loc[sel, "twd_used"] = lg["twd"]
    if not wind["trusted"] and twd_est is not None:
        df["twd_used"] = df.twd_used.fillna(twd_est)
    rel = (df.twd_used - df[hdg_col]) % 360
    df["tack"] = np.where(rel.isna(), None, np.where(rel < 180, "stbd", "port"))
    df["twa_gps"] = np.abs(adiff(df[hdg_col], df.twd_used))
    # VMG toward/away from the (estimated) wind: symmetric across tacks, unlike VMC
    cog = df.COG if "COG" in df else df[hdg_col]
    toward = np.where(df.leg_type == "downwind", (df.twd_used + 180) % 360, df.twd_used)
    df["vmg_wind"] = df.SOG * np.cos(np.radians(adiff(toward, cog)))
    # VMC: how fast the distance to sail to the next mark shrank (ladder.py). This is the measure
    # of success: tacks, gybes, roundings and legs are all scored on it. Without marks there is
    # nothing to sail to, so it falls back to VMG along the wind.
    progress = "closing" if mark_progress(race, df, legs, axis) else "vmg_wind"

    leg_rows = [leg_stats(df, lg, hdg_col, mans, progress) for lg in legs]
    man_rows = [maneuver_stats(df, m, hdg_col, progress) for m in mans]
    for a, b in pairwise(man_rows):
        if b["time_s"] - a["time_s"] < 30:
            a["note"] = b["note"] = "double (<30 s apart; loss numbers overlap)"
    for m, row in zip(mans, man_rows, strict=True):  # detected legs aren't in Njord's Leg column
        row["leg"] = next(
            (lg["leg"] for lg in legs if lg["start"] <= m["t"] <= lg["end"]), row["leg"]
        )
    if legs_source.startswith("detected") and twd_est is not None:
        axis = twd_est  # better than the mean upwind heading used to find the tacks
    start = start_stats(race, df, axis) if gun is not None else None
    # After an OCS return the first beat starts at the restart, not the gun
    racing_from = (
        gun + pd.Timedelta(seconds=start["late_s"])
        if start and start.get("ocs_returned_s") is not None and start.get("late_s")
        else None
    )
    shifts = wind_shifts(df, legs, man_rows, gun, hdg_col, racing_from) if gun is not None else None
    roundings = (
        rounding_stats(race, df, legs, leg_rows, man_rows, shifts, progress) if gun is not None else []
    )
    targets = target_bands(df, wind, parse_tws(tws_override))

    return {
        "race": race.name,
        "stem": race.stem,
        "event": race.meta.get("event"),
        "boat": race.meta.get("boat"),
        "timezone": str(race.tz),
        "gun_local": _local(gun, race.tz),
        "finish_local": _local(legs[-1]["end"], race.tz) if legs else None,
        "duration_min": round((legs[-1]["end"] - legs[0]["start"]).total_seconds() / 60, 1)
        if legs
        else None,
        "channels": [c for c in df.columns if c[0].isupper() and df[c].notna().any()],
        "speed_source": "BoatSpeed" if "BoatSpeed" in df else "SOG",
        "upwind_axis": round(axis, 1) if axis is not None else None,
        # true = magnetic + mag_var; stored directions are true, reports show them magnetic
        "mag_var": magnetic_variation(df),
        "legs_source": legs_source if legs else None,
        # what gains and losses are measured in: VMC to the next mark, or (no marks) VMG
        "progress": "vmc" if progress == "closing" else progress,
        "wind": wind,
        "start": start,
        "legs": leg_rows,
        "maneuvers": man_rows,
        "maneuver_summary": maneuver_summary(man_rows),
        "targets": targets,
        "shifts": shifts,
        "roundings": roundings,
    }


# ---------------------------------------------------------------- wind shifts and tack calls

SHIFT_CALL_DEG = 3  # smaller than this isn't a shift worth tacking on (GPS heading noise)
MISSED_HEADER_DEG = 5
MISSED_HEADER_S = 45
MARK_ZONE_S = 90  # tacks this close to the end of a beat are about the mark, not the wind


def wind_shifts(df, legs, man_rows, gun, hdg_col, racing_from=None) -> dict:
    """Wind direction through each beat from headings, and whether each tack was a good call.

    Upwind, the boat sails at a roughly constant angle to the wind (half the tacking angle), so
    wind ~= heading + half on starboard and heading - half on port. Shifts are relative to the
    beat's median wind; + is a right shift (veer), - a left shift (back).
    """
    df["twd_inst"] = np.nan
    df["shift"] = np.nan
    df["shift_s"] = np.nan
    beats, calls = [], []
    for lg in legs:
        if lg["type"] != "upwind" or lg.get("twd") is None:
            continue
        half = lg["tacking_angle"] / 2
        if racing_from is not None and lg["start"] < racing_from < lg["end"]:
            lg = {**lg, "start": racing_from}  # restarted: from the restart on
        sel = (df.t >= lg["start"]) & (df.t <= lg["end"])
        ok = sel & df.steady & (df.SOG > 3) & df.tack.notna()
        # After the layline tack the boat sails to the mark (bearing away if it overstood):
        # that heading says nothing about the wind, so the trace stops there.
        tacks = [r for r in man_rows if r["kind"] == "Tack" and r["leg"] == lg["leg"]]
        if tacks:
            last = gun + pd.Timedelta(seconds=tacks[-1]["time_s"])
            if (lg["end"] - last).total_seconds() < 600:
                ok &= df.t < last
        raw = np.where(df.tack == "stbd", df[hdg_col] + half, df[hdg_col] - half) % 360
        df.loc[ok, "twd_inst"] = raw[ok]
        med = lg["twd"]
        df.loc[ok, "shift"] = adiff(med, df.loc[ok, "twd_inst"])
        df.loc[sel, "shift_s"] = (
            df.loc[sel, "shift"].rolling(30, center=True, min_periods=15).median()
        )
        g = df[sel]
        mins = (g.t - lg["start"]).dt.total_seconds() / 60
        m = g.shift_s.notna()
        if m.sum() < 120:
            continue
        slope, icpt = np.polyfit(mins[m], g.shift_s[m], 1)
        trend = float(slope * mins.iloc[-1])
        osc = float(np.std(g.shift_s[m] - (slope * mins[m] + icpt)))
        if abs(trend) >= 5 and abs(trend) > 1.5 * osc:
            pattern = f"persistent {'right' if trend > 0 else 'left'} shift"
        elif osc >= 3:
            pattern = "oscillating"
        else:
            pattern = "steady"
        # Leeway: the track over ground sits this far to leeward of the heading. Averaging both
        # tacks' |COG - heading| mostly cancels current.
        st = g[g.steady & (g.SOG > 3)]
        leeway = float(np.nanmean(np.abs(adiff(st[hdg_col], st.COG)))) if "COG" in g else 0.0
        beat = {
            "leg": lg["leg"],
            "twd_median": round(med, 1),
            "half_angle": round(half, 1),
            "leeway_deg": round(leeway, 1),
            "trend_deg": round(trend, 1),
            "oscillation_deg": round(osc, 1),
            "range_deg": [
                round(float(g.shift_s.quantile(0.1)), 1),
                round(float(g.shift_s.quantile(0.9)), 1),
            ],
            "pattern": pattern,
            **side_of_course(g, med),
        }
        beat["missed_headers"] = missed_headers(g, lg, gun)
        beat["missed_header_s"] = sum(x["duration_s"] for x in beat["missed_headers"])
        beat["side_note"] = side_note(beat)
        beats.append(beat)

        for i, r in enumerate(tacks):
            calls.append(tack_call(df, r, lg, gun, med, half, leeway, i, len(tacks)))

    summary = {}
    for c in calls:
        summary[c["verdict_kind"]] = summary.get(c["verdict_kind"], 0) + 1
    return {"beats": beats, "tacks": calls, "summary": summary}


def side_of_course(g, med) -> dict:
    """How far left/right of the rhumb line the boat worked, looking upwind."""
    rot = math.radians(med)
    x, y = local_xy(g.Lat.values, g.Lon.values, g.Lat.iloc[0], g.Lon.iloc[0])
    lat_x = x * math.cos(rot) - y * math.sin(rot)  # + = right, looking upwind
    up_y = x * math.sin(rot) + y * math.cos(rot)
    if up_y[-1] - up_y[0] < 50:
        return {}
    rhumb = lat_x[0] + (lat_x[-1] - lat_x[0]) * (up_y - up_y[0]) / (up_y[-1] - up_y[0])
    off = lat_x - rhumb
    return {
        "pct_time_right": round(100 * float((off > 0).mean())),
        "max_left_m": round(float(-off.min())),
        "max_right_m": round(float(off.max())),
    }


def side_note(beat) -> str:
    p = beat["pattern"]
    right = beat.get("pct_time_right")
    if right is None:
        return ""
    worked = "right" if right > 60 else "left" if right < 40 else "middle"
    if p.startswith("persistent"):
        favored = p.split()[1]
        ok = worked == favored
        return (
            f"Wind went {favored} {abs(beat['trend_deg']):.0f}° over the beat, which pays the {favored}; "
            f"we worked the {worked} ({right}% of the time right of the rhumb line)"
            + (" - right call." if ok else " - the other side should have paid.")
        )
    if p == "oscillating":
        return f"Oscillating (±{beat['oscillation_deg']:.0f}°): tacking on the headers is what pays. We worked the {worked}."
    return f"Steady wind; no side was favored by shifts. We worked the {worked}."


def missed_headers(g, lg, gun) -> list[dict]:
    """Stretches sailed headed by >= MISSED_HEADER_DEG for >= MISSED_HEADER_S without tacking."""
    headed = np.where(g.tack == "stbd", -g.shift_s, g.shift_s)
    open_water = (g.t >= lg["start"] + pd.Timedelta(seconds=STEADY_TRIM_S)) & (
        g.t <= lg["end"] - pd.Timedelta(seconds=MARK_ZONE_S)
    )
    flag = pd.Series((headed >= MISSED_HEADER_DEG) & open_water.values, index=g.index)
    out = []
    for _, run in g[flag].groupby((flag != flag.shift()).cumsum()[flag]):
        dur = (run.t.iloc[-1] - run.t.iloc[0]).total_seconds() + 1
        if dur >= MISSED_HEADER_S:
            h = np.where(run.tack == "stbd", -run.shift_s, run.shift_s)
            out.append(
                {
                    "start_s": round((run.t.iloc[0] - gun).total_seconds()),
                    "duration_s": round(dur),
                    "tack": run.tack.iloc[0],
                    "avg_header_deg": round(float(np.mean(h)), 1),
                }
            )
    return out


def tack_call(df, r, lg, gun, med, half, leeway, i, n) -> dict:
    t = gun + pd.Timedelta(seconds=r["time_s"])
    win = lambda a, b: df[
        (df.t >= t + pd.Timedelta(seconds=a)) & (df.t <= t + pd.Timedelta(seconds=b))
    ]
    before = win(-45, -8)["shift"].mean()
    after = win(20, 75)["shift"].mean()
    onto = r["onto"]
    # Before the tack we were on the other tack: starboard is headed by a left shift, port by a right one
    headed_before = (-before if onto == "Port" else before) if pd.notna(before) else None
    lifted_after = (-after if onto == "Port" else after) if pd.notna(after) else None
    to_end = (lg["end"] - t).total_seconds()
    call = {
        "time_s": r["time_s"],
        "leg": r["leg"],
        "onto": onto,
        "headed_before_deg": _r(headed_before, 1),
        "lifted_after_deg": _r(lifted_after, 1),
        "note": r.get("note"),
        "overstand_deg": None,
        "dist_to_mark_m": None,
    }
    if headed_before is None:
        shift_call = "unknown"
    elif headed_before >= SHIFT_CALL_DEG:
        shift_call = "on a header"
    elif headed_before <= -SHIFT_CALL_DEG:
        shift_call = "on a lift"
    else:
        shift_call = "no clear shift"
    call["shift_call"] = shift_call

    if i == n - 1 and to_end < 600:
        # Final tack of the beat: judge the layline, not the wind
        end = df[df.t <= lg["end"]].iloc[-1]
        k = (df.t - t).abs().idxmin()
        p = df.loc[k]
        b = float(bearing(p.Lat, p.Lon, end.Lat, end.Lon))
        local = med + (df.shift_s[k] if pd.notna(df.shift_s[k]) else 0)
        # Close-hauled track over ground on the new tack: half the tacking angle plus leeway
        track = half + leeway
        course = (local - track) % 360 if onto == "Stbd" else (local + track) % 360
        over = float(adiff(b, course)) if onto == "Stbd" else float(adiff(course, b))
        call["overstand_deg"] = round(over, 1)
        call["dist_to_mark_m"] = round(float(dist_m(p.Lat, p.Lon, end.Lat, end.Lon)))
        if over > 5:
            kind, verdict = "layline", f"layline tack, overstood by ~{over:.0f}°"
        elif over < -3:
            kind, verdict = "layline", f"layline tack, short by ~{-over:.0f}° (needed more tacks)"
        else:
            kind, verdict = "layline", "layline tack, good fetch"
    elif r["time_s"] < 60 and lg["leg"] == 1 and i == 0:
        kind, verdict = "start", "clearing tack off the start"
    elif lg["leg"] > 1 and (t - lg["start"]).total_seconds() < 60:
        kind, verdict = "mark", "tack right after the leeward mark"
    elif to_end < MARK_ZONE_S:
        kind, verdict = "mark", "tack in the mark zone"
    elif r.get("note"):
        kind, verdict = "double", f"double tack ({shift_call})"
    else:
        kind = {"on a header": "header", "on a lift": "lift"}.get(shift_call, "no_shift")
        verdict = f"tacked {shift_call}"
        if lifted_after is not None and kind != "no_shift":
            verdict += ", new tack " + (
                "lifted"
                if lifted_after >= SHIFT_CALL_DEG
                else "headed"
                if lifted_after <= -SHIFT_CALL_DEG
                else "neutral"
            )
    call["verdict"] = verdict
    call["verdict_kind"] = kind
    return call


UPWIND_HEEL_DEG = 9  # Etchells: ~15-25 deg upwind, ~2-6 deg downwind
MIN_LEG_S = 120


def finish_from_line(df, gun, end):
    """Njord's race end can run past the finish. With the finish on the start line, the finish
    is the last crossing from the course side (BelowLineCalc < 0) back behind the line."""
    if "BelowLineCalc" not in df:
        return end
    b = df.BelowLineCalc
    crossed = (b.shift() < 0) & (b >= 0) & (df.t > gun + pd.Timedelta(minutes=5)) & (df.t <= end)
    return df.t[crossed].iloc[-1] if crossed.any() else end


MARK_RADIUS_M = 100  # a passage is the closest approach while within this of the mark


def _course_points(el: dict) -> list[tuple[float, float]]:
    pts = [(el["coord1"]["lat"], el["coord1"]["lon"])]
    if el.get("coord2"):
        pts.append((el["coord2"]["lat"], el["coord2"]["lon"]))
    return pts


LINE_END_MARGIN_M = (
    20  # a crossing this far beyond either end still counts (GPS, boats at the ends)
)


def _line_crossing(df, el, after) -> pd.Timestamp | None:
    """First time after `after` that the track crosses the line between el's two points."""
    (alat, alon), (blat, blon) = _course_points(el)
    bx, by = local_xy(blat, blon, alat, alon)
    g = df[(df.t > after) & df.Lat.notna()]
    px, py = local_xy(g.Lat.to_numpy(), g.Lon.to_numpy(), alat, alon)
    side = np.sign(bx * py - by * px)  # which side of the line each fix is on
    L = math.hypot(bx, by)
    along = (px * bx + py * by) / L  # metres along the line from its first end
    for i in range(1, len(g)):
        if (
            side[i] != side[i - 1]
            and side[i - 1] != 0
            and -LINE_END_MARGIN_M <= along[i] <= L + LINE_END_MARGIN_M
        ):
            return g.t.iloc[i]
    return None


def course_legs(race: Race) -> list[dict] | None:
    """Legs from the course: the closest approach to each mark or gate in order (offsets are
    folded into the next leg), then the finish-line crossing. For when Njord's Leg column is
    incomplete (it depends on Njord detecting each rounding)."""
    df = race.df
    marks = [c for c in race.course if c["type"] in ("Mark", "Gate")]
    finish = next((c for c in race.course if c["type"] == "FinishLine"), None)
    if not marks or race.gun is None:
        return None
    # Leg 1 starts at the boat's own start (first line crossing after the gun), as Njord's does
    line = next((c for c in race.course if c["type"] == "StartLine"), None)
    crossed = _line_crossing(df, line, race.gun) if line and line.get("coord2") else None
    first = crossed if crossed is not None and crossed - race.gun < pd.Timedelta(minutes=2) else race.gun
    t, times = race.gun, [first]
    for el in marks:
        g = df[(df.t > t + pd.Timedelta(seconds=60)) & df.Lat.notna()]
        d = np.min(
            [
                np.hypot(*local_xy(g.Lat.to_numpy(), g.Lon.to_numpy(), lat, lon))
                for lat, lon in _course_points(el)
            ],
            axis=0,
        )
        near = np.flatnonzero(d < MARK_RADIUS_M)
        if not len(near):
            return None  # never reached this mark: can't split the race
        first = near[0]
        run_end = first
        while run_end + 1 < len(d) and d[run_end + 1] < MARK_RADIUS_M:
            run_end += 1
        t = g.t.iloc[first + int(np.argmin(d[first : run_end + 1]))]
        times.append(t)
    end = _line_crossing(df, finish, t + pd.Timedelta(seconds=30)) if finish else None
    times.append(end if end is not None else df.t.iloc[-1])
    axis = upwind_axis(race)
    legs = []
    for i, (a, b) in enumerate(itertools.pairwise(times), 1):
        g = df[(df.t >= a) & (df.t <= b)]
        leg_axis = float(bearing(g.Lat.iloc[0], g.Lon.iloc[0], g.Lat.iloc[-1], g.Lon.iloc[-1]))
        up = axis is None or abs(float(adiff(axis, leg_axis))) < 90
        legs.append(
            {
                "leg": i,
                "type": "upwind" if up else "downwind",
                "start": a,
                "end": b,
                "axis": leg_axis,
            }
        )
    return legs


def detect_legs(race: Race) -> list[dict]:
    """Split gun-to-finish into upwind/downwind legs by heel when the course has no marks."""
    df = race.df
    end = pd.Timestamp(race.meta["endTime"]) if race.meta.get("endTime") else df.t.iloc[-1]
    end = finish_from_line(df, race.gun, end)
    in_race = (df.t >= race.gun) & (df.t <= end)
    heel = df.Heel.abs().rolling(61, center=True, min_periods=20).median()
    mode = pd.Series(np.where(heel >= UPWIND_HEEL_DEG, "upwind", "downwind"), index=df.index)
    mode[~in_race] = None
    df["leg_type_detected"] = mode

    # Runs of the same mode; fold runs shorter than MIN_LEG_S into the previous run
    runs = []
    for m, g in df[in_race].groupby((mode[in_race] != mode[in_race].shift()).cumsum()):
        m = mode[g.index[0]]
        if runs and (runs[-1]["type"] == m or len(g) < MIN_LEG_S):
            runs[-1]["end"] = g.t.iloc[-1]
        else:
            runs.append({"type": m, "start": g.t.iloc[0], "end": g.t.iloc[-1]})
    # A short first run (e.g. reaching off the line) merges forward instead
    if len(runs) > 1 and (runs[0]["end"] - runs[0]["start"]).total_seconds() < MIN_LEG_S:
        runs[1]["start"] = runs[0]["start"]
        runs.pop(0)
    legs = []
    for i, r in enumerate(runs, 1):
        g = df[(df.t >= r["start"]) & (df.t <= r["end"])]
        axis = float(bearing(g.Lat.iloc[0], g.Lon.iloc[0], g.Lat.iloc[-1], g.Lon.iloc[-1]))
        legs.append(dict(r, leg=i, axis=axis, detected=True))
    return legs


def estimate_twd(df, legs, hdg_col, axis):
    """Wind direction from the bisector of steady port and starboard headings on each beat."""
    ests = []
    for lg in legs:
        if lg["type"] != "upwind":
            continue
        g = df[(df.t >= lg["start"]) & (df.t <= lg["end"]) & df.steady & (df.SOG > 2)]
        ref = axis if axis is not None else lg["axis"]
        off = adiff(ref, g[hdg_col])
        stb = g[hdg_col][(off < 0) & (off > -80)]  # starboard tack heads left of the wind
        prt = g[hdg_col][(off > 0) & (off < 80)]
        if len(stb) < 30 or len(prt) < 30:
            continue
        hs, hp = circ_mean(stb), circ_mean(prt)
        angle = float(abs(adiff(hs, hp)))
        lg["twd"] = circ_mean([hs, hp])
        lg["tacking_angle"] = round(angle, 1)
        lg["hdg_stbd"], lg["hdg_port"] = round(hs, 1), round(hp, 1)
        ests.append(lg["twd"])
    return round(circ_mean(ests), 1) if ests else None


ROUNDING_WINDOW_S = (-30, 60)  # VMG compared with the steady legs either side over this window
ROUNDING_FLAG_M = 60  # a rounding this costly gets a flag in the executive summary
SETTLED_VMG = 0.9  # 10 s VMC back to this share of the next leg's steady VMC = settled


def rounding_stats(race: Race, df, legs, leg_rows, man_rows, shifts, progress="vmg_wind") -> list[dict]:
    """Each mark rounding (the boundary between two legs): approach, speed through, and metres
    lost toward the marks against sailing the steady VMC of the leg before (to this mark) and
    after (to the next one). Without marks, VMG along the wind stands in for VMC."""
    steady_key = "closing_steady" if progress == "closing" else "vmg_steady"
    show_key = "vmc_steady" if progress == "closing" else "vmg_steady"
    course = race.course
    marks = [c for c in course if c["type"] in ("Mark", "Gate")]
    # An offset mark straight after a mark: the rounding runs on to the offset
    offsets = [
        course[i + 1] if i + 1 < len(course) and course[i + 1]["type"] == "Offset" else None
        for i, c in enumerate(course)
        if c["type"] in ("Mark", "Gate")
    ]
    calls = {c["time_s"]: c for c in (shifts or {}).get("tacks", [])}
    out = []
    for k, (before, after) in enumerate(pairwise(legs)):
        t_r = before["end"]
        tg_r = (t_r - race.gun).total_seconds()
        off_s = 0.0  # seconds from the mark to the offset
        off = offsets[k] if k < len(offsets) else None
        if off is not None:
            g = df[(df.t > t_r) & (df.t <= t_r + pd.Timedelta(seconds=120)) & df.Lat.notna()]
            if len(g):
                lat, lon = off["coord1"]["lat"], off["coord1"]["lon"]
                d = np.hypot(*local_xy(g.Lat.to_numpy(), g.Lon.to_numpy(), lat, lon))
                off_s = float((g.t.iloc[int(np.argmin(d))] - t_r).total_seconds())
        rb = next(r for r in leg_rows if r["leg"] == before["leg"])
        ra = next(r for r in leg_rows if r["leg"] == after["leg"])
        kind = "windward" if before["type"] == "upwind" else "leeward"
        rel = (df.t - t_r).dt.total_seconds()

        def w(a, b, rel=rel):
            return df[(rel >= a) & (rel <= b)]

        win = w(ROUNDING_WINDOW_S[0], ROUNDING_WINDOW_S[1] + off_s)
        base = np.where(rel[win.index] < 0, rb[steady_key] or np.nan, ra[steady_key] or np.nan)
        gap = (base - win[progress]) * KT_TO_MS
        ok_vmg = win[progress].notna().sum() > 30
        lost = float(np.nansum(gap)) if ok_vmg else None
        pre = (rel[win.index] < 0).values
        # 10 s trailing VMC, only over time after the rounding (before it, it was the other leg's)
        after_r = df[(rel >= off_s) & (rel <= 180 + off_s)]
        vmg10 = after_r[progress].rolling(10, min_periods=10).mean()
        settle = after_r[vmg10 >= SETTLED_VMG * (ra[steady_key] or np.inf)]
        # Approach: the last tack (windward) or gybe (leeward) of the leg before
        want = "Tack" if kind == "windward" else "Gybe"
        prev = [
            m
            for m in man_rows
            if m["kind"] == want and m["leg"] == before["leg"] and m["time_s"] < tg_r
        ]
        last = prev[-1] if prev else None
        row = {
            "n": k + 1,
            "type": kind,
            "time_s": round(tg_r),
            "from_leg": before["leg"],
            "to_leg": after["leg"],
            "last_" + want.lower() + "_before_s": round(tg_r - last["time_s"]) if last else None,
            "overstand_deg": (calls.get(last["time_s"]) or {}).get("overstand_deg")
            if last
            else None,
            "sog_entry": _r(w(-15, -5).SOG.mean(), 2),
            "sog_min": _r(w(-10, 20).SOG.min(), 2),
            "sog_exit": _r(w(20 + off_s, 30 + off_s).SOG.mean(), 2),
            "sog_steady_after": ra["sog_steady"],
            "settle_s": round(float((settle.t.iloc[0] - t_r).total_seconds()) - off_s)
            if len(settle)
            else None,
            # steady VMC (to the mark) of the legs either side: what the rounding is measured against
            "vmg_before": rb[show_key],
            "vmg_after": ra[show_key],
            "metres_lost": _r(lost, 1),
            "lost_before_m": _r(float(np.nansum(gap[pre])), 1) if ok_vmg else None,
            "lost_after_m": _r(float(np.nansum(gap[~pre])), 1) if ok_vmg else None,
            # Angle to the wind coming in (−20..−5 s) and going out (+15..+40 s, after any offset)
            "entry_twa": _r(w(-20, -5).twa_gps.mean(), 0),
            "exit_twa": _r(w(15 + off_s, 40 + off_s).twa_gps.mean(), 0),
            "offset_s": round(off_s) if off is not None else None,
            "mark_dist_m": None,
            "gate_side": None,
        }
        if k < len(marks):
            row.update(mark_approach(df, rel, marks[k], race))
        out.append(row)
    return out


def compare_roundings(results: list[dict]) -> None:
    """Add, to every rounding, the gap to the best rounding of its type across these races and
    up to three suggestions for a tighter one. The goal is zero; the best so far is the milestone."""
    all_r = [
        (res, r)
        for res in results
        for r in res.get("roundings") or []
        if r.get("metres_lost") is not None
    ]
    best = {}
    for res, r in all_r:
        if r["type"] not in best or r["metres_lost"] < best[r["type"]][1]["metres_lost"]:
            best[r["type"]] = (res, r)
    for res, r in all_r:
        bres, b = best[r["type"]]
        r["vs_best_m"] = round(r["metres_lost"] - b["metres_lost"], 1)
        r["best_ref"] = f"{bres['race']} #{b['n']}"
        r["best_m"] = b["metres_lost"]
        r["is_best"] = b is r
        r["tips"] = rounding_tips(r, b, res.get("maneuvers") or [])


def _drop_pct(r: dict) -> float | None:
    if r.get("sog_entry") and r.get("sog_min"):
        return 100 * (1 - r["sog_min"] / r["sog_entry"])
    return None


def rounding_tips(r: dict, b: dict, maneuvers: list[dict]) -> list[str]:
    """Plain suggestions from what this rounding did, compared with the best rounding of its
    type, ordered by roughly how much each cost. Up to three."""
    tips: list[tuple[float, str]] = []

    def tip(weight: float, *parts: str) -> None:
        tips.append((weight, "".join(parts)))

    before, after = r.get("lost_before_m") or 0, r.get("lost_after_m") or 0
    ex, bex = r.get("exit_twa"), b.get("exit_twa")
    best_ex = f" (your best exited at {bex:.0f}°)" if bex is not None else ""
    slow_settle = (r.get("settle_s") or 0) >= (b.get("settle_s") or 0) + 15
    windward = r["type"] == "windward"

    if windward and ex is not None and ex < 155:
        tip(
            after,
            f"Reached off at {ex:.0f}° to the wind for the first 15–40 s after the mark",
            best_ex,
            ". Bear away all the way to the run angle first, then set.",
        )
    if windward and (r.get("overstand_deg") or 0) > 5:
        tip(
            before + 5,
            f"Overstood the layline by ~{r['overstand_deg']:.0f}°. Tack onto it a ",
            "little sooner and sail the last 100 m at full upwind speed.",
        )
    elif windward and before >= 15:
        tip(
            before,
            f"Gave away {before:.0f} m in the 30 s before the mark. Keep target speed on ",
            "the layline; don't pinch up to the mark.",
        )
    if windward and slow_settle:
        tip(
            after * 0.8,
            f"Took {r['settle_s']} s to settle on the run (best {b.get('settle_s')} s). ",
            "Pole and halyard ready on the layline, so the set happens as you bear away.",
        )

    if not windward and before >= 20:
        tip(
            before,
            f"Lost {before:.0f} m on the way in. Drop earlier and set up wide so the turn ",
            "starts before the mark, not at it.",
        )
    if not windward and (r.get("mark_dist_m") or 0) > 6:
        tip(
            after * 0.5,
            f"Passed {r['mark_dist_m']:.0f} m from the mark. Wide in, tight out: leave ",
            "it about a boat length (3 m) away on the exit.",
        )
    if not windward and ex is not None and bex is not None and ex >= max(bex + 5, 38):
        tip(
            after,
            f"Came out at {ex:.0f}° to the wind, low and wide{best_ex}. Finish the turn ",
            "close to close-hauled; the wide entry is what makes a tight exit possible.",
        )
    elif not windward and ex is not None and bex is not None and ex <= bex - 5:
        tip(
            after,
            f"Came out pinching at {ex:.0f}° to the wind{best_ex}. Foot at your normal ",
            "upwind angle until speed is back, then point.",
        )
    if not windward and slow_settle:
        tip(
            after * 0.8,
            f"Took {r['settle_s']} s to settle upwind (best {b.get('settle_s')} s). ",
            "Speed before height.",
        )

    drop, bdrop = _drop_pct(r), _drop_pct(b)
    if drop is not None and drop >= 20 and (bdrop is None or drop >= bdrop + 5):
        tip(
            after * 0.6,
            f"Speed dropped {drop:.0f}% through the turn ({r['sog_entry']} → ",
            f"{r['sog_min']} kt; best {bdrop:.0f}%). "
            if bdrop is not None
            else f"{r['sog_min']} kt). ",
            "A smoother, rounder turn keeps more of it.",
        )

    # A tack after a leeward mark (or gybe after a windward one) inside the minute is in the number
    kind = "Tack" if not windward else "Gybe"
    soon = [m for m in maneuvers if m["kind"] == kind and 0 < m["time_s"] - r["time_s"] <= 60]
    if soon:
        m = soon[0]
        dt = m["time_s"] - r["time_s"]
        tip(
            (m.get("distance_lost_m") or 10) + 5,
            f"{ {'Tack': 'Tacked', 'Gybe': 'Gybed'}[kind] } {dt} s after the mark, so its cost is in ",
            "this number. Fine if it was for clear air or the favoured side; otherwise hold the lane ",
            "until you're up to speed.",
        )

    tips.sort(key=lambda x: -x[0])
    out = [t for _, t in tips[:3]]
    if not out and r is not b:
        out.append(
            f"No single fault stands out: {before:.0f} m went before the mark and {after:.0f} m "
            "after. Same routine, a little smoother, to close the gap to your best."
        )
    if r is b:
        out.insert(
            0, "Your best of this type so far: the benchmark to beat. The goal is still zero."
        )
    return out


def mark_approach(df, rel, mark, race) -> dict:
    """Closest approach to the mark within a minute of the rounding; for a gate, which side."""
    near = df[(rel >= -60) & (rel <= 60)]
    pts = [mark["coord1"]] + ([mark["coord2"]] if mark.get("coord2") else [])
    d = [dist_m(near.Lat.values, near.Lon.values, p["lat"], p["lon"]) for p in pts]
    best = int(np.argmin([x.min() for x in d]))
    out = {"mark_dist_m": round(float(d[best].min()), 1)}
    if len(pts) == 2:
        # Looking downwind (as you approach a leeward gate), which mark did we round?
        axis = upwind_axis(race)
        if axis is not None:
            b = float(
                bearing(
                    pts[1 - best]["lat"], pts[1 - best]["lon"], pts[best]["lat"], pts[best]["lon"]
                )
            )
            downwind = (axis + 180) % 360
            out["gate_side"] = (
                "right-hand mark (looking downwind)"
                if adiff(downwind, b) > 0
                else "left-hand mark (looking downwind)"
            )
    return out


NM = 1852.0


def leg_distance(g, s, lg, sailed_nm) -> dict:
    """Distance sailed vs. the straight line mark to mark, and vs. the shortest path at our own
    average angle to the wind (over the ground, so leeway included) in steady wind."""
    a, b = g.iloc[0], g.iloc[-1]
    straight = float(dist_m(a.Lat, a.Lon, b.Lat, b.Lon)) / NM
    out = {
        "straight_nm": round(straight, 2),
        "extra_pct": None,
        "ideal_nm": None,
        "extra_vs_ideal_m": None,
        "track_angle": None,
    }
    if straight < 0.05:
        return out
    out["extra_pct"] = round(100 * (sailed_nm / straight - 1), 1)
    twd = circ_mean(g.twd_used)
    st = s[s.SOG > 2]
    if twd is None or not len(st) or "COG" not in st:
        return out
    toward = twd if lg["type"] == "upwind" else (twd + 180) % 360
    ang = float(np.mean(np.abs(adiff(toward, st.COG))))  # track angle to the wind axis
    off = math.radians(float(adiff(toward, bearing(a.Lat, a.Lon, b.Lat, b.Lon))))
    along, cross = straight * math.cos(off), abs(straight * math.sin(off))
    if along > 0 and ang < 89 and cross <= along * math.tan(math.radians(ang)):
        ideal = along / math.cos(math.radians(ang))  # two boards at that angle, any split
    else:
        ideal = straight  # the mark can be laid directly
    out.update(
        ideal_nm=round(ideal, 2),
        extra_vs_ideal_m=round((sailed_nm - ideal) * NM),
        track_angle=round(ang, 1),
    )
    return out


def mark_progress(race: Race, df, legs, axis) -> bool:
    """Second by second, from each leg's laylines and rungs (ladder.py):

    to_go    metres still to sail to the next mark at the boats' tacking or gybing angle
    closing  how fast to_go shrank (kt): metres gained or lost toward the mark come from this
    vmc      VMC on the usual scale (kt): progress straight up (or down) the ladder toward the
             mark, i.e. closing x cos(half tacking angle). With the mark dead upwind it's classic
             VMC; off to one side it still counts only real progress, and overstanding counts
             against it.

    All 5 s centred. False when the course has no marks to sail to."""
    df["to_go"], df["closing"], df["vmc"] = np.nan, np.nan, np.nan
    els = [c for c in race.course if c["type"] in ("Mark", "Gate", "FinishLine")]
    start = next((c for c in race.course if c["type"] == "StartLine"), None)
    if not legs or len(els) != len(legs) or start is None or any(lg.get("detected") for lg in legs):
        return False
    lat0 = (start["coord1"]["lat"] + start["coord2"]["lat"]) / 2
    lon0 = (start["coord1"]["lon"] + start["coord2"]["lon"]) / 2
    rot = math.radians(axis or 0)

    def xy(lat, lon):  # the same frame as plot_data: start-line middle, upwind up
        x, y = local_xy(lat, lon, lat0, lon0)
        return x * math.cos(rot) - y * math.sin(rot), x * math.sin(rot) + y * math.cos(rot)

    x, y = xy(df.Lat.to_numpy(), df.Lon.to_numpy())
    pts = [[list(xy(*p)) for p in _course_points(c)] for c in els]
    ons, dirs = [], []
    for lg in legs:
        on = ((df.t >= lg["start"]) & (df.t <= lg["end"])).to_numpy()
        ons.append(on)
        settled = on & (df.t >= lg["start"] + pd.Timedelta(seconds=20)).to_numpy()
        dirs.append(ladder.track_dirs(x[settled], y[settled], df.SOG.to_numpy()[settled]))
    mids = lambda p: [float(np.mean([q[0] for q in p])), float(np.mean([q[1] for q in p]))]
    origins = [list(xy(lat0, lon0))] + [mids(p) for p in pts[:-1]]
    lads = ladder.build([lg["type"] for lg in legs], dirs, pts, origins)
    to_go = np.full(len(df), np.nan)
    rung = np.full(len(df), np.nan)  # cos(half tacking angle): distance to sail -> up the ladder
    over = np.full(len(df), np.nan)  # metres of to_go from being past a layline
    for on, lad in zip(ons, lads, strict=True):
        if lad.get("targets"):
            to_go[on] = ladder.to_go(x[on], y[on], lad, lad["targets"])
            over[on] = ladder.overstand(x[on], y[on], lad, lad["targets"])
            rung[on] = math.cos(math.radians(lad["half_deg"]))
    df["to_go"] = to_go
    df["overstand"] = over
    dt = df.t.diff().dt.total_seconds()
    made = -df.to_go.diff()  # metres closer to the mark since the last fix
    made[made < -50] = np.nan  # a new leg starts: the distance jumps up
    df["closing"] = (made / dt / KT_TO_MS).rolling(5, center=True, min_periods=3).mean()
    df["vmc"] = df.closing * rung
    df["rung_k"] = rung
    return bool(df.vmc.notna().any())


def leg_stats(df, lg, hdg_col, mans, progress="vmg_wind"):
    g = df[(df.t >= lg["start"]) & (df.t <= lg["end"])]
    s = g[g.steady]
    dur = (lg["end"] - lg["start"]).total_seconds()
    dist_nm = float(np.nansum(g.SOG) / 3600)  # 1 Hz
    heel = s.Heel.abs() if "Heel" in s else pd.Series(dtype=float)
    row = {
        "leg": lg["leg"],
        "type": lg["type"],
        "start_s": round((lg["start"] - df.t[df.tg.abs().idxmin()]).total_seconds()),
        "duration_s": round(dur),
        "distance_sailed_nm": round(dist_nm, 2),
        **leg_distance(g, s, lg, dist_nm),
        "sog_avg": round(float(g.SOG.mean()), 2),
        "sog_steady": round(float(s.SOG.mean()), 2) if len(s) else None,
        # VMC for the leg: the distance to sail at its start over the time it took (the measure
        # of success), and the steady-sailing average of VMC second by second
        "vmc_avg": round(float(g.to_go.iloc[0] * g.rung_k.iloc[0]) / dur / KT_TO_MS, 2)
        if progress == "closing" and len(g) and pd.notna(g.to_go.iloc[0]) and dur > 0
        else None,
        "vmc_steady": round(float(s.vmc.mean()), 2)
        if progress == "closing" and len(s) and s.vmc.notna().any()
        else None,
        # the same in distance-to-sail terms: what metres lost in roundings are measured against
        "closing_steady": round(float(s.closing.mean()), 2)
        if progress == "closing" and len(s) and s.closing.notna().any()
        else None,
        "maneuvers": sum(lg["start"] <= m["t"] <= lg["end"] for m in mans),
        "heel_abs_avg": round(float(heel.mean()), 1) if len(heel) else None,
        "heel_abs_std": round(float(heel.std()), 1) if len(heel) > 1 else None,
        "trim_avg": round(float(s.Trim.mean()), 1) if "Trim" in s and len(s) else None,
        "tacking_angle": lg.get("tacking_angle"),
        "twd_est": round(lg["twd"], 1) if lg.get("twd") is not None else None,
        # VMG toward (upwind) or away from (downwind) the wind, and the angle sailed to it
        "vmg_steady": round(float(s.vmg_wind.mean()), 2)
        if len(s) and s.vmg_wind.notna().any()
        else None,
        "twa_steady": round(float(s.twa_gps.mean()), 1)
        if len(s) and s.twa_gps.notna().any()
        else None,
        "gybes": sum(lg["start"] <= m["t"] <= lg["end"] and m["kind"] == "Gybe" for m in mans),
        "tacks": sum(lg["start"] <= m["t"] <= lg["end"] and m["kind"] == "Tack" for m in mans),
    }
    for side in ("stbd", "port"):
        t = s[s.tack == side]
        row[f"pct_time_{side}"] = round(100 * len(t) / len(s)) if len(s) else None
        row[f"sog_{side}"] = round(float(t.SOG.mean()), 2) if len(t) >= 20 else None
        row[f"heel_{side}"] = (
            round(float(t.Heel.abs().mean()), 1) if len(t) >= 20 and "Heel" in t else None
        )
    return row


def maneuver_stats(df, m, hdg_col, progress="vmg_wind"):
    t = m["t"]
    w = lambda a, b: df[
        (df.t >= t + pd.Timedelta(seconds=a)) & (df.t <= t + pd.Timedelta(seconds=b))
    ]
    entry = w(-10, -4)
    after = w(-3, 15)
    entry_sog = float(entry.SOG.mean()) if len(entry) else np.nan
    row = {
        "time_s": round(float(df.tg[(df.t - t).abs().idxmin()])),
        "kind": m["kind"],
        "onto": m["onto"],
        "leg": None,
        "entry_sog": round(entry_sog, 2),
        "min_sog": None,
        "speed_loss_kt": None,
        "speed_loss_pct": None,
        "recovery_s": None,
        "distance_lost_m": None,
        "heading_change": None,
        "note": None,
    }
    near = df.iloc[(df.t - t).abs().idxmin()]
    if "Leg" in df and pd.notna(near.Leg):
        row["leg"] = int(near.Leg)
    if not len(after) or np.isnan(entry_sog):
        return row
    i_min = after.SOG.idxmin()
    min_sog = float(df.SOG[i_min])
    rec = df[(df.index > i_min) & (df.t >= t) & (df.t <= t + pd.Timedelta(seconds=RECOVERY_CAP_S))]
    back = rec[rec.SOG >= RECOVERY_PCT * entry_sog]
    row.update(
        min_sog=round(min_sog, 2),
        speed_loss_kt=round(entry_sog - min_sog, 2),
        speed_loss_pct=round(100 * (entry_sog - min_sog) / entry_sog) if entry_sog else None,
        recovery_s=round((back.t.iloc[0] - t).total_seconds()) if len(back) else None,
    )
    pre, post = w(-12, -6), w(8, 14)
    if len(pre) and len(post):
        row["heading_change"] = round(
            float(adiff(circ_mean(pre[hdg_col]), circ_mean(post[hdg_col])))
        )
    # Metres lost toward the mark against keeping the VMC it had before the maneuver. Not for a
    # maneuver that straddles a mark (it's in the rounding's number: the VMC before was to the
    # other mark), and noted for a tack or gybe made from past a layline (its gain is ending the
    # overstand, a layline call, not boat handling).
    whole = w(-15, 25)
    if progress == "closing" and "to_go" in df and whole.to_go.diff().max() > 50:
        row["note"] = "at a mark (counted in the rounding)"
        return row
    if progress == "closing" and "overstand" in df and (w(-15, -5).overstand.mean() or 0) > 10:
        row["note"] = "from past the layline (ending an overstand)"
    # Split into handling (the dip against a baseline running from the VMC before, -15..-5 s, to
    # the VMC once settled after, +25..+35 s) and the call (the rest: what changing onto the new
    # tack's VMC was worth over the window). They add up to the total against the VMC before.
    base = w(-15, -5)[progress].mean()
    after_v = w(25, 35)[progress].mean()
    span = w(-5, 25)[progress]
    if pd.notna(base) and span.notna().sum() > 20:
        total = float(((base - span) * KT_TO_MS).sum())
        if pd.notna(after_v):
            f = ((span.index.to_series().map(df.tg) - df.tg[span.index[0]]) / 30.0).clip(0, 1)
            line = base + (after_v - base) * f
            handling = float(((line - span) * KT_TO_MS).sum())
            row["distance_lost_m"] = round(handling, 1)
            row["call_m"] = round(total - handling, 1)  # negative = the new tack gained
            if handling < -5 and not row["note"]:
                # a turn can't gain distance by itself: a shift or puff arrived inside the window
                row["note"] = "gained through the turn (a shift or puff): handling can't be judged"
        else:
            row["distance_lost_m"] = round(total, 1)
    return row


def maneuver_summary(rows):
    out = {}
    for kind in sorted({r["kind"] for r in rows}):
        rs = [
            r
            for r in rows
            if r["kind"] == kind and r["speed_loss_kt"] is not None and not r["note"]
        ]
        if not rs:
            continue
        d = [r["distance_lost_m"] for r in rs if r["distance_lost_m"] is not None]
        rec = [r["recovery_s"] for r in rs if r["recovery_s"] is not None]
        out[kind] = {
            "count": len(rs),
            "doubles_excluded": sum(1 for r in rows if r["kind"] == kind and r["note"]),
            "entry_sog_avg": round(np.mean([r["entry_sog"] for r in rs]), 2),
            "speed_loss_avg_kt": round(np.mean([r["speed_loss_kt"] for r in rs]), 2),
            "speed_loss_avg_pct": round(np.mean([r["speed_loss_pct"] for r in rs])),
            "recovery_avg_s": round(np.mean(rec), 1) if rec else None,
            "not_recovered_in_60s": len(rs) - len(rec),
            "distance_lost_avg_m": round(np.mean(d), 1) if d else None,
            "distance_lost_total_m": round(np.sum(d)) if d else None,
        }
        for side in ("Port", "Stbd"):
            sd = [
                r["distance_lost_m"]
                for r in rs
                if r["onto"] == side and r["distance_lost_m"] is not None
            ]
            if sd:
                out[kind][f"distance_lost_avg_onto_{side.lower()}_m"] = round(np.mean(sd), 1)
    return out


def start_stats(race, df, axis):
    at = lambda s: _interp(df, "tg", s)
    out = {"gun_local": _local(race.gun, race.tz)}
    for s in (-60, -30, -10, -5, 0, 5, 10, 30):
        out[f"sog_{s:+d}s"] = _r(at(s).get("SOG"), 2)
    out["accel_pm5s_kt"] = _r(_sub(out["sog_+5s"], out["sog_-5s"]), 2)

    if "BelowLineCalc" in df:
        for s in (-60, -30, -10, 0):
            out[f"below_line_{s:+d}s_m"] = _r(at(s).get("BelowLineCalc"), 1)
        below = out["below_line_+0s_m"]
        post = df[(df.tg >= 0) & (df.tg <= 120) & df.BelowLineCalc.notna()]
        crossed = post[post.BelowLineCalc <= 0]
        on_line = below is not None and below <= OCS_TOLERANCE_M
        out["late_s"] = (
            0.0 if on_line else round(float(crossed.tg.iloc[0]), 1) if len(crossed) else None
        )
        if below is not None and below < -OCS_TOLERANCE_M:
            out["ocs_at_gun_m"] = round(-below, 1)
            # Over at the gun: did the boat get back behind the line and start again?
            back = post[post.BelowLineCalc > 0]
            if len(back):
                t_back = float(back.tg.iloc[0])
                again = post[(post.tg > t_back) & (post.BelowLineCalc <= 0)]
                out["ocs_returned_s"] = round(t_back, 1)
                out["late_s"] = round(float(again.tg.iloc[0]), 1) if len(again) else None
        elif below is not None and below < 0:
            # Over by less than the tolerance: only an OCS if the boat went back to restart
            back = post[post.BelowLineCalc > OCS_TOLERANCE_M]
            if len(back):
                t_back = float(back.tg.iloc[0])
                again = post[(post.tg > t_back) & (post.BelowLineCalc <= 0)]
                if len(again):
                    out["ocs_at_gun_m"] = round(-below, 1)
                    out["ocs_returned_s"] = round(t_back, 1)
                    out["late_s"] = round(float(again.tg.iloc[0]), 1)
        if below and below > OCS_TOLERANCE_M and out["sog_+0s"]:
            out["late_tod_estimate_s"] = round(below / (out["sog_+0s"] * KT_TO_MS), 1)
    back = [
        e
        for e in race.events
        if e.get("eventType") == "LineCrossed"
        and e.get("LineCrossDirection") == "Backward"
        and 0 <= (e["t"] - race.gun).total_seconds() <= 120
    ]
    out["recrossed_after_gun"] = bool(back)
    pre_mans = sorted(
        (e["t"] - race.gun).total_seconds()
        for e in race.events
        if e.get("eventType") in ("Tack", "Gybe")
        and -300 <= (e["t"] - race.gun).total_seconds() < 0
    )
    out["prestart_maneuvers_5min"] = len(pre_mans)
    out["last_maneuver_before_gun_s"] = round(pre_mans[-1]) if pre_mans else None

    # Line position: where the boat crossed, as % of the line from the pin end
    line = next((c for c in race.course if c["type"] == "StartLine"), None)
    if line and axis is not None:
        cross_t = out.get("late_s") or 0.0  # on the line or OCS: position at the gun
        p = at(cross_t)
        a, b = line["coord1"], line["coord2"]
        lat0, lon0 = a["lat"], a["lon"]
        ax, ay = 0.0, 0.0
        bx, by = local_xy(b["lat"], b["lon"], lat0, lon0)
        px, py = local_xy(p["Lat"], p["Lon"], lat0, lon0)
        # Looking upwind, the pin is the left end
        b_bearing = float(bearing(a["lat"], a["lon"], b["lat"], b["lon"]))
        b_is_right = adiff(axis, b_bearing) > 0
        L = math.hypot(bx, by)
        frac = float(((px - ax) * bx + (py - ay) * by) / (L * L))
        pct_from_pin = frac if b_is_right else 1 - frac
        out["line_length_m"] = round(L)
        out["line_pos_pct_from_pin"] = round(100 * pct_from_pin)
        out["line_pos_label"] = (
            "pin end" if pct_from_pin < 0.2 else "boat end" if pct_from_pin > 0.8 else "middle"
        )
        dx, dy = px - (ax + frac * bx), py - (ay + frac * by)
        out["distance_from_line_at_cross_m"] = round(math.hypot(dx, dy), 1)
    pre = df[(df.tg >= -60) & (df.tg < 0)]
    if len(pre) and "tack" in pre:
        out["pct_last_60s_on_stbd"] = round(100 * (pre.tack == "stbd").mean())
    return out


def parse_tws(value) -> tuple[float, float] | None:
    """--tws 9 or --tws 8-10 -> (low, high) in knots."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value), float(value)
    lo, _, hi = str(value).partition("-")
    lo, hi = float(lo), float(hi or lo)
    if not 0 < lo <= hi:
        raise ValueError(f"bad --tws {value!r}; use e.g. 9 or 8-10")
    return lo, hi


def target_bands(df, wind, tws_range):
    """Upwind steady state vs. the Etchells card.

    With a user-supplied wind (one speed or a range) there's one row per knot across the range,
    so the reader sees how much the verdict depends on the exact wind. With trusted logged wind,
    rows are the usual 2-kt bands. Otherwise nothing: comparing to a made-up wind misleads.
    """
    up = df[(df.leg_type == "upwind") & df.steady & df.twa_gps.notna()].copy()
    up = up[up.twa_gps <= 60]
    spd = "BoatSpeed" if "BoatSpeed" in up else "SOG"
    up["heel_abs"] = up.Heel.abs()
    if tws_range is None and not wind["trusted"]:
        return {
            "available": False,
            "reason": f"wind speed not trustworthy ({wind['reason']}); pass --tws, e.g. --tws 8-10",
        }
    if not len(up):
        return {"available": False, "reason": "no steady upwind data"}

    def row(label, b, tws):
        t = interp_targets(tws)
        return {
            "wind": label,
            "seconds": len(b),
            "thin": len(b) < 60,
            "speed_avg": round(float(b[spd].mean()), 2),
            "speed_tgt": round(float(np.mean(t["Vb"])), 2),
            "speed_pct": round(float(np.mean(100 * b[spd] / t["Vb"])), 1),
            "heel_avg": round(float(b.heel_abs.mean()), 1),
            "heel_tgt": round(float(np.mean(t["Heel"])), 1),
            "heel_delta": round(float(np.mean(b.heel_abs - t["Heel"])), 1),
            "heel_std": round(float(b.heel_abs.std()), 1),
            "twa_avg": round(float(b.twa_gps.mean()), 1),
            "twa_tgt": round(float(np.mean(t["TWA"])), 1),
            "twa_delta": round(float(np.mean(b.twa_gps - t["TWA"])), 1),
        }

    if tws_range is not None:
        lo, hi = tws_range
        winds = sorted({lo, hi, *range(math.ceil(lo), math.floor(hi) + 1)})
        rows = [row(f"{w:g} kt", up, np.full(len(up), w)) for w in winds]
        source = (
            f"user-supplied TWS {lo:g}–{hi:g} kt" if hi > lo else f"user-supplied TWS {lo:g} kt"
        )
        mid = (lo + hi) / 2
        mid_heel = float(interp_targets([mid])["Heel"][0])
    else:
        rows = []
        for lo_b, hi_b in BANDS:
            b = up[(up.TWS >= lo_b) & (up.TWS < hi_b)]
            if len(b):
                rows.append(row(f"{lo_b}-{hi_b}" if hi_b < 40 else f"{lo_b}+", b, b.TWS.values))
        source = "logged TWS"
        mid_heel = None

    # Per beat and tack: where the heel (and speed) actually differed
    by_beat = []
    for leg in sorted(up.Leg.dropna().unique()) if "Leg" in up else []:
        for side in ("stbd", "port"):
            b = up[(up.Leg == leg) & (up.tack == side)]
            if len(b) < 60:
                continue
            tgt = (
                mid_heel if mid_heel is not None else float(np.mean(interp_targets(b.TWS)["Heel"]))
            )
            by_beat.append(
                {
                    "leg": int(leg),
                    "tack": side,
                    "seconds": len(b),
                    "speed_avg": round(float(b[spd].mean()), 2),
                    "heel_avg": round(float(b.heel_abs.mean()), 1),
                    "heel_std": round(float(b.heel_abs.std()), 1),
                    "pct_heel_over_tgt_plus5": round(100 * float((b.heel_abs > tgt + 5).mean())),
                }
            )
    return {
        "available": True,
        "tws_source": source,
        "twa_source": "logged TWD" if wind["trusted"] else "estimated from GPS tacking headings",
        "speed_source": spd,
        "bands": rows,
        "by_beat": by_beat,
        "by_beat_heel_target": round(mid_heel, 1) if mid_heel is not None else None,
    }


# ---------------------------------------------------------------- helpers


def _interp(df, xcol, x):
    i = (df[xcol] - x).abs().idxmin()
    if abs(df[xcol][i] - x) > 2:
        return {}
    return df.loc[i].to_dict()


def _r(v, n):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), n)


def _sub(a, b):
    return None if a is None or b is None else a - b


def _local(ts, tz):
    return ts.tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S") if ts is not None else None


def _fmt_mmss(s):
    if s is None:
        return "–"
    s = round(s)
    return f"{'-' if s < 0 else ''}{abs(s) // 60}:{abs(s) % 60:02d}"


# ---------------------------------------------------------------- plots

INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"


def _style(plt):
    plt.rcParams.update(
        {
            "figure.facecolor": "#fcfcfb",
            "axes.facecolor": "#fcfcfb",
            "axes.edgecolor": GRID,
            "axes.labelcolor": INK2,
            "axes.titlecolor": INK,
            "axes.titlesize": 12,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "xtick.color": INK2,
            "ytick.color": INK2,
            "font.size": 10,
            "legend.frameon": False,
        }
    )


def _blues():
    """Sequential blue that starts dark enough to stay visible on the light surface."""
    from matplotlib import colormaps
    from matplotlib.colors import ListedColormap

    return ListedColormap(colormaps["Blues"](np.linspace(0.35, 1.0, 256)))


START_TRACK_S = (-300, 30)  # start map: 5 min before the gun to 30 s after


def start_track(ax, race: Race, res: dict, df: pd.DataFrame):
    """Map of the approach, rotated so upwind is up, coloured by time to the gun."""
    from matplotlib.collections import LineCollection

    w = df[(df.tg >= START_TRACK_S[0]) & (df.tg <= START_TRACK_S[1])].dropna(subset=["Lat", "Lon"])
    if len(w) < 2:
        ax.set_axis_off()
        return
    line = next((c for c in race.course if c["type"] == "StartLine"), None)
    if line:
        lat0 = (line["coord1"]["lat"] + line["coord2"]["lat"]) / 2
        lon0 = (line["coord1"]["lon"] + line["coord2"]["lon"]) / 2
    else:
        lat0, lon0 = w.Lat.iloc[-1], w.Lon.iloc[-1]
    rot = math.radians(res["upwind_axis"] or 0)

    def xy(lat, lon):
        x, y = local_xy(lat, lon, lat0, lon0)
        return x * math.cos(rot) - y * math.sin(rot), x * math.sin(rot) + y * math.cos(rot)

    x, y = xy(w.Lat.values, w.Lon.values)
    pts = np.column_stack([x, y]).reshape(-1, 1, 2)
    segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
    before = w.tg.values[1:] <= 0
    lc = LineCollection(segs[before], cmap=_blues(), linewidths=2.2)
    lc.set_array(w.tg.values[1:][before])
    lc.set_clim(START_TRACK_S[0], 0)
    ax.add_collection(lc)
    ax.add_collection(LineCollection(segs[~before], colors=INK2, linewidths=1.6, linestyles="--"))
    cb = plt_colorbar(ax, lc)
    cb.set_ticks([-300, -240, -180, -120, -60, 0])
    cb.set_ticklabels(["-5:00", "-4:00", "-3:00", "-2:00", "-1:00", "gun"])

    # Minute marks along the track, and the position at the gun
    for sec in (-300, -240, -180, -120, -60, -30):
        p = _interp(df, "tg", sec)
        if p:
            px, py = xy(p["Lat"], p["Lon"])
            ax.plot(px, py, "o", ms=4, color=INK2)
            ax.annotate(
                _fmt_mmss(sec),
                (px, py),
                xytext=(5, 3),
                textcoords="offset points",
                fontsize=8,
                color=INK2,
            )
    p = _interp(df, "tg", 0)
    if p:
        px, py = xy(p["Lat"], p["Lon"])
        ax.plot(px, py, "o", ms=8, mfc=ORANGE, mec="#fcfcfb", mew=1.5, zorder=5)
        ax.annotate(
            "gun",
            (px, py),
            xytext=(6, -12),
            textcoords="offset points",
            fontsize=9,
            color=INK,
            fontweight="bold",
        )
    for e in race.events:
        if e.get("eventType") in ("Tack", "Gybe") and race.gun is not None:
            sec = (e["t"] - race.gun).total_seconds()
            q = _interp(df, "tg", sec) if START_TRACK_S[0] <= sec <= 0 else {}
            if q:
                qx, qy = xy(q["Lat"], q["Lon"])
                ax.plot(qx, qy, "o", ms=6, mfc="none", mec=INK2, mew=1.2)

    if line:
        (ax_, ay_), (bx_, by_) = (xy(line[k]["lat"], line[k]["lon"]) for k in ("coord1", "coord2"))
        ax.plot([ax_, bx_], [ay_, by_], color=INK, lw=2.5, solid_capstyle="round")
        left, right = sorted([(ax_, ay_), (bx_, by_)])  # upwind is up, so the pin is on the left
        ax.annotate(
            "pin",
            left,
            xytext=(-6, -4),
            textcoords="offset points",
            ha="right",
            fontsize=9,
            color=INK,
        )
        ax.annotate(
            "boat",
            right,
            xytext=(6, -4),
            textcoords="offset points",
            ha="left",
            fontsize=9,
            color=INK,
        )
    ax.set_aspect("equal")
    ax.autoscale()
    ax.margins(0.08)
    ax.set_xlabel("metres (upwind is up)")
    ax.set_title("Approach from 5:00 (open circles = tacks/gybes, dashed = after the gun)")


SHORT_CALL = {
    "header": "header",
    "lift": "lift",
    "no_shift": "–",
    "layline": "layline",
    "start": "start",
    "double": "double",
    "mark": "mark",
}


def shifts_plot(race: Race, res: dict, out: Path, plt):
    """Wind shift through each beat (from headings), coloured by tack, with each tack's call."""
    sh = res.get("shifts")
    if not sh or not sh["beats"]:
        return
    df = race.df
    beats = sh["beats"]
    fig, axes = plt.subplots(len(beats), 1, figsize=(10, 2.9 * len(beats) + 0.6), squeeze=False)
    for ax, b in zip(axes[:, 0], beats, strict=True):
        lg = next(x for x in res["legs"] if x["leg"] == b["leg"])
        t0 = race.gun + pd.Timedelta(seconds=lg["start_s"])
        g = df[(df.t >= t0) & (df.t <= t0 + pd.Timedelta(seconds=lg["duration_s"]))]
        m = g.tg / 60
        for side, color, label in (("stbd", BLUE, "on starboard"), ("port", ORANGE, "on port")):
            ax.plot(m, g.shift_s.where(g.tack == side), color=color, lw=2, label=label)
        ax.axhline(0, color=INK2, lw=0.8)
        ax.plot(
            [m.iloc[0], m.iloc[-1]],
            [0, b["trend_deg"]],
            color=INK2,
            lw=1,
            ls="--",
            label=f"trend {b['trend_deg']:+.0f}°",
        )
        for mh in b["missed_headers"]:
            ax.axvspan(
                mh["start_s"] / 60, (mh["start_s"] + mh["duration_s"]) / 60, color=GRID, zorder=0
            )
        lo, hi = ax.get_ylim()
        span = max(abs(lo), abs(hi), 8)
        ax.set_ylim(-span, span * 1.25)
        last_x, row = None, 0
        for c in sh["tacks"]:
            if c["leg"] != b["leg"]:
                continue
            x = c["time_s"] / 60
            row = 1 - row if last_x is not None and x - last_x < 0.9 else 0  # stagger close tacks
            last_x = x
            ax.axvline(x, color=INK2, lw=0.8, ls=":")
            ax.annotate(
                SHORT_CALL.get(c["verdict_kind"], ""),
                (x, span * (1.12 - 0.14 * row)),
                ha="center",
                fontsize=8,
                color=INK,
                fontweight="bold" if c["verdict_kind"] in ("header", "lift") else None,
            )
        ax.set_ylabel("shift (°)\n+ right: stbd lifted")
        ax.set_title(f"Leg {b['leg']}: median {mag(b['twd_median'], res):.0f}° {north(res)[:3]}, {b['pattern']}")
        ax.legend(loc="lower left", fontsize=8, ncol=3)
    axes[-1, 0].set_xlabel(
        "minutes from gun (dotted = tacks, labelled with the call; grey = headed > 5° without tacking)"
    )
    fig.suptitle(
        f"{res['race']}: wind shifts upwind (from headings)",
        x=0.01,
        ha="left",
        fontweight="bold",
        fontsize=13,
    )
    fig.tight_layout()
    fig.savefig(out / "shifts.png", dpi=130)
    plt.close(fig)


def downwind_plot(race: Race, res: dict, out: Path, plt):
    """Speed down each run, coloured by gybe, with each gybe and the metres it cost."""
    runs = [lg for lg in res["legs"] if lg["type"] == "downwind"]
    if not runs:
        return
    df = race.df
    fig, axes = plt.subplots(len(runs), 1, figsize=(10, 2.6 * len(runs) + 0.6), squeeze=False)
    for ax, lg in zip(axes[:, 0], runs, strict=True):
        t0 = race.gun + pd.Timedelta(seconds=lg["start_s"])
        g = df[(df.t >= t0) & (df.t <= t0 + pd.Timedelta(seconds=lg["duration_s"]))]
        m = g.tg / 60
        sog = g.SOG.rolling(5, center=True, min_periods=1).mean()
        for side, color, label in (("stbd", BLUE, "on starboard"), ("port", ORANGE, "on port")):
            ax.plot(m, sog.where(g.tack == side), color=color, lw=1.8, label=label)
        for mm in res["maneuvers"]:
            if mm["leg"] == lg["leg"] and mm["kind"] == "Gybe":
                x = mm["time_s"] / 60
                ax.axvline(x, color=INK2, lw=0.8, ls=":")
                lost = mm["distance_lost_m"]
                ax.annotate(
                    f"gybe {lost:+.0f} m" if lost is not None else "gybe",
                    (x, 1.0),
                    xycoords=("data", "axes fraction"),
                    xytext=(3, -12),
                    textcoords="offset points",
                    fontsize=8,
                    color=INK,
                )
        prog = (
            f"VMC {lg['vmc_avg']} kt to the mark, "
            if lg.get("vmc_avg") is not None
            else f"VMG {lg['vmg_steady']} kt, " if lg.get("vmg_steady") is not None else ""
        )
        ax.set_title(
            f"Leg {lg['leg']}: {prog}SOG {lg['sog_steady']} kt steady, "
            f"{lg['pct_time_stbd']}% on starboard"
        )
        ax.set_ylabel("SOG (kt, 5 s avg)")
        ax.legend(loc="lower left", fontsize=8, ncol=2)
    axes[-1, 0].set_xlabel("minutes from gun (dotted = gybes, labelled with metres lost)")
    fig.suptitle(f"{res['race']}: downwind", x=0.01, ha="left", fontweight="bold", fontsize=13)
    fig.tight_layout()
    fig.savefig(out / "downwind.png", dpi=130)
    plt.close(fig)


def polar_plot(race: Race, res: dict, out: Path, plt):
    """Speed vs. wind angle (from headings) on each beat: starboard right, port left, with the
    card's targets. Height on the chart is upwind VMG."""
    beats = [lg for lg in res["legs"] if lg["type"] == "upwind"]
    if not beats:
        return
    df = race.df
    targets = res["targets"].get("bands", []) if res["targets"].get("available") else []
    fig, axes = plt.subplots(1, len(beats), figsize=(5.5 * len(beats), 3.3), squeeze=False)
    for ax, lg in zip(axes[0], beats, strict=True):
        t0 = race.gun + pd.Timedelta(seconds=lg["start_s"])
        g = df[
            (df.t >= t0)
            & (df.t <= t0 + pd.Timedelta(seconds=lg["duration_s"]))
            & df.steady
            & (df.SOG > 2)
            & (df.twa_gps <= 60)
        ]
        for side, sign, color in (("stbd", 1, BLUE), ("port", -1, ORANGE)):
            t = g[g.tack == side]
            a = np.radians(t.twa_gps)
            ax.scatter(
                sign * t.SOG * np.sin(a), t.SOG * np.cos(a), s=4, alpha=0.25, color=color, lw=0
            )
            bins = t.groupby((t.twa_gps // 2) * 2).SOG.agg(["mean", "count"])
            bins = bins[bins["count"] >= 10]
            if len(bins):
                ang = np.radians(bins.index + 1)
                ax.plot(
                    sign * bins["mean"] * np.sin(ang),
                    bins["mean"] * np.cos(ang),
                    color=color,
                    lw=2,
                    label=f"{side} (avg)",
                )
        for tg in targets:
            ang = math.radians(tg["twa_tgt"])
            for sign in (1, -1):
                ax.plot(
                    sign * tg["speed_tgt"] * math.sin(ang),
                    tg["speed_tgt"] * math.cos(ang),
                    "D",
                    color=INK,
                    ms=5,
                )
            ax.annotate(
                tg["wind"],
                (tg["speed_tgt"] * math.sin(ang), tg["speed_tgt"] * math.cos(ang)),
                xytext=(5, -3),
                textcoords="offset points",
                fontsize=8,
                color=INK2,
            )
        for r in (4, 5, 6, 7):
            th = np.radians(np.linspace(-70, 70, 100))
            ax.plot(r * np.sin(th), r * np.cos(th), color=GRID, lw=0.8, zorder=0)
            ax.annotate(f"{r} kt", (0, r), fontsize=7, color=INK2, ha="center", va="bottom")
        for deg in (30, 40, 50, 60):
            for sign in (1, -1):
                a = math.radians(deg)
                ax.plot(
                    [0, sign * 7.2 * math.sin(a)],
                    [0, 7.2 * math.cos(a)],
                    color=GRID,
                    lw=0.8,
                    zorder=0,
                )
            ax.annotate(
                f"{deg}°",
                (7.3 * math.sin(math.radians(deg)), 7.3 * math.cos(math.radians(deg))),
                fontsize=7,
                color=INK2,
            )
        ax.set_aspect("equal")
        ax.set_xlim(-7.2, 7.2)
        ax.set_ylim(2.5, 7.6)
        ax.grid(False)
        ax.set_xticks([])
        ax.set_ylabel("upwind VMG (kt)")
        ax.set_title(f"Leg {lg['leg']}: port ← wind angle → starboard")
        ax.legend(loc="lower center", fontsize=8, ncol=2)
    fig.suptitle(
        f"{res['race']}: upwind polars (◆ = card targets)",
        x=0.01,
        ha="left",
        fontweight="bold",
        fontsize=13,
    )
    fig.tight_layout()
    fig.savefig(out / "polar.png", dpi=130)
    plt.close(fig)


def roundings_plot(race: Race, res: dict, out: Path, plt):
    """Speed through every rounding, aligned on the rounding: windward left, leeward right."""
    rs = res.get("roundings") or []
    if not rs:
        return
    df = race.df
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), sharey=True)
    for ax, kind in zip(axes, ("windward", "leeward"), strict=True):
        mine = [r for r in rs if r["type"] == kind]
        for k, r in enumerate(mine):
            rel = df.tg - r["time_s"]
            g = df[(rel >= -60) & (rel <= 90)]
            ax.plot(
                rel[g.index],
                g.SOG.rolling(3, center=True, min_periods=1).mean(),
                color=(BLUE, ORANGE, AQUA)[k % 3],
                lw=1.8,
                label=f"#{r['n']} at {_fmt_mmss(r['time_s'])}: {r['metres_lost']} m lost",
            )
        ax.axvline(0, color=INK, lw=1, ls="--")
        ax.set_title(f"{kind.capitalize()} roundings")
        ax.set_xlabel("seconds from rounding")
        if mine:
            ax.legend(fontsize=8, loc="lower right")
    axes[0].set_ylabel("SOG (kt)")
    fig.suptitle(
        f"{res['race']}: speed through the roundings",
        x=0.01,
        ha="left",
        fontweight="bold",
        fontsize=13,
    )
    fig.tight_layout()
    fig.savefig(out / "roundings.png", dpi=130)
    plt.close(fig)


def plt_colorbar(ax, mappable):
    cb = ax.figure.colorbar(mappable, ax=ax, shrink=0.8, pad=0.015, fraction=0.03)
    cb.ax.tick_params(labelsize=8)
    return cb


def plot_data(race: Race, res: dict) -> dict:
    """1 Hz series for the interactive HTML charts, in metres with upwind up.

    Origin is the start line's middle (else the first point). Values are rounded to what the
    charts show, and missing values are null.
    """
    df = race.df
    line = next((c for c in race.course if c["type"] == "StartLine"), None)
    if line:
        lat0 = (line["coord1"]["lat"] + line["coord2"]["lat"]) / 2
        lon0 = (line["coord1"]["lon"] + line["coord2"]["lon"]) / 2
    else:
        lat0, lon0 = float(df.Lat.iloc[0]), float(df.Lon.iloc[0])
    rot = math.radians(res["upwind_axis"] or 0)

    def xy(lat, lon):
        x, y = local_xy(lat, lon, lat0, lon0)
        return x * math.cos(rot) - y * math.sin(rot), x * math.sin(rot) + y * math.cos(rot)

    def col(values, nd):
        return [
            None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), nd)
            for v in values
        ]

    x, y = xy(df.Lat.values, df.Lon.values)
    hdg = df.Heading if "Heading" in df else df.COG
    if res.get("mag_var") is not None:
        hdg = (hdg - res["mag_var"]) % 360  # shown magnetic
    leg_of = pd.Series(np.nan, index=df.index)
    for lg in res["legs"]:
        t0 = race.gun + pd.Timedelta(seconds=lg["start_s"])
        leg_of[(df.t >= t0) & (df.t <= t0 + pd.Timedelta(seconds=lg["duration_s"]))] = lg["leg"]
    series = {
        "t": col(df.tg, 0),
        "x": col(x, 1),
        "y": col(y, 1),
        "sog": col(df.SOG, 2),
        # speed toward the target: VMC to the next mark (VMG along the wind when there are no marks)
        "vmg": col(df[res.get("progress") or "vmg_wind"], 2),
        "hdg": col(hdg, 0),
        "heel": col(df.Heel, 1) if "Heel" in df else None,
        "twa": col(df.twa_gps, 0),
        "shift": col(df.shift_s, 1) if "shift_s" in df else None,
        "below": col(df.BelowLineCalc, 1) if "BelowLineCalc" in df else None,
        "leg": [None if math.isnan(v) else int(v) for v in leg_of],
        "tack": [{"stbd": "s", "port": "p"}.get(v) for v in df.tack],
        "steady": [1 if v else 0 for v in df.steady],
    }
    course = []
    for c in race.course:
        pts = [xy(c[k]["lat"], c[k]["lon"]) for k in ("coord1", "coord2") if c.get(k)]
        course.append({"type": c["type"], "pts": [[round(a, 1), round(b, 1)] for a, b in pts]})
    # Laylines and rungs for each leg, from this boat's own tracks (ladder.py)
    ladders = []
    tg_els = [c for c in course if c["type"] in ("Mark", "Gate", "FinishLine")]
    types = [lg["type"] for lg in res["legs"]]
    if tg_els and len(tg_els) == len(types):
        sog = df.SOG.to_numpy()
        dirs = []
        for lg in res["legs"]:
            on = (leg_of == lg["leg"]).to_numpy() & (df.tg.to_numpy() >= lg["start_s"] + 20)
            dirs.append(ladder.track_dirs(x[on], y[on], sog[on]))
        mids = lambda c: [float(np.mean([p[0] for p in c["pts"]])), float(np.mean([p[1] for p in c["pts"]]))]
        start = next((c for c in course if c["type"] == "StartLine"), None)
        origins = [mids(start) if start else [0.0, 0.0]] + [mids(c) for c in tg_els[:-1]]
        ladders = ladder.build(types, dirs, [c["pts"] for c in tg_els], origins)
    calls = {c["time_s"]: c for c in (res.get("shifts") or {}).get("tacks", [])}
    mans = [
        {
            k: m.get(k)
            for k in (
                "time_s",
                "kind",
                "onto",
                "leg",
                "entry_sog",
                "min_sog",
                "speed_loss_pct",
                "recovery_s",
                "distance_lost_m",
                "note",
            )
        }
        | {
            "call": (calls.get(m["time_s"]) or {}).get("verdict"),
            "kind_call": (calls.get(m["time_s"]) or {}).get("verdict_kind"),
        }
        for m in res["maneuvers"]
    ]
    return {
        "race": res["race"],
        "progress": "VMC" if res.get("progress") == "vmc" else "VMG",
        "north": north(res),  # the heading series is magnetic when the variation is known
        "series": series,
        "course": course,
        "legs": [
            {k: lg[k] for k in ("leg", "type", "start_s", "duration_s")} for lg in res["legs"]
        ],
        "maneuvers": mans,
        "ladders": ladders,
        "beats": [
            {k: b[k] for k in ("leg", "twd_median", "trend_deg", "pattern")}
            for b in (res.get("shifts") or {}).get("beats", [])
        ],
        "roundings": res.get("roundings") or [],
        "targets": [
            {k: b[k] for k in ("wind", "speed_tgt", "twa_tgt", "heel_tgt")}
            for b in res["targets"].get("bands", [])
        ]
        if res["targets"].get("available")
        else [],
    }


def make_plots(race: Race, res: dict, out: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    _style(plt)
    df = race.df
    race_df = df[df.leg_type.notna()] if df.leg_type.notna().any() else df
    mans = res["maneuvers"]

    # Track, coloured by speed (sequential blue), rotated so upwind is up
    lat0, lon0 = race_df.Lat.iloc[0], race_df.Lon.iloc[0]
    rot = math.radians(res["upwind_axis"] or 0)

    def xy(lat, lon):
        x, y = local_xy(lat, lon, lat0, lon0)
        return x * math.cos(rot) - y * math.sin(rot), x * math.sin(rot) + y * math.cos(rot)

    x, y = xy(race_df.Lat.values, race_df.Lon.values)
    aspect = (np.ptp(x) + 100) / (np.ptp(y) + 100)
    fig, ax = plt.subplots(figsize=(min(10, max(4.5, 7 * aspect + 2)), 7.5))
    pts = np.column_stack([x, y]).reshape(-1, 1, 2)
    lc = LineCollection(np.concatenate([pts[:-1], pts[1:]], axis=1), cmap=_blues(), linewidths=2)
    lc.set_array(race_df.SOG.values[1:])
    lc.set_clim(np.nanpercentile(race_df.SOG, 5), np.nanpercentile(race_df.SOG, 95))
    ax.add_collection(lc)
    fig.colorbar(lc, ax=ax, shrink=0.6, label="SOG (kt)")
    for m in mans:
        p = _interp(df, "tg", m["time_s"])
        if p:
            mx, my = xy(p["Lat"], p["Lon"])
            ax.plot(
                mx, my, "o", ms=5, mfc="none", mec=ORANGE if m["kind"] == "Gybe" else INK2, mew=1.2
            )
    for c in race.course:
        cx, cy = xy(c["coord1"]["lat"], c["coord1"]["lon"])
        if c.get("coord2"):
            dx, dy = xy(c["coord2"]["lat"], c["coord2"]["lon"])
            ax.plot([cx, dx], [cy, dy], color=INK, lw=2)
            # Start below its middle, gate off its left end, finish off its right end:
            # these three often sit on top of each other at the leeward end.
            label = c["type"].replace("Line", " line")
            if c["type"] == "StartLine":
                pos, off, ha = ((cx + dx) / 2, (cy + dy) / 2), (0, -14), "center"
            elif c["type"] == "Gate":
                pos, off, ha = min((cx, cy), (dx, dy)), (-6, -3), "right"
            else:
                pos, off, ha = max((cx, cy), (dx, dy)), (6, -3), "left"
            ax.annotate(
                label, pos, xytext=off, textcoords="offset points", ha=ha, color=INK2, fontsize=9
            )
        else:
            ax.plot(cx, cy, "^", color=INK, ms=9)
    ax.set_aspect("equal")
    ax.autoscale()
    ax.set_xlabel("metres (upwind is up)")
    ax.set_title(f"{res['race']}: track")
    ax.text(
        0,
        -0.1,
        "grey circles = tacks, orange = gybes",
        transform=ax.transAxes,
        color=INK2,
        fontsize=9,
    )
    fig.tight_layout()
    fig.savefig(out / "track.png", dpi=130)
    plt.close(fig)

    # Timeline: SOG and heel as two panels sharing time (never dual-axis)
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 5.5), sharex=True, height_ratios=[3, 2])
    m = race_df.tg / 60
    a1.plot(m, race_df.SOG.rolling(5, center=True, min_periods=1).mean(), color=BLUE, lw=1.5)
    a1.set_ylabel("SOG (kt, 5 s avg)")
    a1.set_title(f"{res['race']}: speed and heel")
    if "Heel" in race_df:
        a2.plot(
            m, race_df.Heel.abs().rolling(5, center=True, min_periods=1).mean(), color=BLUE, lw=1.5
        )
        a2.set_ylabel("|heel| (°, 5 s avg)")
    for lg in res["legs"]:
        x0 = lg["start_s"] / 60
        for a in (a1, a2):
            if lg["type"] == "upwind":
                a.axvspan(x0, x0 + lg["duration_s"] / 60, color="#f0efec", zorder=0, lw=0)
        a1.annotate(
            f"Leg {lg['leg']} {lg['type']}",
            (x0, 1),
            xycoords=("data", "axes fraction"),
            xytext=(3, -12),
            textcoords="offset points",
            color=INK2,
            fontsize=9,
        )
    for mm in mans:
        a1.axvline(mm["time_s"] / 60, color=GRID, lw=0.8, zorder=0)
    a2.set_xlabel("minutes from gun (shaded = upwind legs, lines = maneuvers)")
    fig.tight_layout()
    fig.savefig(out / "timeline.png", dpi=130)
    plt.close(fig)

    # Start: track from -5 min (left) beside SOG and distance-to-line from -2 min (right)
    pre = df[(df.tg >= -120) & (df.tg <= 30)]
    if len(pre) and "BelowLineCalc" in pre:
        fig = plt.figure(figsize=(10, 10.5))
        gs = fig.add_gridspec(3, 1, height_ratios=[1.5, 1, 1], hspace=0.28)
        start_track(fig.add_subplot(gs[0]), race, res, df)
        a1 = fig.add_subplot(gs[1])
        a2 = fig.add_subplot(gs[2], sharex=a1)
        a1.plot(pre.tg, pre.SOG, color=BLUE, lw=2)
        a1.set_ylabel("SOG (kt)")
        a1.set_title("Last 2 minutes: speed and distance behind the line")
        a1.tick_params(labelbottom=False)
        a2.plot(pre.tg, pre.BelowLineCalc, color=BLUE, lw=2)
        a2.axhline(0, color=INK, lw=1)
        a2.set_ylabel("m behind line")
        a2.set_xlabel("seconds from gun")
        for a in (a1, a2):
            a.axvline(0, color=INK, lw=1, ls="--")
        fig.suptitle(f"{res['race']}: start", x=0.01, ha="left", fontweight="bold", fontsize=13)
        fig.subplots_adjust(left=0.09, right=0.97, top=0.94, bottom=0.06)
        fig.savefig(out / "start.png", dpi=130)
        plt.close(fig)

    shifts_plot(race, res, out, plt)
    downwind_plot(race, res, out, plt)
    polar_plot(race, res, out, plt)
    roundings_plot(race, res, out, plt)

    # Maneuvers: distance lost per tack/gybe, in race order
    rs = [r for r in mans if r["distance_lost_m"] is not None and not r["note"]]
    if rs:
        fig, ax = plt.subplots(figsize=(10, 3.8))
        colors = [ORANGE if r["kind"] == "Gybe" else BLUE for r in rs]
        ax.bar(range(len(rs)), [r["distance_lost_m"] for r in rs], color=colors, width=0.7)
        ax.set_xticks(range(len(rs)), [_fmt_mmss(r["time_s"]) for r in rs], rotation=60, fontsize=8)
        ax.set_ylabel("metres lost toward the mark")
        ax.set_xlabel("time from gun")
        ax.set_title(f"{res['race']}: distance lost per maneuver (blue = tack, orange = gybe)")
        ax.axhline(0, color=INK, lw=0.8)
        ax.grid(axis="x", visible=False)
        fig.tight_layout()
        fig.savefig(out / "maneuvers.png", dpi=130)
        plt.close(fig)


# ---------------------------------------------------------------- output


def md_table(rows, cols):
    if not rows:
        return "_none_\n"
    head = "| " + " | ".join(h for h, _ in cols) + " |\n|" + "---|" * len(cols) + "\n"
    body = ""
    for r in rows:
        cells = []
        for _, k in cols:
            v = k(r) if callable(k) else r.get(k)
            cells.append("–" if v is None else str(v))
        body += "| " + " | ".join(cells) + " |\n"
    return head + body


CALL_LABEL = {
    "header": "on a header",
    "lift": "on a lift",
    "no_shift": "no clear shift (< 3°)",
    "layline": "layline",
    "mark": "at a mark",
    "start": "off the start",
    "double": "double tack",
}


def shifts_md(sh: dict | None, res: dict | None = None) -> list[str]:
    if not sh or not sh["beats"]:
        return []
    out = [
        "## Wind shifts and tack calls",
        (
            "Wind direction from headings: heading ± half the tacking angle. Shifts are relative "
            "to each beat's median; + is a right shift (veer), − a left shift (back). A puff that "
            "lets the boat point higher also reads as a lift, so treat single 3–4° calls as soft."
        ),
        "",
        md_table(
            sh["beats"],
            [
                ("Leg", "leg"),
                (f"Median wind ({north(res)})", lambda r: f"{mag(r['twd_median'], res):.0f}°"),
                ("Trend over beat", lambda r: f"{r['trend_deg']:+.0f}°"),
                ("Oscillation", lambda r: f"±{r['oscillation_deg']:.0f}°"),
                ("Pattern", "pattern"),
                ("% right of rhumb", "pct_time_right"),
                ("Max left/right m", lambda r: f"{r.get('max_left_m')}/{r.get('max_right_m')}"),
                ("Headed > 5° without tacking", lambda r: f"{r['missed_header_s']} s"),
            ],
        ),
    ]
    out += [f"- Leg {b['leg']}: {b['side_note']}" for b in sh["beats"] if b["side_note"]]
    for b in sh["beats"]:
        for m in b["missed_headers"]:
            out.append(
                f"- Leg {b['leg']}: sailed {m['duration_s']} s on {m['tack']} headed ~{m['avg_header_deg']:.0f}° "
                f"from {_fmt_mmss(m['start_s'])} without tacking."
            )
    counts = ", ".join(f"{n} {CALL_LABEL.get(k, k)}" for k, n in sh["summary"].items())
    out += [
        "",
        f"Tacks: {counts}.",
        "",
        md_table(
            sh["tacks"],
            [
                ("Time", lambda r: _fmt_mmss(r["time_s"])),
                ("Leg", "leg"),
                ("Onto", "onto"),
                ("Headed before", lambda r: _signed(r["headed_before_deg"])),
                ("New tack after", lambda r: _signed(r["lifted_after_deg"])),
                ("Call", "verdict"),
            ],
        ),
        (
            "Headed before: + means the old tack was headed (a good time to tack). "
            "New tack after: + means the new tack was lifted over the next minute."
        ),
        "",
    ]
    return out


def roundings_md(rs: list | None) -> list[str]:
    if not rs:
        return []
    return (
        [
            "## Mark roundings",
            (
                "Metres lost toward the marks: VMC (speed toward this mark, then the next) from 30 s "
                "before to 60 s after the rounding against the steady VMC of the leg before and after. "
                "Settled: 10 s VMC back to 90% of the next leg's."
                + (
                    " With an offset mark the window runs to 60 s after the offset, and the exit "
                    "angle, exit speed and settle time are measured from the offset (the offset "
                    "reach is the same for every boat)."
                    if any(r.get("offset_s") is not None for r in rs)
                    else ""
                )
            ),
            "",
            md_table(
                rs,
                [
                    ("#", "n"),
                    ("Type", "type"),
                    ("From gun", lambda r: _fmt_mmss(r["time_s"])),
                    ("Approach", _approach),
                    ("SOG in", "sog_entry"),
                    ("SOG min", "sog_min"),
                    ("SOG out", "sog_exit"),
                    ("Settled s", "settle_s"),
                    ("m lost", "metres_lost"),
                    ("Closest to mark m", "mark_dist_m"),
                    ("Gate", "gate_side"),
                    ("vs best m", "vs_best_m"),
                ],
            ),
            (
                "vs best: metres more than the best rounding of the same type in this analysis. "
                "The goal is zero lost; the best is the next milestone."
            ),
            "",
        ]
        + [
            f"- #{r['n']} {r['type']}: " + " ".join(r.get("tips") or ["(no suggestions)"])
            for r in rs
        ]
        + [""]
    )


def _approach(r: dict) -> str | None:
    if r["type"] == "windward":
        s = r.get("last_tack_before_s")
        if s is None:
            return None
        over = r.get("overstand_deg")
        tail = f", overstood {over:.0f}°" if over is not None and over > 5 else ""
        return f"layline tack {s} s out{tail}"
    s = r.get("last_gybe_before_s")
    return f"last gybe {s} s out" if s is not None else "no gybe on the run"


def _signed(v):
    return None if v is None else f"{v:+.0f}°"


def write_report(res: dict, out: Path):
    s = res["start"] or {}
    w = res["wind"]
    lines = [
        f"# {res.get('boat') or ''} {res.get('event') or ''} {res['race']}".strip(),
        "",
        (
            f"Gun {res['gun_local']} ({res['timezone']}), race {res['duration_min']} min. "
            f"Speed source: {res['speed_source']}."
        ),
        "",
        "## Data quality",
        f"- Wind: {'trusted' if w['trusted'] else 'NOT trusted'} ({w['reason']}).",
        f"- Wind direction estimated from tacking headings: {mag(w.get('twd_estimated'), res)}° {north(res)}."
        if w.get("twd_estimated") is not None
        else "- Wind direction could not be estimated from tacking headings.",
        "- Speeds are over ground; current moves them. Compare tacks before coaching small differences."
        if res["speed_source"] == "SOG"
        else "",
        "",
        "## Start",
    ]
    if s:
        late = s.get("late_s")
        lines += [
            (
                f"- Distance behind line: {s.get('below_line_-60s_m')} m at -60 s, "
                f"{s.get('below_line_-30s_m')} m at -30 s, {s.get('below_line_-10s_m')} m at -10 s, "
                f"{s.get('below_line_+0s_m')} m at the gun."
            ),
            f"- Over the line at the gun by {s['ocs_at_gun_m']} m; back behind it at "
            f"+{s['ocs_returned_s']:.0f} s and restarted at +{s['late_s']:.0f} s."
            if s.get("ocs_returned_s") is not None and s.get("late_s") is not None
            else f"- OCS at gun by {s['ocs_at_gun_m']} m (check the GPS antenna-to-bow offset)."
            if s.get("ocs_at_gun_m")
            else f"- On the line at the gun (within {OCS_TOLERANCE_M:g} m)."
            if late == 0
            else f"- Crossed the line {late} s after the gun (time-on-distance estimate "
            f"{s.get('late_tod_estimate_s')} s at gun speed).",
            f"- SOG: {s.get('sog_-30s')} kt at -30 s, {s.get('sog_-10s')} at -10 s, "
            f"{s.get('sog_+0s')} at the gun, {s.get('sog_+10s')} at +10 s, {s.get('sog_+30s')} at +30 s. "
            f"Acceleration ±5 s: {s.get('accel_pm5s_kt'):+} kt."
            if s.get("accel_pm5s_kt") is not None
            else "",
            f"- Line position: {s.get('line_pos_pct_from_pin')}% from the pin ({s.get('line_pos_label')}), "
            f"line {s.get('line_length_m')} m long."
            if s.get("line_pos_pct_from_pin") is not None
            else "",
            f"- {s.get('pct_last_60s_on_stbd')}% of the last minute on starboard."
            if s.get("pct_last_60s_on_stbd") is not None
            else "",
            "- Recrossed the line backwards within 2 min of the gun."
            if s.get("recrossed_after_gun")
            else "",
        ]
    lines += [
        "",
        "## Legs",
        f"Legs: {res['legs_source']}." if res.get("legs_source") else "",
        (
            "Straight: mark to mark. Ideal: shortest path at our average angle to the wind over "
            "the ground (leeway included) in steady wind; m vs ideal is the extra we sailed."
        ),
        "",
        md_table(
            res["legs"],
            [
                ("Leg", "leg"),
                ("Type", "type"),
                ("Time", lambda r: _fmt_mmss(r["duration_s"])),
                ("VMC to mark", "vmc_avg"),
                ("VMC steady", "vmc_steady"),
                ("Sailed nm", "distance_sailed_nm"),
                ("Straight nm", "straight_nm"),
                ("+% vs straight", "extra_pct"),
                ("m vs ideal", "extra_vs_ideal_m"),
                ("SOG", "sog_avg"),
                ("SOG steady", "sog_steady"),
                ("Maneuvers", "maneuvers"),
                ("Heel (abs)", "heel_abs_avg"),
                ("Heel sd", "heel_abs_std"),
                ("Tacking ∠", "tacking_angle"),
                (f"TWD est ({north(res)[:3]})", lambda r: mag(r.get("twd_est"), res)),
                ("SOG stbd/port", lambda r: f"{r['sog_stbd']}/{r['sog_port']}"),
                ("Heel stbd/port", lambda r: f"{r['heel_stbd']}/{r['heel_port']}"),
                ("% stbd", "pct_time_stbd"),
            ],
        ),
        "## Maneuvers",
    ]
    for kind, v in res["maneuver_summary"].items():
        lines.append(
            f"- {kind}s: {v['count']}, avg entry {v['entry_sog_avg']} kt, avg loss {v['speed_loss_avg_kt']} kt "
            f"({v['speed_loss_avg_pct']}%), avg recovery {v['recovery_avg_s']} s, "
            f"avg {v['distance_lost_avg_m']} m lost (total {v['distance_lost_total_m']} m)"
            + (
                f"; onto port {v['distance_lost_avg_onto_port_m']} m vs onto stbd "
                f"{v['distance_lost_avg_onto_stbd_m']} m"
                if "distance_lost_avg_onto_port_m" in v and "distance_lost_avg_onto_stbd_m" in v
                else ""
            )
            + (
                f"; {v['not_recovered_in_60s']} never got back to 95% within 60 s"
                if v["not_recovered_in_60s"]
                else ""
            )
            + (
                f"; {v['doubles_excluded']} in double tacks/gybes left out of these averages"
                if v["doubles_excluded"]
                else ""
            )
            + "."
        )
    lines += [
        "",
        md_table(
            res["maneuvers"],
            [
                ("Time", lambda r: _fmt_mmss(r["time_s"])),
                ("Kind", "kind"),
                ("Onto", "onto"),
                ("Leg", "leg"),
                ("Entry kt", "entry_sog"),
                ("Min kt", "min_sog"),
                ("Loss %", "speed_loss_pct"),
                ("Recover s", "recovery_s"),
                ("Handling m", "distance_lost_m"),
                ("Call m", lambda r: r.get("call_m")),
                ("Note", "note"),
            ],
        ),
        "",
        "Metres toward the mark. Handling: the dip against a baseline from the VMC before to the VMC "
        "once settled after (what the turn itself cost). Call: what changing onto the new tack's VMC "
        "was worth over the same 30 s (negative = gained, e.g. tacking off a header). They add up to "
        "the total against keeping the VMC it had.",
    ]
    lines += shifts_md(res.get("shifts"), res)
    lines += roundings_md(res.get("roundings"))
    lines += [
        "## Upwind vs. targets",
    ]
    tg = res["targets"]
    if tg["available"]:
        lines += [
            f"TWS: {tg['tws_source']}. TWA: {tg['twa_source']}. Speed: {tg['speed_source']}.",
            "",
            md_table(
                tg["bands"],
                [
                    ("Wind", lambda r: r["wind"] + (" (thin)" if r["thin"] else "")),
                    ("Time s", "seconds"),
                    ("Speed", "speed_avg"),
                    ("Target", "speed_tgt"),
                    ("% target", "speed_pct"),
                    ("Heel (abs)", "heel_avg"),
                    ("Target heel", "heel_tgt"),
                    ("Δ heel", "heel_delta"),
                    ("Heel sd", "heel_std"),
                    ("TWA", "twa_avg"),
                    ("Target TWA", "twa_tgt"),
                    ("Δ TWA", "twa_delta"),
                ],
            ),
        ]
        if tg.get("by_beat"):
            ht = tg.get("by_beat_heel_target")
            lines += [
                "By beat and tack"
                + (f" (heel target {ht}° at the middle of the wind range)" if ht else "")
                + ":",
                "",
                md_table(
                    tg["by_beat"],
                    [
                        ("Leg", "leg"),
                        ("Tack", "tack"),
                        ("Time s", "seconds"),
                        ("Speed", "speed_avg"),
                        ("Heel (abs)", "heel_avg"),
                        ("Heel sd", "heel_std"),
                        ("% time > target + 5°", "pct_heel_over_tgt_plus5"),
                    ],
                ),
            ]
    else:
        lines.append(f"Not computed: {tg['reason']}.")
    lines += ["", "## Plots", "track.png, timeline.png, start.png, maneuvers.png", ""]
    (out / "report.md").write_text("\n".join(x for x in lines if x is not None) + "\n")


def _call_counts(sh: dict | None) -> str | None:
    if not sh or not sh["tacks"]:
        return None
    n = sh["summary"]
    return f"{n.get('header', 0)}/{n.get('lift', 0)}/{n.get('no_shift', 0)}"


def _mid_target(tg: dict) -> dict:
    if not tg["available"] or not tg["bands"]:
        return {}
    b = tg["bands"][len(tg["bands"]) // 2]
    return {"tgt_speed_pct": b["speed_pct"], "tgt_heel_delta": f"{b['heel_delta']:+}"}


def _day(r: dict) -> str:
    return (r.get("gun_local") or "")[:10]


def _day_label(day: str) -> str:
    from datetime import date

    return date.fromisoformat(day).strftime("%a %-d %b") if day else "Undated"


def _up(r):
    return [lg for lg in r["legs"] if lg["type"] == "upwind"]


def _down(r):
    return [lg for lg in r["legs"] if lg["type"] == "downwind"]


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return float(np.mean(xs)) if xs else None


def _rng(xs, fmt="{:.0f}", unit=""):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "–"
    lo, hi = min(xs), max(xs)
    return (
        f"{fmt.format(lo)}{unit}"
        if fmt.format(lo) == fmt.format(hi)
        else f"{fmt.format(lo)}–{fmt.format(hi)}{unit}"
    )


def ocs_label(s: dict) -> str:
    """Over the line at the gun: 'restarted +9 s' if the boat went back, else 'OCS 2.5 m'."""
    if s.get("ocs_returned_s") is not None and s.get("late_s") is not None:
        return f"over {s['ocs_at_gun_m']} m, restarted +{s['late_s']:.0f} s"
    return f"OCS {s['ocs_at_gun_m']} m"


def _start_phrase(s: dict) -> str:
    if not s:
        return "start not measured"
    if s.get("ocs_at_gun_m"):
        when = ocs_label(s)
    elif s.get("late_s") == 0:
        when = "on the line at the gun"
    elif s.get("late_s") is not None:
        when = f"{s['late_s']:.0f} s late"
    else:
        when = "late (not measured)"
    where = s.get("line_pos_label")
    acc = s.get("accel_pm5s_kt")
    acc_txt = (
        f", {'accelerating' if acc >= 0.5 else 'flat' if acc > -0.3 else 'slowing'} ({acc:+.1f} kt)"
        if acc is not None
        else ""
    )
    return f"start {when}{', ' + where if where else ''}{acc_txt}"


def _calls(rs):
    n = {}
    for r in rs:
        for c in (r.get("shifts") or {}).get("tacks", []):
            n[c["verdict_kind"]] = n.get(c["verdict_kind"], 0) + 1
    over = sum(
        1
        for r in rs
        for c in (r.get("shifts") or {}).get("tacks", [])
        if (c.get("overstand_deg") or 0) > 5
    )
    return n, over


def _race_line(r: dict) -> str:
    up, down = _up(r), _down(r)
    parts = [_start_phrase(r["start"] or {}).capitalize()]
    tgt = _mid_target(r["targets"])
    # Progress to the mark first (VMC: each leg's distance to sail over the time it took), then
    # the speed and heel that explain it
    up_vmc, dn_vmc = _mean([lg.get("vmc_avg") for lg in up]), _mean([lg.get("vmc_avg") for lg in down])
    up_sog = _mean([lg["sog_steady"] for lg in up])
    if up_sog is not None:
        heel = _mean([lg["heel_abs_avg"] for lg in up])
        pct = f" ({tgt['tgt_speed_pct']:.0f}% of target)" if tgt else ""
        vmc = f"VMC {up_vmc:.2f} kt to the mark (SOG " if up_vmc is not None else ""
        parts.append(f"upwind {vmc}{up_sog:.2f} kt{pct}{')' if vmc else ''}, heel {heel:.0f}°")
    dn = _mean([lg["sog_steady"] for lg in down])
    if dn is not None:
        vmc = f"VMC {dn_vmc:.2f} kt to the mark (SOG " if dn_vmc is not None else ""
        parts.append(f"downwind {vmc}{dn:.2f} kt{')' if vmc else ''}")
    ms = r["maneuver_summary"].get("Tack")
    if ms:
        n, _ = _calls([r])
        parts.append(
            f"{_n(ms['count'] + ms.get('doubles_excluded', 0), 'tack')} "
            f"({ms['speed_loss_avg_pct']}% speed loss), "
            f"{_n(n.get('header', 0), 'tack')} on a header vs {n.get('lift', 0)} on a lift"
        )
    beats = (r.get("shifts") or {}).get("beats", [])
    if beats:
        parts.append("beats: " + ", ".join(f"{_pattern(b)} (we went {_side(b)})" for b in beats))
    flags = _flags(r)
    return "; ".join(parts) + "." + (f" **Flags:** {'; '.join(flags)}." if flags else "")


def _n(k: int, word: str) -> str:
    return f"{k} {word}{'' if k == 1 else 's'}"


def _pattern(b: dict) -> str:
    p = b["pattern"]
    return (
        f"trending {p.split()[1]} {abs(b['trend_deg']):.0f}°" if p.startswith("persistent") else p
    )


def _side(b: dict) -> str:
    right = b.get("pct_time_right")
    return "?" if right is None else "right" if right > 60 else "left" if right < 40 else "middle"


def _flags(r: dict) -> list[str]:
    """Stand-outs worth a look, by fixed thresholds (no judgment about why)."""
    out = []
    s = r["start"] or {}
    if s.get("ocs_at_gun_m"):
        out.append(ocs_label(s))
    elif (s.get("late_s") or 0) >= 8:
        out.append(f"late start ({s['late_s']:.0f} s)")
    tgt = _mid_target(r["targets"])
    if tgt and tgt["tgt_speed_pct"] < 97:
        out.append(f"upwind speed {tgt['tgt_speed_pct']:.0f}% of target")
    n, over = _calls([r])
    if over:
        out.append(f"{_n(over, 'layline')} overstood")
    if n.get("lift", 0) >= 3 and n.get("lift", 0) > n.get("header", 0):
        out.append(f"{n['lift']} tacks on lifts")
    ms = r["maneuver_summary"].get("Tack")
    if ms and ms["speed_loss_avg_pct"] >= 35:
        out.append(f"deep tacks ({ms['speed_loss_avg_pct']}% loss)")
    for x in r.get("roundings") or []:
        if (x.get("metres_lost") or 0) >= ROUNDING_FLAG_M:
            out.append(f"{x['type']} rounding #{x['n']} lost {x['metres_lost']:.0f} m")
    for b in (r.get("shifts") or {}).get("beats", []):
        wind = b["pattern"].split()[1] if b["pattern"].startswith("persistent") else None
        if wind and _side(b) not in ("middle", wind):
            out.append(f"leg {b['leg']}: wind went {wind}, we went {_side(b)}")
    return out


def _group_line(rs: list[dict]) -> str:
    starts = [r["start"] for r in rs if r["start"]]
    late = [
        s.get("late_s")
        if s.get("ocs_returned_s") is not None
        else 0
        if s.get("ocs_at_gun_m")
        else s.get("late_s")
        for s in starts
    ]
    tg = [_mid_target(r["targets"]) for r in rs]
    pct = [t["tgt_speed_pct"] for t in tg if t]
    heel = [_mean([lg["heel_abs_avg"] for lg in _up(r)]) for r in rs]
    twd = [mag(b["twd_median"], r) for r in rs for b in (r.get("shifts") or {}).get("beats", [])]
    beats = [b for r in rs for b in (r.get("shifts") or {}).get("beats", [])]
    right = sum(1 for b in beats if (b.get("pct_time_right") or 0) > 60)
    trend_left = [b for b in beats if b["pattern"] == "persistent left shift"]
    trend_right = [b for b in beats if b["pattern"] == "persistent right shift"]
    n, over = _calls(rs)
    layl = n.get("layline", 0)
    bits = [_n(len(rs), "race")]
    if twd:
        bits.append(f"wind {_rng(twd, unit='°')} {north(rs[0])}")
    if late:
        bits.append(f"starts {_rng(late)} s late")
    if pct:
        bits.append(f"upwind {_rng(pct)}% of target")
    if any(h is not None for h in heel):
        bits.append(f"heel {_rng(heel, unit='°')}")
    wind_calls = n.get("header", 0) + n.get("lift", 0) + n.get("no_shift", 0)
    if wind_calls:
        bits.append(
            f"{wind_calls} tacks away from the marks: {n.get('header', 0)} on headers, "
            f"{n.get('lift', 0)} on lifts"
        )
    if beats:
        side = f"worked the right on {right} of {len(beats)} beats"
        if trend_left or trend_right:
            side += f" ({_n(len(trend_left), 'beat')} trended left, {len(trend_right)} right)"
        bits.append(side)
    if layl:
        bits.append(f"laylines {layl - over} good / {over} overstood")
    rr = [x for r in rs for x in (r.get("roundings") or []) if x.get("metres_lost") is not None]
    for kind in ("windward", "leeward"):
        k = [x["metres_lost"] for x in rr if x["type"] == kind]
        if k:
            bits.append(
                f"{kind} roundings {np.mean(k):.0f} m lost on average ({_n(len(k), 'rounding')})"
            )
    return "; ".join(bits) + "."


def write_executive(results: list[dict], out: Path):
    """A broad, factual overview: one line for everything, each day, and each race."""
    days = {}
    for r in results:
        days.setdefault(_day(r), []).append(r)
    lines = ["# Executive summary", ""]
    if len(days) > 1:
        lines += [f"**Overall:** {_group_line(results)}", ""]
    for day, rs in days.items():
        lines += [f"## {_day_label(day)}", "", f"**Day:** {_group_line(rs)}", ""]
        for r in rs:
            gun = (r.get("gun_local") or "")[11:16]
            lines.append(f"- **{r['race']}** ({gun}, {r['duration_min']:.0f} min): {_race_line(r)}")
        lines.append("")
    lines.append(
        "*Facts only; the debrief decides what matters. Targets use the wind given with --tws "
        "(or trusted logged wind). Shift calls come from headings.*"
    )
    (out / "executive.md").write_text("\n".join(lines) + "\n")


def write_event(results: list[dict], out: Path):
    rows = []
    for r in results:
        s = r["start"] or {}
        ms = r["maneuver_summary"].get("Tack", {})
        up = [lg for lg in r["legs"] if lg["type"] == "upwind"]
        rows.append(
            {
                "race": r["race"],
                "gun": (r["gun_local"] or "")[11:16],
                "min": r["duration_min"],
                "late": s.get("late_s") if not s.get("ocs_at_gun_m") else ocs_label(s),
                "pos": s.get("line_pos_pct_from_pin"),
                "sog0": s.get("sog_+0s"),
                "accel": s.get("accel_pm5s_kt"),
                "tacks": ms.get("count"),
                "tack_loss": ms.get("distance_lost_avg_m"),
                # progress to the mark (VMC), then the speed that explains it
                "up_vmc": _r(_mean([lg.get("vmc_avg") for lg in up]), 2) if up else None,
                "dn_vmc": _r(_mean([lg.get("vmc_avg") for lg in r["legs"] if lg["type"] == "downwind"]), 2),
                "up_sog": round(np.mean([lg["sog_steady"] for lg in up if lg["sog_steady"]]), 2)
                if up
                else None,
                "up_heel": round(
                    np.mean([lg["heel_abs_avg"] for lg in up if lg["heel_abs_avg"]]), 1
                )
                if up
                else None,
                **_mid_target(r["targets"]),
                "calls": _call_counts(r.get("shifts")),
                "tack_angle": round(
                    np.mean([lg["tacking_angle"] for lg in up if lg["tacking_angle"]]), 1
                )
                if any(lg["tacking_angle"] for lg in up)
                else None,
            }
        )
    text = "# Event summary\n\n" + md_table(
        rows,
        [
            ("Race", "race"),
            ("Gun", "gun"),
            ("Min", "min"),
            ("Late s", "late"),
            ("Line pos % from pin", "pos"),
            ("SOG at gun", "sog0"),
            ("Accel ±5 s", "accel"),
            ("Upwind VMC to mark", "up_vmc"),
            ("Downwind VMC to mark", "dn_vmc"),
            ("Tacks", "tacks"),
            ("Avg m lost/tack (to the mark)", "tack_loss"),
            ("Upwind SOG", "up_sog"),
            ("Upwind heel (abs)", "up_heel"),
            ("Tacking ∠", "tack_angle"),
            ("Tacks on header/lift/no shift", "calls"),
        ]
        + (
            [("Speed % target", "tgt_speed_pct"), ("Δ heel", "tgt_heel_delta")]
            if any(r["targets"]["available"] for r in results)
            else []
        ),
    )
    tws = next((r["targets"]["tws_source"] for r in results if r["targets"]["available"]), None)
    if tws:
        text += (
            f"\nTargets at the middle of the wind range ({tws}); per-race detail has the range.\n"
        )
    text += "\nPer-race detail: " + ", ".join(f"{r['stem']}/report.md" for r in results) + "\n"
    (out / "event.md").write_text(text)


def run(
    csvs: list[Path], out: Path, tws: float | None, tz: str | None, plots: bool = True
) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    analysed = []
    for p in csvs:
        race = load_race(p, tz)
        analysed.append((race, analyze_race(race, tws)))
    compare_roundings([res for _, res in analysed])
    results = []
    for race, res in analysed:
        d = out / race.stem
        d.mkdir(exist_ok=True)
        (d / "summary.json").write_text(json.dumps(res, indent=2, default=str))
        pd.DataFrame(res["legs"]).to_csv(d / "legs.csv", index=False)
        pd.DataFrame(res["maneuvers"]).to_csv(d / "maneuvers.csv", index=False)
        if res["targets"]["available"]:
            pd.DataFrame(res["targets"]["bands"]).to_csv(d / "targets.csv", index=False)
        write_report(res, d)
        (d / "plotdata.json").write_text(json.dumps(plot_data(race, res), separators=(",", ":")))
        boat = race.meta.get("boat") or out.name
        overlay = maneuver_overlay.race_overlay(race.df, res["maneuvers"], boat, res["race"])
        (d / "overlay.json").write_text(json.dumps(overlay, separators=(",", ":")))
        drift = current.race_drift(race.df, res, race.course, boat)
        # For lining the race up with tide and current data (noaa.py)
        drift["gun"] = race.gun.timestamp() if race.gun is not None else None
        drift["timezone"] = str(race.tz)
        (d / "drift.json").write_text(json.dumps(drift, separators=(",", ":")))
        if plots:
            make_plots(race, res, d)
        results.append(res)
    write_event(results, out)
    write_executive(results, out)
    return results


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("csv", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--tws",
        help="true wind speed (kt) when the log's wind is unreliable: 9, or a range like 8-10",
    )
    ap.add_argument(
        "--tz", help="IANA timezone for displayed times (default: from <stem>-race.json, else UTC)"
    )
    ap.add_argument("--no-plots", action="store_true")
    ap.add_argument("--html", action="store_true", help="also write <out>/report.html")
    ap.add_argument("--debrief", type=Path, help="Markdown debrief to put at the top of the HTML")
    ap.add_argument(
        "--cdn", action="store_true", help="HTML loads Plotly online (smaller file, needs internet)"
    )
    a = ap.parse_args()
    run(a.csv, a.out, a.tws, a.tz, plots=not a.no_plots)
    print((a.out / "event.md").read_text())
    if a.html or a.debrief or a.cdn:
        from html_report import write_html

        print(f"HTML report: {write_html(a.out, a.debrief, cdn=a.cdn)}")


if __name__ == "__main__":
    main()
