#!/usr/bin/env python3
"""Compare boats that sailed the same races: results, gaps at every mark, leg gains and losses,
starts, upwind and downwind speed, sides of the course, and roundings.

Usage:
  python fleet.py --data <dir> --reports <dir> --out <dir> [--html] [--debrief fleet.md]
                  [--title "PCC 2026"] [--cdn]

--data holds one folder per boat with Njord exports (raceN.csv plus raceN-race.json with the
course); --reports holds the matching analyze.py output per boat (same folder names). Races are
matched across boats by file stem (race1, race2, ...). Mark passages come from the course
geometry for every boat (analyze.course_legs), so all boats are split the same way.

Side by side: every stretch where two boats sailed within PAIR_RADIUS_M of each other on the same
leg and tack or gybe, with what each gained from speed and from angle (same breeze, so it's the
boats, not the wind).

Writes <out>/fleet.md and <out>/fleet.json; with --html also <out>/fleet.html (gap charts, a race
replay per race with each boat's numbers beside it, the Side by side page, the fleet debrief, and
each boat's coach's summary from <reports>/<boat>/debrief.md when present).
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import re
from pathlib import Path

import analyze as A
import html_report as H
import numpy as np
import pandas as pd

# Categorical slots in fixed order (light, dark), validated for colour-vision separation.
# Scatter-style charts compare every pair, which only the first three slots pass.
BOAT_COLORS = [
    ("#2a78d6", "#3987e5"),
    ("#eb6834", "#d95926"),
    ("#1baf7a", "#199e70"),
    ("#eda100", "#c98500"),
    ("#e87ba4", "#d55181"),
    ("#008300", "#008300"),
    ("#4a3aa7", "#9085e9"),
    ("#e34948", "#e66767"),
]
SERIES_STEP_S = 2  # track overlay and replay resolution
# Side by side: two boats within this distance on the same leg and the same tack or gybe
PAIR_RADIUS_M = 200
PAIR_MIN_S = 60  # shortest stretch worth comparing
PAIR_SETTLE_S = 20  # skip this long after a mark or a tack/gybe (either boat)
KT = 1852 / 3600  # m/s per knot


# ---------------------------------------------------------------- loading


def load_fleet(data_dir: Path, reports_dir: Path, tz: str | None = None) -> list[dict]:
    boats = []
    for d in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        races = {}
        for csv in sorted(d.glob("race*.csv")):
            summ = reports_dir / d.name / csv.stem / "summary.json"
            if not summ.exists():
                continue
            races[csv.stem] = {
                "race": A.load_race(csv, tz),
                "summary": json.loads(summ.read_text()),
            }
        if races:
            name = next(iter(races.values()))["summary"].get("boat") or d.name
            boats.append({"id": d.name, "name": name, "races": races})
    for i, b in enumerate(boats):
        b["color"], b["color_dark"] = BOAT_COLORS[i % len(BOAT_COLORS)]
    return boats


# ---------------------------------------------------------------- geometry


def _mid(el: dict) -> tuple[float, float]:
    pts = A._course_points(el)
    return float(np.mean([p[0] for p in pts])), float(np.mean([p[1] for p in pts]))


def frame(race: A.Race):
    """Local metres with the start-line middle at the origin and the upwind axis pointing up."""
    line = next(c for c in race.course if c["type"] == "StartLine")
    lat0, lon0 = _mid(line)
    axis = A.upwind_axis(race) or 0.0
    rot = math.radians(axis)

    def xy(lat, lon):
        x, y = A.local_xy(lat, lon, lat0, lon0)
        # rotate so bearing `axis` becomes north (up)
        return x * math.cos(rot) - y * math.sin(rot), x * math.sin(rot) + y * math.cos(rot)

    return xy


def leg_labels(legs: list[dict]) -> list[str]:
    out, nw, nl = [], 0, 0
    for i, lg in enumerate(legs):
        if i == len(legs) - 1:
            out.append("Finish")
        elif lg["type"] == "upwind":
            nw += 1
            out.append(f"Windward {nw}")
        else:
            nl += 1
            out.append(f"Leeward {nl}")
    return out


def leg_names(types: list[str]) -> list[str]:
    """'Beat 1', 'Run 1', 'Beat 2', ... for leg types in order."""
    out, n = [], {"upwind": 0, "downwind": 0}
    for t in types:
        n[t] += 1
        out.append(f"{'Beat' if t == 'upwind' else 'Run'} {n[t]}")
    return out


def _rounded(g: pd.DataFrame, col: str, nd: int) -> list:
    if col not in g:
        return []
    return [None if pd.isna(v) else round(float(v), nd) for v in g[col]]


def side_of_rhumb(g: pd.DataFrame, a: tuple, b: tuple, xy) -> dict:
    """Signed distance from the rhumb line a->b (right of it, looking at b, is positive)."""
    ax, ay = xy(*a)
    bx, by = xy(*b)
    px, py = xy(g.Lat.to_numpy(), g.Lon.to_numpy())
    L = math.hypot(bx - ax, by - ay) or 1.0
    ux, uy = (bx - ax) / L, (by - ay) / L
    xte = (px - ax) * uy - (py - ay) * ux  # >0 = right of the rhumb looking at the mark
    return {
        "pct_right": round(float((xte > 0).mean() * 100)),
        "mean_xte_m": round(float(np.mean(xte))),
        "max_right_m": round(float(max(np.max(xte), 0))),
        "max_left_m": round(float(max(-np.min(xte), 0))),
    }


def _circ_mean(deg) -> float:
    return float(np.degrees(np.angle(np.mean(np.exp(1j * np.radians(np.asarray(deg)))))))


def tacking_angles(g: pd.DataFrame, leg_start) -> dict:
    """Tacking angle by compass (heading) and over the ground (GPS course) on a beat.

    Tacks are split by heel side (Njord heel is negative on starboard), so a compass offset
    doesn't move a sample to the wrong tack. The heading angle minus the ground angle is the
    slip on both tacks together; the ground angle doesn't depend on the compass at all."""
    if not {"Heading", "COG", "Heel"} <= set(g.columns):
        return {}
    g = g[(g.t >= leg_start + pd.Timedelta(seconds=30)) & (g.SOG > 3)]
    stbd, port = g[g.Heel < -8], g[g.Heel > 8]
    if len(stbd) < 30 or len(port) < 30:
        return {}

    def between(a, b):
        return round(abs((a - b + 180) % 360 - 180), 1)

    ta_hdg = between(_circ_mean(stbd.Heading), _circ_mean(port.Heading))
    ta_cog = between(_circ_mean(stbd.COG), _circ_mean(port.COG))
    return {
        "ta_heading": ta_hdg,
        "ta_cog": ta_cog,
        "slip_per_tack": round((ta_cog - ta_hdg) / 2, 1),
    }


# ---------------------------------------------------------------- side by side


def _per_second(race: A.Race, df: pd.DataFrame, legs: list[dict], xy, summ: dict) -> pd.DataFrame:
    """One row per second from the boat's own start to its finish, in the course frame.

    side: +1 moving right across the course, -1 moving left. With upwind up, moving left is
    starboard tack upwind and starboard gybe downwind (from the track, not the compass)."""
    g = df[df.Lat.notna()].copy()
    g["s"] = (g.t - race.gun).dt.total_seconds().round().astype(int)
    g = g.drop_duplicates("s").set_index("s")
    g["x"], g["y"] = xy(g.Lat.to_numpy(), g.Lon.to_numpy())
    ends = np.array([(lg["end"] - race.gun).total_seconds() for lg in legs])
    starts = np.concatenate([[0.0], ends[:-1]])
    late = (summ.get("start") or {}).get("late_s") or 0.0
    g = g[(g.index >= late) & (g.index <= ends[-1])].copy()
    s = g.index.to_numpy()
    g["leg"] = np.minimum(np.searchsorted(ends, s), len(ends) - 1)
    g["since_leg"] = s - np.maximum(starts[g.leg.to_numpy()], late)
    vx = g.x.diff(10).shift(-5)  # 10 s of track, centred
    side = np.sign(vx).replace(0, np.nan).ffill().bfill()
    g["side"] = side
    change = side.ne(side.shift()).cumsum()
    g["since_side"] = g.groupby(change).cumcount()
    return g


def side_by_side(per: dict[str, pd.DataFrame], leg_types: list[str]) -> list[dict]:
    """Stretches where two boats sailed within PAIR_RADIUS_M of each other on the same leg and
    tack/gybe, and what each gained. Gain is progress along the course axis (up the course on a
    beat, down it on a run): the part from boat speed and the part from sailing a closer angle
    to the axis. Both boats are in the same breeze, so the difference is the boats, not the wind.
    """
    out = []
    names = leg_names(leg_types)
    ids = list(per)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            idx = per[a].index.intersection(per[b].index)
            if not len(idx):
                continue
            pa, pb = per[a].loc[idx], per[b].loc[idx]
            ok = (
                (np.hypot(pa.x - pb.x, pa.y - pb.y) < PAIR_RADIUS_M)
                & (pa.leg == pb.leg)
                & (pa.leg < len(leg_types))
                & (pa.side == pb.side)
                & (pa.since_leg >= PAIR_SETTLE_S)
                & (pb.since_leg >= PAIR_SETTLE_S)
                & (pa.since_side >= PAIR_SETTLE_S)
                & (pb.since_side >= PAIR_SETTLE_S)
            ).to_numpy()
            runs: list[list[int]] = []
            for s in idx[ok]:
                if runs and s - runs[-1][1] <= 5 and pa.side.loc[s] == pa.side.loc[runs[-1][1]]:
                    runs[-1][1] = s
                else:
                    runs.append([s, s])
            for s0, s1 in runs:
                if s1 - s0 >= PAIR_MIN_S:
                    out.append(_pair_row(a, b, pa.loc[s0:s1], pb.loc[s0:s1], leg_types, names))
    out.sort(key=lambda r: r["t0"])
    return out


