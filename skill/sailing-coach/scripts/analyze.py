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
  track.png, timeline.png, start.png, maneuvers.png
and <out>/event.md comparing all races.

Numbers only. No coaching judgments here; that's the debrief's job.
"""

from __future__ import annotations

import argparse
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
        gun = pd.Timestamp(meta["startTime"]).floor("s")
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
    # Fallback: heading swing > 70 deg within 15 s, 30 s debounce
    df = race.df
    out, last = [], None
    hdg = df.Heading if "Heading" in df else df.COG
    for i in range(15, len(df)):
        if df.SOG[i] < 1.5 or pd.isna(hdg[i]) or pd.isna(hdg[i - 15]):
            continue
        if abs(float(adiff(hdg[i - 15], hdg[i]))) > 70 and (
            last is None or (df.t[i] - last).total_seconds() > 30
        ):
            last = df.t[i - 7]
            out.append({"t": last, "kind": "Maneuver", "onto": None})
    return out


def analyze_race(race: Race, tws_override: float | None) -> dict:
    df = race.df
    gun = race.gun
    df["tg"] = (df.t - gun).dt.total_seconds() if gun is not None else np.nan
    has_leg = "Leg" in df and df.Leg.notna().any()
    axis = upwind_axis(race)

    # --- legs
    legs = []
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

    leg_rows = [leg_stats(df, lg, hdg_col, mans) for lg in legs]
    man_rows = [maneuver_stats(df, m, hdg_col) for m in mans]
    for a, b in pairwise(man_rows):
        if b["time_s"] - a["time_s"] < 30:
            a["note"] = b["note"] = "double (<30 s apart; loss numbers overlap)"
    start = start_stats(race, df, axis) if gun is not None else None
    targets = target_bands(df, wind, tws_override)

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
        "wind": wind,
        "start": start,
        "legs": leg_rows,
        "maneuvers": man_rows,
        "maneuver_summary": maneuver_summary(man_rows),
        "targets": targets,
    }


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


def leg_stats(df, lg, hdg_col, mans):
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
        "sog_avg": round(float(g.SOG.mean()), 2),
        "sog_steady": round(float(s.SOG.mean()), 2) if len(s) else None,
        "vmc_avg": round(float(g.VMC.mean()), 2) if "VMC" in g and g.VMC.notna().any() else None,
        "maneuvers": sum(lg["start"] <= m["t"] <= lg["end"] for m in mans),
        "heel_abs_avg": round(float(heel.mean()), 1) if len(heel) else None,
        "heel_abs_std": round(float(heel.std()), 1) if len(heel) > 1 else None,
        "trim_avg": round(float(s.Trim.mean()), 1) if "Trim" in s and len(s) else None,
        "tacking_angle": lg.get("tacking_angle"),
        "twd_est": round(lg["twd"], 1) if lg.get("twd") is not None else None,
    }
    for side in ("stbd", "port"):
        t = s[s.tack == side]
        row[f"pct_time_{side}"] = round(100 * len(t) / len(s)) if len(s) else None
        row[f"sog_{side}"] = round(float(t.SOG.mean()), 2) if len(t) >= 20 else None
        row[f"heel_{side}"] = (
            round(float(t.Heel.abs().mean()), 1) if len(t) >= 20 and "Heel" in t else None
        )
    return row


def maneuver_stats(df, m, hdg_col):
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
    base = w(-15, -5).vmg_wind.mean()
    span = w(-5, 25).vmg_wind
    if pd.notna(base) and span.notna().sum() > 20:
        row["distance_lost_m"] = round(float(((base - span) * KT_TO_MS).sum()), 1)
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
        if below is not None and below < -OCS_TOLERANCE_M:
            out["ocs_at_gun_m"] = round(-below, 1)
        on_line = below is not None and below <= OCS_TOLERANCE_M
        out["late_s"] = (
            0.0 if on_line else round(float(crossed.tg.iloc[0]), 1) if len(crossed) else None
        )
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


def target_bands(df, wind, tws_override):
    """Upwind steady state vs. the Etchells card. Skipped unless wind speed is trustworthy."""
    up = df[(df.leg_type == "upwind") & df.steady & df.twa_gps.notna()].copy()
    if tws_override is not None:
        up["tws_used"] = tws_override
        source = f"user-supplied TWS {tws_override:g} kt"
    elif wind["trusted"]:
        up["tws_used"] = up.TWS
        source = "logged TWS"
    else:
        return {
            "available": False,
            "reason": f"wind speed not trustworthy ({wind['reason']}); pass --tws",
        }
    up = up[up.twa_gps <= 60]
    if not len(up):
        return {"available": False, "reason": "no steady upwind data"}
    t = interp_targets(up.tws_used)
    spd = "BoatSpeed" if "BoatSpeed" in up else "SOG"
    up["spd_pct"] = 100 * up[spd] / t["Vb"]
    up["twa_delta"] = up.twa_gps - t["TWA"]
    up["heel_abs"] = up.Heel.abs()
    up["heel_delta"] = up.heel_abs - t["Heel"]
    rows = []
    for lo, hi in BANDS:
        b = up[(up.tws_used >= lo) & (up.tws_used < hi)]
        if not len(b):
            continue
        rows.append(
            {
                "band": f"{lo}-{hi}" if hi < 40 else f"{lo}+",
                "seconds": len(b),
                "thin": len(b) < 60,
                "speed_avg": round(float(b[spd].mean()), 2),
                "speed_pct": round(float(b.spd_pct.mean()), 1),
                "twa_avg": round(float(b.twa_gps.mean()), 1),
                "twa_delta": round(float(b.twa_delta.mean()), 1),
                "heel_avg": round(float(b.heel_abs.mean()), 1),
                "heel_delta": round(float(b.heel_delta.mean()), 1),
                "heel_std": round(float(b.heel_abs.std()), 1),
            }
        )
    return {
        "available": True,
        "tws_source": source,
        "twa_source": "logged TWD" if wind["trusted"] else "estimated from GPS tacking headings",
        "speed_source": spd,
        "bands": rows,
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

    # Start: last 2 min to +30 s, SOG and distance behind the line
    pre = df[(df.tg >= -120) & (df.tg <= 30)]
    if len(pre) and "BelowLineCalc" in pre:
        fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 5), sharex=True)
        a1.plot(pre.tg, pre.SOG, color=BLUE, lw=2)
        a1.set_ylabel("SOG (kt)")
        a1.set_title(f"{res['race']}: start")
        a2.plot(pre.tg, pre.BelowLineCalc, color=BLUE, lw=2)
        a2.axhline(0, color=INK, lw=1)
        a2.set_ylabel("m behind line")
        a2.set_xlabel("seconds from gun")
        for a in (a1, a2):
            a.axvline(0, color=INK, lw=1, ls="--")
        fig.tight_layout()
        fig.savefig(out / "start.png", dpi=130)
        plt.close(fig)

    # Maneuvers: distance lost per tack/gybe, in race order
    rs = [r for r in mans if r["distance_lost_m"] is not None and not r["note"]]
    if rs:
        fig, ax = plt.subplots(figsize=(10, 3.8))
        colors = [ORANGE if r["kind"] == "Gybe" else BLUE for r in rs]
        ax.bar(range(len(rs)), [r["distance_lost_m"] for r in rs], color=colors, width=0.7)
        ax.set_xticks(range(len(rs)), [_fmt_mmss(r["time_s"]) for r in rs], rotation=60, fontsize=8)
        ax.set_ylabel("metres lost vs. entry VMG")
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
        f"- Wind direction estimated from tacking headings: {w.get('twd_estimated')}°."
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
            f"- OCS at gun by {s['ocs_at_gun_m']} m (check the GPS antenna-to-bow offset)."
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
        md_table(
            res["legs"],
            [
                ("Leg", "leg"),
                ("Type", "type"),
                ("Time", lambda r: _fmt_mmss(r["duration_s"])),
                ("Dist nm", "distance_sailed_nm"),
                ("SOG", "sog_avg"),
                ("SOG steady", "sog_steady"),
                ("VMC", "vmc_avg"),
                ("Maneuvers", "maneuvers"),
                ("|Heel|", "heel_abs_avg"),
                ("Heel sd", "heel_abs_std"),
                ("Tacking ∠", "tacking_angle"),
                ("TWD est", "twd_est"),
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
                f"; onto port {v.get('distance_lost_avg_onto_port_m')} m vs onto stbd "
                f"{v.get('distance_lost_avg_onto_stbd_m')} m"
                if "distance_lost_avg_onto_port_m" in v
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
                ("Lost m", "distance_lost_m"),
                ("Note", "note"),
            ],
        ),
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
                    ("TWS band", lambda r: r["band"] + (" (thin)" if r["thin"] else "")),
                    ("Time s", "seconds"),
                    ("Speed", "speed_avg"),
                    ("% target", "speed_pct"),
                    ("TWA", "twa_avg"),
                    ("Δ TWA", "twa_delta"),
                    ("|Heel|", "heel_avg"),
                    ("Δ heel", "heel_delta"),
                    ("Heel sd", "heel_std"),
                ],
            ),
        ]
    else:
        lines.append(f"Not computed: {tg['reason']}.")
    lines += ["", "## Plots", "track.png, timeline.png, start.png, maneuvers.png", ""]
    (out / "report.md").write_text("\n".join(x for x in lines if x is not None) + "\n")


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
                "late": s.get("late_s")
                if not s.get("ocs_at_gun_m")
                else f"OCS {s['ocs_at_gun_m']} m",
                "pos": s.get("line_pos_pct_from_pin"),
                "sog0": s.get("sog_+0s"),
                "accel": s.get("accel_pm5s_kt"),
                "tacks": ms.get("count"),
                "tack_loss": ms.get("distance_lost_avg_m"),
                "up_sog": round(np.mean([lg["sog_steady"] for lg in up if lg["sog_steady"]]), 2)
                if up
                else None,
                "up_heel": round(
                    np.mean([lg["heel_abs_avg"] for lg in up if lg["heel_abs_avg"]]), 1
                )
                if up
                else None,
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
            ("Tacks", "tacks"),
            ("Avg m lost/tack", "tack_loss"),
            ("Upwind SOG", "up_sog"),
            ("Upwind |heel|", "up_heel"),
            ("Tacking ∠", "tack_angle"),
        ],
    )
    text += "\nPer-race detail: " + ", ".join(f"{r['stem']}/report.md" for r in results) + "\n"
    (out / "event.md").write_text(text)


def run(
    csvs: list[Path], out: Path, tws: float | None, tz: str | None, plots: bool = True
) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for p in csvs:
        race = load_race(p, tz)
        res = analyze_race(race, tws)
        d = out / race.stem
        d.mkdir(exist_ok=True)
        (d / "summary.json").write_text(json.dumps(res, indent=2, default=str))
        pd.DataFrame(res["legs"]).to_csv(d / "legs.csv", index=False)
        pd.DataFrame(res["maneuvers"]).to_csv(d / "maneuvers.csv", index=False)
        if res["targets"]["available"]:
            pd.DataFrame(res["targets"]["bands"]).to_csv(d / "targets.csv", index=False)
        write_report(res, d)
        if plots:
            make_plots(race, res, d)
        results.append(res)
    write_event(results, out)
    return results


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("csv", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--tws", type=float, help="true wind speed (kt) to use when the log's wind is unreliable"
    )
    ap.add_argument(
        "--tz", help="IANA timezone for displayed times (default: from <stem>-race.json, else UTC)"
    )
    ap.add_argument("--no-plots", action="store_true")
    a = ap.parse_args()
    run(a.csv, a.out, a.tws, a.tz, plots=not a.no_plots)
    print((a.out / "event.md").read_text())


if __name__ == "__main__":
    main()