def _pair_row(a, b, pa, pb, leg_types, names) -> dict:
    leg = int(pa.leg.iloc[0])
    up = leg_types[leg] == "upwind"
    sign = 1 if up else -1  # progress is +y on a beat, -y on a run
    dur = int(pa.index[-1] - pa.index[0])

    def boat(p):
        dx, dy = p.x.iloc[-1] - p.x.iloc[0], p.y.iloc[-1] - p.y.iloc[0]
        made = sign * dy
        return {
            "sog": round(float(p.SOG.mean()), 2),
            "heel": round(float(p.Heel.abs().mean()), 1) if "Heel" in p else None,
            # angle between the track and straight up (beat) or down (run) the course
            "angle": round(math.degrees(math.atan2(abs(dx), sign * dy)), 1),
            "made_m": round(float(made)),
        }

    ra, rb = boat(pa), boat(pb)
    gain = ra["made_m"] - rb["made_m"]
    cos = math.cos(math.radians((ra["angle"] + rb["angle"]) / 2))
    speed = (ra["sog"] - rb["sog"]) * KT * cos * dur

    def rel(k):  # a relative to b: metres ahead along the course sailed, and to windward
        vx = pa.x.iloc[-1] - pa.x.iloc[0] + pb.x.iloc[-1] - pb.x.iloc[0]
        vy = pa.y.iloc[-1] - pa.y.iloc[0] + pb.y.iloc[-1] - pb.y.iloc[0]
        L = math.hypot(vx, vy) or 1.0
        ux, uy = vx / L, vy / L
        nx, ny = (-uy, ux) if ux > 0 else (uy, -ux)  # the normal that points up the course
        dx, dy = pa.x.iloc[k] - pb.x.iloc[k], pa.y.iloc[k] - pb.y.iloc[k]
        return {"ahead_m": round(dx * ux + dy * uy), "windward_m": round(dx * nx + dy * ny)}

    return {
        "a": a,
        "b": b,
        "t0": int(pa.index[0]),
        "t1": int(pa.index[-1]),
        "duration_s": dur,
        "leg": leg,
        "leg_name": names[leg],
        "leg_type": leg_types[leg],
        "side": ("starboard" if pa.side.iloc[0] < 0 else "port") + (" tack" if up else " gybe"),
        "apart_m": [
            round(float(math.hypot(pa.x.iloc[k] - pb.x.iloc[k], pa.y.iloc[k] - pb.y.iloc[k])))
            for k in (0, -1)
        ],
        "rel": [rel(0), rel(-1)],
        "boats": {a: ra, b: rb},
        "gain_m": gain,  # + = a gained on b
        "gain_speed_m": round(speed),
        "gain_angle_m": round(gain - speed),
        "gain_m_per_min": round(gain / dur * 60, 1),
    }


def pair_summary(races: list[dict]) -> list[dict]:
    """Every pair's side-by-side stretches added up, beats and runs apart."""
    out: dict[tuple, dict] = {}
    for r in races:
        for p in r.get("pairs", []):
            k = (p["a"], p["b"])
            row = out.setdefault(k, {"a": p["a"], "b": p["b"], "upwind": {}, "downwind": {}})
            t = row[p["leg_type"]]
            for key, v in (
                ("n", 1),
                ("duration_s", p["duration_s"]),
                ("gain_m", p["gain_m"]),
                ("gain_speed_m", p["gain_speed_m"]),
                ("gain_angle_m", p["gain_angle_m"]),
            ):
                t[key] = t.get(key, 0) + v
    for row in out.values():
        for t in (row["upwind"], row["downwind"]):
            if t.get("duration_s"):
                t["gain_m_per_min"] = round(t["gain_m"] / t["duration_s"] * 60, 1)
    return list(out.values())


# ---------------------------------------------------------------- per race


def race_fleet(stem: str, entries: list[tuple[dict, dict]]) -> dict:
    """entries: (boat, {"race", "summary"}) for every boat that sailed this race."""
    boats, per_second = {}, {}
    ref_legs = course_xy = targets_xy = None
    race_name, gun_local = stem, None
    for boat, e in entries:
        race, summ = e["race"], e["summary"]
        legs = A.course_legs(race)
        if not legs:
            continue
        ref_legs = ref_legs or legs
        df = race.df
        xy = frame(race)
        marks = [c for c in race.course if c["type"] in ("Mark", "Gate")]
        start_line = next(c for c in race.course if c["type"] == "StartLine")
        finish = next((c for c in race.course if c["type"] == "FinishLine"), None)
        targets = [_mid(m) for m in marks] + ([_mid(finish)] if finish else [])
        origin = [_mid(start_line)] + [_mid(m) for m in marks]
        mans = summ.get("maneuvers") or []
        leg_rows = []
        for i, lg in enumerate(legs):
            g = df[(df.t >= lg["start"]) & (df.t <= lg["end"])]
            s0 = (lg["start"] - race.gun).total_seconds()
            s1 = (lg["end"] - race.gun).total_seconds()
            lat, lon = g.Lat.to_numpy(), g.Lon.to_numpy()
            seg = A.dist_m(lat[:-1], lon[:-1], lat[1:], lon[1:])
            sailed = float(np.nansum(seg)) / A.NM
            straight = float(A.dist_m(*origin[i], *targets[i])) / A.NM if i < len(targets) else None
            row = {
                "leg": i + 1,
                "type": lg["type"],
                "start_s": round(s0),
                "end_s": round(s1),
                "duration_s": round(s1 - s0),
                "sog_avg": round(float(g.SOG.mean()), 2),
                "heel_abs_avg": round(float(g.Heel.abs().mean()), 1) if "Heel" in g else None,
                "sailed_nm": round(sailed, 2),
                "straight_nm": round(straight, 2) if straight else None,
                "extra_pct": round(100 * (sailed / straight - 1), 1) if straight else None,
                "tacks": sum(m["kind"] == "Tack" and s0 <= m["time_s"] <= s1 for m in mans),
                "gybes": sum(m["kind"] == "Gybe" and s0 <= m["time_s"] <= s1 for m in mans),
            }
            if i < len(targets):
                row.update(side_of_rhumb(g, origin[i], targets[i], xy))
            if lg["type"] == "upwind":
                row.update(tacking_angles(g, lg["start"]))
            leg_rows.append(row)
        # summary legs (steady speed, tacking angle, heel) in beat/run order
        by_type = {"upwind": [], "downwind": []}
        for lg in summ.get("legs") or []:
            by_type.setdefault(lg["type"], []).append(lg)
        seen = {"upwind": 0, "downwind": 0}
        for row in leg_rows:
            k = seen[row["type"]]
            seen[row["type"]] += 1
            src = by_type.get(row["type"], [])
            if k < len(src):
                for key in ("sog_steady", "vmg_steady", "tacking_angle", "heel_abs_std"):
                    row[key] = src[k].get(key)
        per_second[boat["id"]] = _per_second(race, df, legs, xy, summ)
        # series for the track overlay and the replay
        w = df[
            (df.t >= race.gun - pd.Timedelta(seconds=60))
            & (df.t <= legs[-1]["end"] + pd.Timedelta(seconds=30))
            & df.Lat.notna()
        ].iloc[::SERIES_STEP_S]
        x, y = xy(w.Lat.to_numpy(), w.Lon.to_numpy())
        boats[boat["id"]] = {
            "name": boat["name"],
            "color": boat["color"],
            "color_dark": boat["color_dark"],
            "passes_s": [r["end_s"] for r in leg_rows],
            "legs": leg_rows,
            "start": summ.get("start") or {},
            "roundings": summ.get("roundings") or [],
            "maneuver_summary": summ.get("maneuver_summary") or {},
            "series": {
                "t": [round(v) for v in (w.t - race.gun).dt.total_seconds()],
                "x": [round(float(v), 1) for v in x],
                "y": [round(float(v), 1) for v in y],
                "sog": [None if pd.isna(v) else round(float(v), 2) for v in w.SOG],
                "heel": _rounded(w, "Heel", 0),
                "cog": _rounded(w, "COG", 0),
            },
        }
        if course_xy is None:
            course_xy = [
                {"type": c["type"], "pts": [list(map(float, xy(*p))) for p in A._course_points(c)]}
                for c in race.course
            ]
            race_name, gun_local = summ.get("race", stem), summ.get("gun_local")
            targets_xy = [[round(float(v), 1) for v in xy(*p)] for p in targets]
    if not boats:
        return {}
    labels = leg_labels(ref_legs)
    n = min(len(b["passes_s"]) for b in boats.values())
    labels = labels[:n]
    # ranks and gaps at each mark
    for j in range(n):
        order = sorted(boats, key=lambda k: boats[k]["passes_s"][j])
        lead = boats[order[0]]["passes_s"][j]
        for rank, k in enumerate(order, 1):
            boats[k].setdefault("ranks", []).append(rank)
            boats[k].setdefault("gaps_s", []).append(boats[k]["passes_s"][j] - lead)
    # leg deltas vs the fastest boat on that leg
    for j in range(n):
        best = min(b["legs"][j]["duration_s"] for b in boats.values())
        for b in boats.values():
            b["legs"][j]["vs_best_s"] = b["legs"][j]["duration_s"] - best
    for b in boats.values():
        b["place"] = b["ranks"][-1]
        b["finish_s"] = b["passes_s"][n - 1]
        b["gap_s"] = b["gaps_s"][-1]
    # where the time went, against the first tracked boat so the parts add up to its gap:
    # start = line crossing later than that boat's; leg 1 runs from each boat's own crossing
    win = min(boats.values(), key=lambda b: b["finish_s"])
    for b in boats.values():
        cross = b["start"].get("late_s") or 0.0
        wcross = win["start"].get("late_s") or 0.0
        split = {"start": cross - wcross, "upwind": 0.0, "downwind": 0.0}
        for j in range(n):
            mine = b["passes_s"][j] - (b["passes_s"][j - 1] if j else cross)
            theirs = win["passes_s"][j] - (win["passes_s"][j - 1] if j else wcross)
            split[b["legs"][j]["type"]] += mine - theirs
        b["time_split_s"] = {k: round(v) for k, v in split.items()}
    leg_types = [lg["type"] for lg in ref_legs[:n]]
    return {
        "stem": stem,
        "race": race_name,
        "gun_local": gun_local,
        "marks": labels,
        "leg_types": leg_types,
        "leg_names": leg_names(leg_types),
        "course": course_xy,
        "targets": targets_xy,
        "boats": boats,
        "pairs": side_by_side(per_second, leg_types),
    }


def fleet_analysis(boats: list[dict]) -> dict:
    stems = sorted({s for b in boats for s in b["races"]}, key=lambda s: (len(s), s))
    races = []
    for stem in stems:
        entries = [(b, b["races"][stem]) for b in boats if stem in b["races"]]
        r = race_fleet(stem, entries)
        if r:
            races.append(r)
    day = {}
    for b in boats:
        rs = [r["boats"][b["id"]] for r in races if b["id"] in r["boats"]]
        day[b["id"]] = {
            "name": b["name"],
            "color": b["color"],
            "color_dark": b["color_dark"],
            "places": [x["place"] for x in rs],
            "points": sum(x["place"] for x in rs),
            "total_gap_s": sum(x["gap_s"] for x in rs),
            "split_s": {
                k: sum(x["time_split_s"][k] for x in rs) for k in ("start", "upwind", "downwind")
            },
        }
    return {"races": races, "day": day, "pairs": pair_summary(races)}


def _dayname(date: str) -> str:
    """'2026-02-21' -> 'Sat 21 Feb'."""
    d = dt.date.fromisoformat(date)
    return f"{d:%a} {d.day} {d:%b}"


def _label(ours: str, official: str) -> str:
    """'1044 (Flash)', but just 'Mojo' when the official name is the same."""
    same = official.lower().strip("!").startswith(ours.lower())
    return ours if same else f"{ours} ({official})"


def add_official(fa: dict, official: dict, sails: dict[str, str]) -> None:
    """Put the official results (yachtscoring.py) next to the tracked-boat comparison.

    sails maps our boat folder ids to sail numbers. Races are matched by start time."""
    fleet_boats = official["boats"]
    n_fleet = sum(1 for b in fleet_boats if any(s == "AOK" for s in b["status"]))
    starts = {r["start_local"][:16]: r["number"] for r in official["races"]}
    numbers = [starts.get((r.get("gun_local") or "")[:16]) for r in fa["races"]]
    idx = [n - 1 for n in numbers if n]

    def day_total(b, races=idx):
        return sum(b["points"][i] or 0 for i in races)

    by_day = sorted(fleet_boats, key=day_total)
    # the same, day by day (races grouped by their start date)
    days: dict[str, list[int]] = {}
    for n in numbers:
        if n:
            days.setdefault(official["races"][n - 1]["start_local"][:10], []).append(n - 1)
    corinthian = [b for b in fleet_boats if b["corinthian"]]
    out = {
        "event": official["event"],
        "source": official["source"],
        "fleet_size": len(fleet_boats),
        "finishers": n_fleet,
        "race_numbers": numbers,
        "race_winners": [
            next((b["name"] for b in fleet_boats if b["points"][i] == 1), None) for i in idx
        ],
        "corinthian_size": len(corinthian),
        "days": list(days),
        "boats": {},
    }
    for bid, sail in sails.items():
        b = next((x for x in fleet_boats if x["sail"] == str(sail)), None)
        if b is None or bid not in fa["day"]:
            continue
        total = day_total(b)
        out["boats"][bid] = {
            "name": b["name"],
            "sail": b["sail"],
            "skipper": b["skipper"],
            "corinthian": b["corinthian"],
            "places": [b["points"][i] for i in idx],
            "day_total": total,
            "day_rank": 1 + sum(day_total(x) < total for x in by_day),
            "days": {
                d: {
                    "total": day_total(b, rs),
                    "rank": 1 + sum(day_total(x, rs) < day_total(b, rs) for x in fleet_boats),
                }
                for d, rs in days.items()
            },
            "overall_place": b["overall_place"],
            "net": b["net"],
            "all_places": b["points"],
            "corinthian_place": corinthian.index(b) + 1 if b["corinthian"] else None,
        }
    # what a place was worth: GPS gap between two tracked boats over the official places between them
    per_place = []
    for r, n in zip(fa["races"], numbers, strict=False):
        if not n:
            continue
        ids = [i for i in r["boats"] if i in out["boats"]]
        for i in ids:
            for j in ids:
                pi, pj = out["boats"][i]["all_places"][n - 1], out["boats"][j]["all_places"][n - 1]
                gap = r["boats"][j]["finish_s"] - r["boats"][i]["finish_s"]
                if pj > pi and gap >= 5:  # skip photo finishes the GPS can't resolve
                    per_place.append(round(gap / (pj - pi), 1))
    out["s_per_place"] = sorted(per_place)
    fa["official"] = out


# ---------------------------------------------------------------- markdown


def _mmss(s):
    if s is None:
        return "–"
    s = round(s)
    return f"{'-' if s < 0 else ''}{abs(s) // 60}:{abs(s) % 60:02d}"


def _plus(s):
    return "–" if s is None else "0" if s == 0 else f"+{_mmss(s)}"


def write_md(fa: dict, out: Path, title: str) -> str:
    day = fa["day"]
    order = sorted(day, key=lambda k: (day[k]["points"], day[k]["total_gap_s"]))
    L = [f"# {title}", ""]
    L += [
        "## Order among the tracked boats",
        "",
        "| Boat | "
        + " | ".join(r["race"] for r in fa["races"])
        + " | Total | Time behind the first tracked boat |",
        "|---|" + "---|" * len(fa["races"]) + "---|---|",
    ]
    for k in order:
        d = day[k]
        L.append(
            f"| {d['name']} | "
            + " | ".join(str(p) for p in d["places"])
            + f" | {d['points']} | {_plus(d['total_gap_s'])} |"
        )
    L += [
        "",
        (
            "Order and gaps among the boats with data only, from each boat's finish-line crossing "
            "(mark passages from the course geometry, the same way for every boat). These are not "
            "race results unless every boat in the fleet was tracked."
        ),
        "",
    ]
    off = fa.get("official")
    if off:
        rn = [f"R{n}" for n in off["race_numbers"] if n]
        L += [
            "",
            f"## Official results ({off['fleet_size']} boats, YachtScoring)",
            "",
            "| Boat | Sail | Skipper | "
            + " | ".join(rn)
            + " | "
            + " | ".join(f"{_dayname(d)} total (rank of {off['fleet_size']})" for d in off["days"])
            + " | Event place (all races) |",
            "|---|---|---|" + "---|" * len(rn) + "---|" * len(off["days"]) + "---|",
        ]
        for k in sorted(off["boats"], key=lambda k: off["boats"][k]["day_total"]):
            o = off["boats"][k]
            event = f"{o['overall_place']} ({o['net']} net)"
            if o["corinthian"]:
                event += f"; Corinthian {o['corinthian_place']} of {off['corinthian_size']}"
            L.append(
                f"| {_label(fa['day'][k]['name'], o['name'])} | {o['sail']} | {o['skipper']} | "
                + " | ".join(str(p) for p in o["places"])
                + " | "
                + " | ".join(
                    f"{o['days'][d]['total']} ({o['days'][d]['rank']})" for d in off["days"]
                )
                + f" | {event} |"
            )
        spp = off["s_per_place"]
        L += [
            "",
            "Race winners: "
            + ", ".join(
                f"R{n} {w}" for n, w in zip(off["race_numbers"], off["race_winners"], strict=False)
            )
            + ".",
        ]
        if spp:
            mid = spp[len(spp) // 2]
            L += [
                "",
                (
                    f"What a place was worth: between the tracked boats, each official place was "
                    f"{spp[0]:.0f}–{spp[-1]:.0f} s of GPS time (median {mid:.0f} s). Places and "
                    "points are official; YachtScoring's finish times aren't used (some are "
                    "data-entry artifacts), so gaps come from the GPS tracks."
                ),
            ]
    L += [
        "",
        "## Where the time went (total over these races, seconds behind the first tracked boat in each race)",
        "",
        (
            "Against the first tracked boat in each race, so the three parts add up to the gap. "
            "Start: crossing the line later than that boat. Upwind and downwind: time lost (or gained, "
            "negative) on those legs, counting leg 1 from each boat's own line crossing; "
            "roundings sit inside the legs."
        ),
        "",
        "| Boat | Start | Upwind legs | Downwind legs | Total |",
        "|---|---|---|---|---|",
    ]
    for k in order:
        s = day[k]["split_s"]
        L.append(
            f"| {day[k]['name']} | {s['start']} | {s['upwind']} | {s['downwind']} | "
            f"{s['start'] + s['upwind'] + s['downwind']} |"
        )
    for r in fa["races"]:
        bs = r["boats"]
        ids = sorted(bs, key=lambda k: bs[k]["place"])
        L += ["", f"## {r['race']} ({(r['gun_local'] or '')[11:16]})", ""]
        L += [
            "**Gap to the first tracked boat at each mark**",
            "",
            "| Boat | " + " | ".join(r["marks"]) + " |",
            "|---|" + "---|" * len(r["marks"]) + "",
        ]
        for k in ids:
            L.append(
                f"| {bs[k]['name']} | "
                + " | ".join(
                    f"{_plus(g)} ({rk})"
                    for g, rk in zip(bs[k]["gaps_s"], bs[k]["ranks"], strict=False)
                )
                + " |"
            )
        L += ["", "Gap in min:s; order among the tracked boats at that mark in brackets.", ""]
        L += [
            "**Start**",
            "",
            "| Boat | Late (s) | Line pos (from pin) | SOG at gun | Accel ±5 s | Back at −60 s |",
            "|---|---|---|---|---|---|",
        ]
        for k in ids:
            s = bs[k]["start"]
            late = (
                A.ocs_label(s)
                if s.get("ocs_at_gun_m")
                else "on the line"
                if s.get("late_s") == 0
                else s.get("late_s")
            )
            L.append(
                f"| {bs[k]['name']} | {late} | {s.get('line_pos_pct_from_pin')}% | "
                f"{s.get('sog_+0s')} kt | {s.get('accel_pm5s_kt')} kt | "
                f"{s.get('below_line_-60s_m')} m |"
            )
        L += [
            "",
            "**Legs**",
            "",
            (
                "| Leg | Boat | Time | vs fastest | SOG | Sailed / straight nm | Tacks | Gybes | "
                "% right of rhumb | Heel |"
            ),
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for j, lab in enumerate(r["marks"]):
            for k in ids:
                lg = bs[k]["legs"][j]
                L.append(
                    f"| {j + 1} {lg['type']} to {lab} | {bs[k]['name']} | {_mmss(lg['duration_s'])} | "
                    f"{_plus(lg['vs_best_s'])} | {lg['sog_avg']} | "
                    f"{lg['sailed_nm']} / {lg['straight_nm']} | {lg['tacks']} | {lg['gybes']} | "
                    f"{lg.get('pct_right', '–')} | {lg['heel_abs_avg']}° |"
                )
        L += [
            "",
            "**Roundings (metres lost, each boat against its own steady VMG)**",
            "",
            "| Boat | " + " | ".join(r["marks"][: len(bs[ids[0]]["roundings"])]) + " |",
            "|---|" + "---|" * len(bs[ids[0]]["roundings"]),
        ]
        for k in ids:
            L.append(
                f"| {bs[k]['name']} | "
                + " | ".join(
                    "–" if x.get("metres_lost") is None else f"{x['metres_lost']:.0f}"
                    for x in bs[k]["roundings"]
                )
                + " |"
            )
    L += [
        "",
        (
            "*Speeds are over ground. % right of rhumb: share of the leg spent right of the line "
            "from the previous mark to the next, looking at the mark.*"
        ),
        "",
    ]
    L += pairs_md(fa)
    text = "\n".join(L) + "\n"
    (out / "fleet.md").write_text(text)
    return text


# ---------------------------------------------------------------- html


FLEET_JS = r"""
(function () {
  function css(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function dark() {
    const t = document.documentElement.dataset.theme;
    return t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  }
  function col(b) { return dark() ? b.color_dark : b.color; }
  function mmss(t) {
    const a = Math.abs(Math.round(t));
    return Math.floor(a / 60) + ':' + String(a % 60).padStart(2, '0');
  }
  function clock(t) { return t < 0 ? '−' + mmss(t) + ' to gun' : '+' + mmss(t); }
  function signed(v) { return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v) + ' s'; }
  const FLEET = JSON.parse(document.getElementById('fleet-data').textContent);
  const IDS = Object.keys(FLEET.day);  // fixed boat order: colour follows the boat
  const CONFIG = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ['toImage', 'lasso2d', 'select2d'] };
  // long titles break onto two lines on a phone instead of running off the chart
  function wrap(title, el) {
    if (!el || el.clientWidth >= 560 || title.length < 40) return title;
    const mid = Math.floor(title.length / 2);
    let cut = title.lastIndexOf(' ', mid);
    if (cut < 10) cut = title.indexOf(' ', mid);
    return cut > 0 ? title.slice(0, cut) + '<br>' + title.slice(cut + 1) : title;
  }
  function base(title, h, el) {
    const ink = css('--ink'), line = css('--line'), muted = css('--ink2');
    const narrow = el && el.clientWidth < 560;
    return {
      title: { text: wrap(title, el), font: { size: narrow ? 12 : 14, color: ink } }, height: h || 340,
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      font: { color: muted, size: 12 }, margin: { l: 56, r: 12, t: narrow ? 48 : 40, b: narrow ? 64 : 48 },
      legend: { orientation: 'h', y: narrow ? -0.32 : -0.2, font: { color: ink } },
      hoverlabel: { font: { size: 12 } },
      xaxis: { gridcolor: line, zerolinecolor: line, linecolor: line },
      yaxis: { gridcolor: line, zerolinecolor: muted, zerolinewidth: 1, linecolor: line },
    };
  }
  // every beat, race by race, in order: label, race, and each boat's leg row
  function beats() {
    const out = [];
    for (const r of FLEET.races) {
      const n = (r.race.match(/\d+/) || [''])[0];
      let k = 0;
      r.leg_types.forEach((t, j) => {
        if (t !== 'upwind') return;
        k += 1;
        const legs = {};
        for (const id of Object.keys(r.boats)) legs[id] = r.boats[id].legs[j];
        out.push({ label: 'R' + n + ' B' + k, name: r.race + ' beat ' + k, legs });
      });
    }
    return out;
  }
  function leewards() {
    const out = [];
    for (const r of FLEET.races) {
      const n = (r.race.match(/\d+/) || [''])[0];
      const any = r.boats[Object.keys(r.boats)[0]];
      let k = 0;
      any.roundings.forEach((x, i) => {
        if (x.type !== 'leeward') return;
        k += 1;
        const rs = {};
        for (const id of Object.keys(r.boats)) rs[id] = r.boats[id].roundings[i];
        out.push({ label: 'R' + n + ' L' + k, name: r.race + ' leeward ' + k, rs });
      });
    }
    return out;
  }
  const CHARTS = {
    gaps(el, r) {
      const traces = Object.keys(r.boats).map(id => {
        const b = r.boats[id];
        return { x: r.marks, y: b.gaps_s, mode: 'lines+markers', name: b.name,
          line: { color: col(b), width: 2 }, marker: { size: 8 },
          customdata: b.gaps_s.map((g, i) => [mmss(g), b.ranks[i]]),
          hovertemplate: '<b>' + b.name + '</b> %{x}<br>%{customdata[0]} behind, order %{customdata[1]}<extra></extra>' };
      });
      const lay = base(r.race + ': seconds behind the first tracked boat at each mark', 340, el);
      lay.yaxis.autorange = 'reversed'; lay.yaxis.title = 's behind';
      Plotly.newPlot(el, traces, lay, CONFIG);
    },
    // Official places in each race, out of the whole fleet
    places(el) {
      const off = FLEET.official;
      if (!off) { el.textContent = 'No official results loaded (fleet.py --official).'; return; }
      const labels = off.race_numbers.map(n => 'Race ' + n);
      const traces = IDS.filter(id => off.boats[id]).map(id => {
        const o = off.boats[id], d = FLEET.day[id];
        return { type: 'scatter', mode: 'lines+markers', name: o.name.toLowerCase().replace(/!+$/, '').startsWith(d.name.toLowerCase()) ? d.name : d.name + ' (' + o.name + ')', x: labels, y: o.places,
          line: { color: col(d), width: 2 }, marker: { size: 10, color: col(d), line: { color: css('--card'), width: 2 } },
          hovertemplate: '<b>' + d.name + '</b> · %{x}<br>%{y} of ' + off.fleet_size + '<extra></extra>' };
      });
      const lay = base('Official place in each race, out of ' + off.fleet_size + ' boats', 340, el);
      lay.yaxis.autorange = false; lay.yaxis.range = [off.fleet_size + 1, 0];
      lay.yaxis.title = 'place (1 = race win)';
      Plotly.newPlot(el, traces, lay, CONFIG);
    },
    // Takeaway 1: where the time went
    split(el) {
      const parts = [['start', 'Start'], ['upwind', 'Upwind legs'], ['downwind', 'Downwind legs']];
      const traces = IDS.map(id => {
        const d = FLEET.day[id];
        const ref = parts.every(p => d.split_s[p[0]] === 0);
        return { type: 'bar', name: d.name + (ref ? ' (the reference: 0)' : ''), x: parts.map(p => p[1]),
          y: parts.map(p => d.split_s[p[0]]), marker: { color: col(d) },
          text: parts.map(p => d.split_s[p[0]] === 0 ? '' : signed(d.split_s[p[0]])),
          textposition: 'outside', textfont: { color: css('--ink2'), size: 11 }, cliponaxis: false,
          customdata: parts.map(p => signed(d.split_s[p[0]])),
          hovertemplate: '<b>' + d.name + '</b> · %{x}<br>%{customdata} against the first tracked boat<extra></extra>' };
      });
      const lay = base('Where the time went: seconds behind the first tracked boat, all races', 360, el);
      lay.barmode = 'group'; lay.bargap = 0.3; lay.bargroupgap = 0.08; lay.yaxis.title = 's (negative = gained)';
      Plotly.newPlot(el, traces, lay, CONFIG);
    },
    // Takeaway 2: which boat was fastest on each beat
    beats(el) {
      const bs = beats();
      const traces = IDS.map(id => ({
        type: 'bar', name: FLEET.day[id].name, x: bs.map(b => b.label),
        y: bs.map(b => b.legs[id] ? b.legs[id].vs_best_s : null), marker: { color: col(FLEET.day[id]) },
        text: bs.map(b => b.legs[id] && b.legs[id].vs_best_s === 0 ? 'fastest' : ''),
        textposition: 'outside', textfont: { color: col(FLEET.day[id]), size: 10 }, cliponaxis: false,
        customdata: bs.map(b => b.legs[id] ? [b.name, mmss(b.legs[id].duration_s), b.legs[id].pct_right] : ['', '', '']),
        hovertemplate: '<b>' + FLEET.day[id].name + '</b> · %{customdata[0]}<br>+%{y} s on the fastest of the three' +
          '<br>leg %{customdata[1]}, %{customdata[2]}% right of the rhumb line<extra></extra>' }));
      const lay = base('Time lost on each beat to the fastest of the three (0 = fastest)', 340, el);
      lay.barmode = 'group'; lay.bargap = 0.25; lay.bargroupgap = 0.08; lay.yaxis.title = 's behind';
      Plotly.newPlot(el, traces, lay, CONFIG);
    },
    // Takeaway 3: pointing by compass vs angle made over the ground
    angles(el) {
      const bs = beats(), traces = [];
      const mean = xs => xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
      IDS.forEach((id, i) => {
        const d = FLEET.day[id];
        const rows = bs.map(b => b.legs[id]).filter(l => l && l.ta_cog != null);
        const hdg = mean(rows.map(l => l.ta_heading)), cog = mean(rows.map(l => l.ta_cog));
        if (hdg == null) return;
        traces.push({ x: [hdg, cog], y: [d.name, d.name], mode: 'lines+markers', showlegend: false,
          line: { color: col(d), width: 3 },
          marker: { size: 13, color: [css('--card'), col(d)], line: { color: col(d), width: 2 } },
          customdata: [['by compass', ''], ['over the ground', ' · slip ' + ((cog - hdg) / 2).toFixed(1) + '° a tack']],
          hovertemplate: '<b>' + d.name + '</b> %{customdata[0]}: %{x:.0f}°%{customdata[1]}<extra></extra>' });
      });
      // key: hollow = compass, filled = ground (neutral ink so it isn't read as a boat)
      const ink = css('--ink2');
      traces.push({ x: [null], y: [null], mode: 'markers', name: 'By compass (heading)',
        marker: { size: 11, color: css('--card'), line: { color: ink, width: 2 } } });
      traces.push({ x: [null], y: [null], mode: 'markers', name: 'Over the ground (GPS)',
        marker: { size: 11, color: ink } });
      const lay = base('Upwind tacking angle, day average: by compass vs over the ground', 320, el);
      lay.xaxis.title = 'tacking angle (°) · smaller = higher';
      lay.yaxis.type = 'category'; lay.yaxis.autorange = 'reversed';
      lay.margin.l = 70; lay.margin.b = 90; lay.legend.y = -0.42;
      Plotly.newPlot(el, traces, lay, CONFIG);
    },
    // Takeaway 4: side of the course against time lost, every beat
    sides(el) {
      const bs = beats();
      const traces = IDS.map(id => {
        const d = FLEET.day[id], rows = bs.filter(b => b.legs[id]);
        return { type: 'scatter', mode: 'markers', name: d.name,
          x: rows.map(b => b.legs[id].pct_right), y: rows.map(b => b.legs[id].vs_best_s),
          marker: { size: 12, color: col(d), line: { color: css('--card'), width: 2 } },
          customdata: rows.map(b => b.name),
          hovertemplate: '<b>' + d.name + '</b> · %{customdata}<br>%{x}% right of the rhumb line<br>+%{y} s on the fastest<extra></extra>' };
      });
      const lay = base('Side of the course vs time lost, every beat', 360, el);
      lay.xaxis.title = '% of the beat right of the rhumb line'; lay.xaxis.range = [-5, 105];
      lay.yaxis.title = 's behind the fastest';
      lay.shapes = [{ type: 'line', x0: 50, x1: 50, yref: 'paper', y0: 0, y1: 1, line: { color: css('--line'), dash: 'dot' } }];
      Plotly.newPlot(el, traces, lay, CONFIG);
    },
    // Takeaway 5: heel upwind by beat
    heel(el) {
      const bs = beats(), n = IDS.length;
      const traces = IDS.map((id, i) => ({
        type: 'scatter', mode: 'markers', name: FLEET.day[id].name,
        x: bs.map((b, j) => j + (i - (n - 1) / 2) * 0.16),  // dodge so equal values don't hide
        y: bs.map(b => b.legs[id] ? b.legs[id].heel_abs_avg : null),
        marker: { size: 11, color: col(FLEET.day[id]), line: { color: css('--card'), width: 2 } },
        customdata: bs.map(b => b.legs[id] ? [b.name, b.legs[id].vs_best_s] : ['', '']),
        hovertemplate: '<b>' + FLEET.day[id].name + '</b> · %{customdata[0]}<br>heel %{y}°, +%{customdata[1]} s on the fastest<extra></extra>' }));
      const lay = base('Average heel upwind, by beat', 340, el);
      lay.yaxis.title = 'heel (°)';
      lay.xaxis.tickvals = bs.map((b, j) => j); lay.xaxis.ticktext = bs.map(b => b.label);
      lay.xaxis.showgrid = false;
      Plotly.newPlot(el, traces, lay, CONFIG);
    },
    // Takeaway 6: speed out of the leeward marks
    exits(el) {
      const ls = leewards();
      const traces = IDS.map(id => ({
        type: 'bar', name: FLEET.day[id].name, x: ls.map(l => l.label),
        y: ls.map(l => l.rs[id] ? l.rs[id].sog_exit : null), marker: { color: col(FLEET.day[id]) },
        customdata: ls.map(l => l.rs[id] ? [l.name, l.rs[id].settle_s, Math.round(l.rs[id].metres_lost)] : ['', '', '']),
        hovertemplate: '<b>' + FLEET.day[id].name + '</b> · %{customdata[0]}<br>%{y} kt out of the mark' +
          '<br>%{customdata[1]} s to get back to speed, %{customdata[2]} m lost<extra></extra>' }));
      const lay = base('Speed out of the leeward mark (20–30 s after it)', 340, el);
      lay.barmode = 'group'; lay.bargap = 0.3; lay.bargroupgap = 0.08; lay.yaxis.title = 'kt';
      Plotly.newPlot(el, traces, lay, CONFIG);
    },
    // Takeaway 7: tack recovery
    tacks(el) {
      const races = FLEET.races.map(r => r.race);
      const traces = IDS.map(id => ({
        type: 'bar', name: FLEET.day[id].name, x: races,
        y: FLEET.races.map(r => { const t = r.boats[id] && r.boats[id].maneuver_summary.Tack; return t ? t.recovery_avg_s : null; }),
        marker: { color: col(FLEET.day[id]) },
        customdata: FLEET.races.map(r => { const t = r.boats[id] && r.boats[id].maneuver_summary.Tack; return t ? [t.count, t.speed_loss_avg_pct] : ['', '']; }),
        hovertemplate: '<b>' + FLEET.day[id].name + '</b> · %{x}<br>%{y} s back to speed on average' +
          '<br>%{customdata[0]} tacks, %{customdata[1]}% speed loss<extra></extra>' }));
      const lay = base('Time to get back to speed after a tack (average per race)', 340, el);
      lay.barmode = 'group'; lay.bargap = 0.3; lay.bargroupgap = 0.08; lay.yaxis.title = 's';
      Plotly.newPlot(el, traces, lay, CONFIG);
    },
    pair(el) {
      const a = el.dataset.a, b = el.dataset.b, A = FLEET.day[a], B = FLEET.day[b];
      const rows = [];
      for (const r of FLEET.races) for (const p of r.pairs || []) {
        if (p.a === a && p.b === b) rows.push([r, p]);
      }
      const n = rows.length;
      const lab = rows.map(([r, p]) => 'R' + (r.race.match(/\d+/) || [''])[0] + ' ' + p.leg_name + ' ' + (p.side.startsWith('star') ? 'stbd' : 'port') + ' ' + mmss(p.t0));
      const gainer = p => (p.gain_m >= 0 ? A : B);
      const trace = {
        type: 'bar', orientation: 'h', y: lab, x: rows.map(([, p]) => p.gain_m),
        marker: { color: rows.map(([, p]) => col(gainer(p))) },
        text: rows.map(([, p]) => Math.abs(p.gain_m) + ' m'), textposition: 'outside', cliponaxis: false,
        textfont: { color: css('--ink2'), size: 11 },
        customdata: rows.map(([, p]) => [gainer(p).name, Math.abs(p.gain_m), mmss(p.duration_s),
          p.gain_speed_m, p.gain_angle_m, p.boats[a].sog, p.boats[b].sog, p.boats[a].angle, p.boats[b].angle]),
        hovertemplate: '<b>%{y}</b><br>%{customdata[0]} gained %{customdata[1]} m in %{customdata[2]}' +
          '<br>speed %{customdata[3]} m, angle %{customdata[4]} m (+ = ' + A.name + ')' +
          '<br>SOG ' + A.name + ' %{customdata[5]} · ' + B.name + ' %{customdata[6]} kt' +
          '<br>angle ' + A.name + ' %{customdata[7]}° · ' + B.name + ' %{customdata[8]}°<extra></extra>',
        showlegend: false,
      };
      const key = [A, B].map(x => ({ type: 'bar', x: [null], y: [null], name: x.name + ' gained',
        marker: { color: col(x) }, hoverinfo: 'skip' }));
      const lay = base('Metres gained in each stretch side by side (' + A.name + ' right, ' + B.name + ' left)', 90 + 26 * n, el);
      lay.yaxis = Object.assign(lay.yaxis, { type: 'category', autorange: 'reversed', automargin: true });
      lay.xaxis.title = 'm up (beat) or down (run) the course';
      lay.xaxis.zeroline = true; lay.xaxis.zerolinecolor = css('--ink2');
      lay.margin.l = 8;
      lay.legend.y = -60 / (90 + 26 * n) - 0.1;
      Plotly.newPlot(el, [trace, ...key], lay, CONFIG);
    },
  };
  // ---------------------------------------------------------------- replay
  const ORD = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th'];
  function at(ts, t) {  // index of the last sample at or before t
    let lo = 0, hi = ts.length - 1;
    if (t < ts[0]) return -1;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (ts[m] <= t) lo = m; else hi = m - 1; }
    return lo;
  }
  function replay(el) {
    const races = el.dataset.race ? FLEET.races.filter(x => x.stem === el.dataset.race) : FLEET.races;
    el.innerHTML =
      (races.length > 1 ? '<div class="rp-races">' + races.map((r, i) =>
        '<button type="button" data-i="' + i + '">' + r.race + '</button>').join('') + '</div>' : '') +
      '<div class="rp-body"><div class="rp-main"><div class="rp-plot"></div>' +
      '<div class="rp-ctl"><button type="button" class="rp-play" aria-label="Play">▶</button>' +
      '<input class="rp-slider" type="range" step="1" aria-label="Race time">' +
      '<span class="rp-time"></span>' +
      '<select class="rp-speed" aria-label="Replay speed"><option value="10">10×</option>' +
      '<option value="30" selected>30×</option><option value="60">60×</option><option value="120">120×</option></select></div>' +
      '</div><aside class="rp-panel"></aside></div>';
    const plot = el.querySelector('.rp-plot'), panel = el.querySelector('.rp-panel');
    const slider = el.querySelector('.rp-slider'), label = el.querySelector('.rp-time');
    const playBtn = el.querySelector('.rp-play'), speedSel = el.querySelector('.rp-speed');
    let r, ids, T0, T1, t = 0, timer = null, stopAt = null, live = [];
    function load(i) {
      pause();
      r = races[i];
      el.querySelectorAll('.rp-races button').forEach((b, k) => b.setAttribute('aria-pressed', k === i));
      ids = IDS.filter(id => r.boats[id]);
      T0 = Math.min(...ids.map(id => r.boats[id].series.t[0]));
      T1 = Math.max(...ids.map(id => r.boats[id].series.t.at(-1)));
      slider.min = T0; slider.max = T1;
      const traces = [], xs = [], ys = [];
      for (const c of r.course) {
        traces.push({ x: c.pts.map(p => p[0]), y: c.pts.map(p => p[1]), mode: c.pts.length > 1 ? 'lines+markers' : 'markers',
          marker: { size: 9, color: css('--ink2'), symbol: c.type === 'Offset' ? 'circle-open' : 'diamond' },
          line: { color: css('--ink2'), dash: 'dot' }, name: c.type, showlegend: false, hoverinfo: 'name' });
        c.pts.forEach(p => { xs.push(p[0]); ys.push(p[1]); });
      }
      for (const id of ids) {  // whole track, faint
        const b = r.boats[id], s = b.series;
        traces.push({ x: s.x, y: s.y, mode: 'lines', line: { color: col(b), width: 1 }, opacity: 0.25,
          hoverinfo: 'skip', showlegend: false });
        xs.push(...s.x); ys.push(...s.y);
      }
      live = [];
      for (const id of ids) {  // last 90 s, then the boat
        const b = r.boats[id];
        live.push(traces.length);
        traces.push({ x: [], y: [], mode: 'lines', line: { color: col(b), width: 3 }, hoverinfo: 'skip', showlegend: false });
        live.push(traces.length);
        traces.push({ x: [], y: [], mode: 'markers+text', text: [b.name], textposition: 'top center',
          textfont: { color: col(b), size: 12 }, marker: { size: 12, color: col(b), line: { color: css('--card'), width: 2 } },
          hoverinfo: 'skip', showlegend: false });
      }
      const pad = 60, lay = base(r.race + ' replay (upwind is up)', el.clientWidth < 560 ? 420 : 560, plot);
      lay.xaxis.range = [Math.min(...xs) - pad, Math.max(...xs) + pad];
      lay.yaxis.range = [Math.min(...ys) - pad, Math.max(...ys) + pad];
      lay.xaxis.scaleanchor = 'y'; lay.xaxis.title = 'm'; lay.yaxis.title = 'm';
      lay.margin.b = 40; lay.uirevision = r.stem; lay.showlegend = false;
      Plotly.react(plot, traces, lay, CONFIG);
      set(Math.max(T0, 0));
    }
    function status(b, k) {  // leg index, or -1 before the start, or passes.length after the finish
      const tt = b.series.t[k], cross = (b.start && b.start.late_s) || 0;
      if (tt < cross) return -1;
      let j = 0;
      while (j < b.passes_s.length && b.passes_s[j] <= tt) j++;
      return j;
    }
    function set(tt) {
      t = Math.max(T0, Math.min(T1, Math.round(tt)));
      slider.value = t;
      label.textContent = t < 0 ? '−' + mmss(t) + ' to the gun' : mmss(t) + ' after the gun';
      const xs = [], ys = [], rows = [];
      ids.forEach(id => {
        const b = r.boats[id], s = b.series, k = at(s.t, t);
        const from = Math.max(0, at(s.t, t - 90));
        xs.push(k < 0 ? [] : s.x.slice(from, k + 1), k < 0 ? [] : [s.x[k]]);
        ys.push(k < 0 ? [] : s.y.slice(from, k + 1), k < 0 ? [] : [s.y[k]]);
        if (k < 0) { rows.push({ id, b, k }); return; }
        const j = status(b, k), n = b.passes_s.length;
        const tgt = j >= 0 && j < n ? r.targets[j] : null;
        const dist = tgt ? Math.hypot(tgt[0] - s.x[k], tgt[1] - s.y[k]) : null;
        const a0 = Math.max(0, k - 3), a1 = Math.min(s.x.length - 1, k + 2);
        const dx = s.x[a1] - s.x[a0];
        rows.push({ id, b, k, j, n, dist, left: dx < 0, done: j >= n,
          key: j >= n ? -1e9 + b.finish_s : j < 0 ? 1e9 : -j * 1e6 + dist });
      });
      Plotly.restyle(plot, { x: xs, y: ys }, live);
      const racing = rows.filter(x => x.k >= 0 && x.j >= 0).sort((p, q) => p.key - q.key);
      const lead = racing[0];
      panel.innerHTML = rows.map(x => {
        const b = x.b, s = b.series;
        let head = '', leg = 'Not in the data yet', facts = '';
        if (x.k >= 0) {
          const pos = racing.indexOf(x);
          head = pos >= 0 ? '<span class="rp-pos">' + ORD[pos] + '</span>' : '';
          const up = x.j >= 0 && x.j < x.n && r.leg_types[x.j] === 'upwind';
          const side = (x.left ? 'starboard' : 'port') + (up ? ' tack' : ' gybe');
          if (x.j < 0) leg = t < 0 ? 'Pre-start' : b.start && b.start.ocs_at_gun_m ? 'Restarting (over at the gun)' : 'Starting';
          else if (x.done) leg = 'Finished' + (b.gap_s ? ', +' + mmss(b.gap_s) : ', first of the tracked boats');
          else leg = r.leg_names[x.j] + ' · ' + side;
          const heel = s.heel && s.heel[x.k] != null ? Math.abs(s.heel[x.k]) + '°' : '–';
          facts = '<dt>SOG</dt><dd>' + (s.sog[x.k] == null ? '–' : s.sog[x.k].toFixed(1)) + ' kt</dd><dt>Heel</dt><dd>' + heel + '</dd>';
          if (x.dist != null) facts += '<dt>To mark</dt><dd>' + Math.round(x.dist) + ' m</dd>';
          if (x.j > 0 && x.j <= b.gaps_s.length && !x.done) {
            const g = b.gaps_s[x.j - 1];
            facts += '<dt>Last mark</dt><dd>' + (g ? '+' + mmss(g) : 'first') + '</dd>';
          }
          if (lead && x !== lead && !x.done && !lead.done && x.j === lead.j && x.j >= 0)
            facts += '<dt>Behind</dt><dd>' + Math.round(x.dist - lead.dist) + ' m</dd>';
        }
        return '<div class="rp-boat" style="--c:' + col(b) + '"><div class="rp-name"><span class="rp-dot"></span>' +
          b.name + head + '</div><div class="rp-leg">' + leg + '</div><dl>' + facts + '</dl></div>';
      }).join('') + '<p class="rp-note">Position among the tracked boats: legs done, then distance to the next mark. ' +
        '“Behind” is the extra distance to the mark on the same leg.</p>';
    }
    function pause() { clearInterval(timer); timer = null; stopAt = null; playBtn.textContent = '▶'; playBtn.setAttribute('aria-label', 'Play'); }
    function play(until) {
      if (timer) return pause();
      if (t >= T1) set(T0);
      stopAt = until ?? null;
      playBtn.textContent = '❚❚'; playBtn.setAttribute('aria-label', 'Pause');
      timer = setInterval(() => {
        const nt = t + Number(speedSel.value) / 10, end = stopAt ?? T1;
        set(Math.min(nt, end));
        if (nt >= end) pause();
      }, 100);
    }
    playBtn.addEventListener('click', () => play());
    slider.addEventListener('input', () => { pause(); set(Number(slider.value)); });
    el.querySelectorAll('.rp-races button').forEach(b => b.addEventListener('click', () => load(Number(b.dataset.i))));
    el._replay = {
      watch(stem, t0, t1) {
        const i = races.findIndex(x => x.stem === stem);
        if (i < 0) return;
        if (races[i] !== r) load(i);
        pause(); set(t0 - 10); play(t1 + 10);
      },
    };
    load(0);
  }
  // "Watch" links on the side-by-side page jump to that race's replay and play the stretch
  document.addEventListener('click', e => {
    const a = e.target.closest('a[data-watch]');
    if (!a) return;
    setTimeout(() => {
      const el = document.getElementById('replay-' + a.dataset.watch);
      if (el && el._replay) el._replay.watch(a.dataset.watch, Number(a.dataset.t0), Number(a.dataset.t1));
    }, 60);
  });
  function render(root) {
    if (!window.Plotly) {
      root.querySelectorAll('.chart:not([data-done])').forEach(el => {
        el.textContent = 'Interactive chart needs an internet connection (the chart library loads online).';
      });
      return;
    }
    root.querySelectorAll('.replay:not([data-done])').forEach(el => {
      el.dataset.done = '1';
      try { replay(el); } catch (e) { el.textContent = 'Replay failed: ' + e.message; }
    });
    root.querySelectorAll('.chart[data-fleet]:not([data-done])').forEach(el => {
      el.dataset.done = '1';
      const r = el.dataset.race ? FLEET.races.find(x => x.stem === el.dataset.race) : null;
      try { CHARTS[el.dataset.fleet](el, r); } catch (e) { el.textContent = 'Chart failed: ' + e.message; }
    });
  }
  window.renderCharts = render;
})();
"""


def _chart(kind: str, stem: str | None = None) -> str:
    race = f' data-race="{stem}"' if stem else ""
    return f'<div class="chart" data-fleet="{kind}"{race}></div>'


def _replay(stem: str | None = None) -> str:
    """Race replay: a time slider and play button over the tracks, and each boat's numbers at
    that moment in a panel beside the chart. One race, or (no stem) buttons to pick the race."""
    if stem:
        return f'<div class="replay" id="replay-{stem}" data-race="{stem}"></div>'
    return '<div class="replay"></div>'


REPLAY_CSS = """
.replay { margin: 8px 0 16px; }
.rp-races { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.rp-races button, .rp-ctl button, .rp-ctl select { font: inherit; font-size: 0.88rem; color: var(--ink);
  background: var(--card); border: 1px solid var(--line); border-radius: 999px; padding: 4px 12px; cursor: pointer; }
.rp-races button[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: #fff; }
.rp-body { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; }
@media (min-width: 760px) { .rp-body { grid-template-columns: minmax(0, 1fr) 210px; } }
.rp-panel { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px;
  align-content: start; font-size: 0.86rem; }
@media (min-width: 760px) { .rp-panel { grid-template-columns: 1fr; } }
.rp-boat { border: 1px solid var(--line); border-left: 4px solid var(--c); border-radius: 8px; padding: 6px 10px; }
.rp-name { font-weight: 600; display: flex; align-items: center; gap: 6px; }
.rp-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--c); flex: none; }
.rp-pos { margin-left: auto; color: var(--ink2); font-weight: 500; font-variant-numeric: tabular-nums; }
.rp-leg { color: var(--ink2); margin: 1px 0 3px; }
.rp-boat dl { display: grid; grid-template-columns: auto 1fr; gap: 0 10px; margin: 0; font-variant-numeric: tabular-nums; }
.rp-boat dt { color: var(--muted); } .rp-boat dd { margin: 0; text-align: right; }
.rp-note { grid-column: 1 / -1; color: var(--muted); font-size: 0.78rem; margin: 0; }
.rp-ctl { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 6px; }
.rp-slider { flex: 1 1 220px; min-width: 0; accent-color: var(--accent); }
.rp-time { font-variant-numeric: tabular-nums; color: var(--ink2); min-width: 9.5em; font-size: 0.9rem; }
.rp-play { min-width: 44px; }
.gain { font-weight: 600; }
"""

# Day-level charts a fleet debrief can place with a line of its own: [[chart:split]].
# [[chart:replay]] puts the race replay there, with a button for each race.
TAKEAWAY_CHARTS = ("places", "split", "beats", "angles", "sides", "heel", "exits", "tacks")
CHART_LINE = re.compile(r"^\[\[chart:(\w+)\]\]\s*$")


def debrief_html(md: str) -> str:
    """The debrief as HTML, with each [[chart:name]] line replaced by that interactive chart."""
    out, chunk = [], []
    for line in md.splitlines():
        m = CHART_LINE.match(line)
        if not m:
            chunk.append(line)
            continue
        out.append(H.md_to_html("\n".join(chunk)))
        chunk = []
        if m.group(1) == "replay":
            out.append(_replay())
        elif m.group(1) in TAKEAWAY_CHARTS:
            out.append(_chart(m.group(1)))
    out.append(H.md_to_html("\n".join(chunk)))
    return "".join(out)


def _pair_names(fa: dict, a: str, b: str) -> tuple[str, str]:
    return fa["day"][a]["name"], fa["day"][b]["name"]


def pair_sentence(fa: dict, row: dict, kind: str) -> str | None:
    """'25 min side by side upwind: 1044 gained 83 m on Mojo (3.3 m a minute): 164 m from a
    closer angle, 81 m lost on speed.'"""
    t = row[kind]
    if not t.get("duration_s"):
        return None
    na, nb = _pair_names(fa, row["a"], row["b"])
    win, lose, sgn = (na, nb, 1) if t["gain_m"] >= 0 else (nb, na, -1)
    sp, an = sgn * t["gain_speed_m"], sgn * t["gain_angle_m"]

    def part(v, what):
        return f"{abs(v)} m {'from' if v >= 0 else 'lost on'} {what}"

    angle = "height" if kind == "upwind" else "depth"
    return (
        f"{round(t['duration_s'] / 60)} min side by side {kind} ({t['n']} stretches): "
        f"**{win} gained {abs(t['gain_m'])} m on {lose}** ({abs(t['gain_m_per_min'])} m a minute): "
        f"{part(sp, 'speed')}, {part(an, angle)}."
    )


def pairs_md(fa: dict) -> list[str]:
    if not fa.get("pairs"):
        return []
    L = [
        f"## Side by side (within {PAIR_RADIUS_M} m, same leg, same tack or gybe)",
        "",
        (
            "Both boats had the same wind, so the gain is the boats: speed, and angle (height on "
            "a beat, depth on a run). Gain is progress up or down the course axis."
        ),
        "",
    ]
    for row in fa["pairs"]:
        na, nb = _pair_names(fa, row["a"], row["b"])
        L.append(f"**{na} vs {nb}**")
        L += [f"- {x}" for k in ("upwind", "downwind") if (x := pair_sentence(fa, row, k))]
        L.append("")
    L += [
        (
            "| Race | Leg | Tack | From | Length | Boats | Apart (m) | Gain (m) | "
            "Speed / angle (m) | SOG (kt) | Angle (°) | Heel (°) |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in fa["races"]:
        for p in r.get("pairs", []):
            na, nb = _pair_names(fa, p["a"], p["b"])
            ba, bb = p["boats"][p["a"]], p["boats"][p["b"]]
            win = na if p["gain_m"] >= 0 else nb
            L.append(
                f"| {r['race']} | {p['leg_name']} | {p['side']} | {_mmss(p['t0'])} | "
                f"{_mmss(p['duration_s'])} | {na} / {nb} | {p['apart_m'][0]}→{p['apart_m'][1]} | "
                f"{win} +{abs(p['gain_m'])} | {p['gain_speed_m']:+} / {p['gain_angle_m']:+} | "
                f"{ba['sog']} / {bb['sog']} | {ba['angle']} / {bb['angle']} | "
                f"{ba['heel']} / {bb['heel']} |"
            )
    L += [
        "",
        (
            "*Gain, speed and angle are from the first boat's side (+ = it gained). Angle is the "
            "track against straight up or down the course, so it depends on the tack when the "
            "wind is shifted; compare the two boats on the same stretch.*"
        ),
        "",
    ]
    return L


def pairs_html(fa: dict) -> str:
    """The Side by side page: the rule, each pair's totals and chart, every stretch with a link
    that plays it on that race's replay."""
    out = [
        H.card(
            "<h2>How this works</h2><ul>"
            f"<li>A stretch counts when two boats are within {PAIR_RADIUS_M} m on the same leg and "
            f"the same tack or gybe for at least {PAIR_MIN_S} s, starting {PAIR_SETTLE_S} s after "
            "a mark or a tack or gybe.</li>"
            "<li>Both boats have the same wind, so what one gains is the boats: <strong>speed"
            "</strong>, and <strong>angle</strong>: height on a beat (pointing against footing), "
            "depth on a run (soaking against heading up).</li>"
            "<li>Gain is progress up the course on a beat and down it on a run, from the GPS "
            "tracks. The split into speed and angle is from each boat's average SOG and track "
            "angle.</li>"
            "<li><strong>Watch</strong> plays the stretch on that race's replay.</li></ul>"
        )
    ]
    for row in fa["pairs"]:
        a, b = row["a"], row["b"]
        na, nb = _pair_names(fa, a, b)
        lines = [x for k in ("upwind", "downwind") if (x := pair_sentence(fa, row, k))]
        body = f"<h2>{html.escape(na)} vs {html.escape(nb)}</h2><ul>"
        body += "".join(f"<li>{H.inline(x)}</li>" for x in lines) + "</ul>"
        body += f'<div class="chart" data-fleet="pair" data-a="{a}" data-b="{b}"></div>'
        rows = []
        for r in fa["races"]:
            for p in r.get("pairs", []):
                if (p["a"], p["b"]) != (a, b):
                    continue
                ba, bb = p["boats"][a], p["boats"][b]
                win = na if p["gain_m"] >= 0 else nb
                rel = p["rel"][0]
                where = (
                    f"{abs(rel['ahead_m'])} m {'ahead' if rel['ahead_m'] >= 0 else 'behind'}, "
                    f"{abs(rel['windward_m'])} m {'to windward' if rel['windward_m'] >= 0 else 'to leeward'}"
                )
                rows.append(
                    {
                        "Race": r["race"],
                        "Leg": f"{p['leg_name']}, {p['side']}",
                        "From": _mmss(p["t0"]),
                        "Length": _mmss(p["duration_s"]),
                        f"{na} at the start": where,
                        "Gained": f'<span class="gain">{html.escape(win)} {abs(p["gain_m"])} m</span>',
                        "Speed / angle": f"{p['gain_speed_m']:+} / {p['gain_angle_m']:+} m",
                        "SOG": f"{ba['sog']} / {bb['sog']} kt",
                        "Angle": f"{ba['angle']:.0f}° / {bb['angle']:.0f}°",
                        "Heel": f"{ba['heel']:.0f}° / {bb['heel']:.0f}°"
                        if ba["heel"] is not None and bb["heel"] is not None
                        else "–",
                        "": f'<a href="#replay-{r["stem"]}" data-watch="{r["stem"]}" '
                        f'data-t0="{p["t0"]}" data-t1="{p["t1"]}">Watch</a>',
                    }
                )
        cols = list(rows[0]) if rows else []
        body += (
            f'<p class="chart-hint">Pairs of numbers are {html.escape(na)} / {html.escape(nb)}; '
            f"speed and angle are from {html.escape(na)}'s side (+ = it gained).</p>"
            '<div class="scroll"><table><tr>'
            + "".join(f"<th>{html.escape(c)}</th>" for c in cols)
            + "</tr>"
            + "".join("<tr>" + "".join(f"<td>{rw[c]}</td>" for c in cols) + "</tr>" for rw in rows)
            + "</table></div>"
        )
        out.append(H.card(body))
    return "".join(out)


def boat_cards(reports_dir: Path, boats: list[dict]) -> str:
    out = []
    for b in boats:
        f = reports_dir / b["id"] / "debrief.md"
        if not f.exists():
            continue
        c = H.coach_card(f.read_text())
        if c:
            out.append(
                c.replace("<h2>Coach's summary</h2>", f"<h2>{html.escape(b['name'])}</h2>", 1)
            )
    return "".join(out)


def write_html(
    fa: dict,
    md: str,
    out: Path,
    title: str,
    debrief: str | None,
    reports_dir: Path,
    boats: list[dict],
    cdn: bool,
) -> Path:
    sections = md.split("\n## ")
    rest = ["## " + s for s in sections[1:]]
    results = [
        s
        for s in rest
        if s.startswith(("## Order among", "## Official results", "## Where the time"))
    ]
    per_race = [s for s in rest if s not in results and not s.startswith("## Side by side")]
    summary = (H.coach_card(debrief) if debrief else "") + H.card(H.md_to_html("\n".join(results)))
    pages = [
        H.page(
            "summary",
            "Summary",
            "Order among the tracked boats, and where each gained and lost against the others.",
            summary,
        )
    ]
    if debrief:
        pages.append(
            H.page(
                "debrief",
                "Fleet debrief",
                "How the boats compared, and what each can improve.",
                H.card(
                    '<p class="chart-hint">Hover any chart for the numbers behind it; drag to '
                    "zoom, double-click to reset.</p>" + debrief_html(debrief),
                    "debrief",
                ),
            )
        )
    race_body = ""
    for r, sec in zip(fa["races"], per_race, strict=False):
        race_body += H.card(
            f"<h2>{html.escape(r['race'])}</h2>"
            + '<p class="chart-hint">Hover the gaps chart for times. Press play on the replay, '
            "or drag the slider; the panel shows each boat at that moment.</p>"
            + _chart("gaps", r["stem"])
            + _replay(r["stem"])
            + H.md_to_html(sec.split("\n", 1)[1])
        )
    pages.append(
        H.page(
            "races",
            "Race by race",
            "Gaps at each mark, a replay, starts, legs and roundings.",
            race_body,
        )
    )
    if any(r.get("pairs") for r in fa["races"]):
        pages.append(
            H.page(
                "pairs",
                "Side by side",
                f"Every stretch where two tracked boats sailed within {PAIR_RADIUS_M} m of each "
                "other on the same leg and the same tack or gybe.",
                pairs_html(fa),
            )
        )
    cards = boat_cards(reports_dir, boats)
    if cards:
        pages.append(
            H.page(
                "boats",
                "Boat by boat",
                "Each boat's coach's summary; full reports are in each boat's own file.",
                cards,
            )
        )
    names = {
        "summary": "Summary",
        "debrief": "Fleet debrief",
        "races": "Race by race",
        "pairs": "Side by side",
        "boats": "Boat by boat",
    }
    nav = (
        '<nav class="pages">'
        + "".join(
            f'<a href="#{p}">{n}</a>' for p, n in names.items() if f'id="{p}"' in "".join(pages)
        )
        + "</nav>"
    )
    data = json.dumps(fa).replace("</", "<\\/")
    lib = (
        f'<script src="{H.PLOTLY_CDN}"></script>'
        if cdn
        else f"<script>{H.VENDOR.read_text()}</script>"
    )
    doc = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{H.CSS}{REPLAY_CSS}</style><script>{H.PAGE_JS}</script></head>"
        f'<body><main><header class="top"><h1>{html.escape(title)}</h1>'
        "<p>Fleet comparison from Njord data</p></header>"
        + nav
        + "".join(pages)
        + "<footer>Numbers from analyze.py and fleet.py. Speeds are over ground. "
        "Times are local to the event.</footer></main>"
        f'<script type="application/json" id="fleet-data">{data}</script>{lib}'
        f"<script>{FLEET_JS}</script></body></html>"
    )
    path = out / "fleet.html"
    path.write_text(doc)
    return path


# ---------------------------------------------------------------- cli


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--reports", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--title", default="Fleet")
    ap.add_argument("--tz")
    ap.add_argument("--html", action="store_true")
    ap.add_argument("--debrief", type=Path, help="fleet debrief (Markdown) for the HTML")
    ap.add_argument("--cdn", action="store_true", help="load Plotly online instead of inlining it")
    ap.add_argument("--official", type=Path, help="official results from yachtscoring.py")
    ap.add_argument(
        "--sail",
        action="append",
        default=[],
        metavar="BOAT=SAIL",
        help="which sail number each boat folder is, e.g. --sail mojo=1315 (repeat per boat)",
    )
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    boats = load_fleet(a.data, a.reports, a.tz)
    fa = fleet_analysis(boats)
    if a.official:
        add_official(fa, json.loads(a.official.read_text()), dict(x.split("=", 1) for x in a.sail))
    (a.out / "fleet.json").write_text(json.dumps(fa, indent=1))
    md = write_md(fa, a.out, a.title)
    print(md)
    if a.html or a.debrief:
        debrief = a.debrief.read_text() if a.debrief else None
        print(write_html(fa, md, a.out, a.title, debrief, a.reports, boats, a.cdn))


if __name__ == "__main__":
    main()
