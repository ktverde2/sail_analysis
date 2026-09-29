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
import ladder as LD
import current as CU
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
PAIR_MIDDLE_M = 100  # within this of the rhumb line counts as the middle of the course
# Wind shadow, roughly: within this distance and this angle of straight downwind of the other
# boat (the course axis stands in for the wind). A flag for "possibly in bad air", not a fact.
SHADOW_M = 75
SHADOW_DEG = 25
KT = 1852 / 3600  # m/s per knot
# Side-by-side charts: SOG, heel and trim are drawn as a rolling mean over this many seconds,
# with a band of +/-1 standard deviation of the 1 Hz readings in the same window
DETAIL_SMOOTH_S = 15


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
    if "Heading" in g:  # heading against the course axis (compass, so boats can differ)
        g["hdg_rel"] = A.adiff(A.upwind_axis(race) or 0.0, g.Heading.to_numpy())
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


def side_by_side(per: dict[str, pd.DataFrame], leg_types: list[str], ladders: list[dict]) -> list[dict]:
    """Stretches where two boats sailed within PAIR_RADIUS_M of each other on the same leg and
    tack/gybe, and what each gained. Gain is distance to sail to the mark (ladder.to_go): inside
    the laylines that's rungs, and past a layline the overstand counts against the boat. Split
    into the part from boat speed and the part from course (how much of each metre sailed brought
    the mark closer: height or depth, and not sailing past the layline). Both boats are in the
    same breeze, so the difference is the boats, not the wind.
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
                    out.append(_pair_row(a, b, pa.loc[s0:s1], pb.loc[s0:s1], leg_types, names, ladders))
    out.sort(key=lambda r: r["t0"])
    return out


def place_pairs(pairs, per, origins, targets, stem) -> list[dict]:
    """Number each stretch (R2-3 = race 2, third stretch) and say where on the leg it was:
    along the leg (first, middle or last third, from the last mark to the next) and across it
    (left, middle or right of the rhumb line, looking at the next mark)."""
    n = (re.findall(r"\d+", stem) or [stem])[0]
    for k, p in enumerate(pairs, 1):
        p["id"] = f"R{n}-{k}"
        mid = (p["t0"] + p["t1"]) // 2
        pts = [per[b].loc[mid] for b in (p["a"], p["b"]) if mid in per[b].index]
        j = p["leg"]
        if not pts or j >= len(targets) or j >= len(origins):
            continue
        px = float(np.mean([q.x for q in pts]))
        py = float(np.mean([q.y for q in pts]))
        (ax, ay), (bx, by) = origins[j], targets[j]
        L = math.hypot(bx - ax, by - ay) or 1.0
        ux, uy = (bx - ax) / L, (by - ay) / L
        frac = ((px - ax) * ux + (py - ay) * uy) / L
        xte = (px - ax) * uy - (py - ay) * ux  # + = right of the rhumb, looking at the mark
        along = "first third" if frac < 1 / 3 else "middle third" if frac < 2 / 3 else "last third"
        side = "right" if xte > PAIR_MIDDLE_M else "left" if xte < -PAIR_MIDDLE_M else "middle"
        p["where"] = {
            "along": along,
            "side": side,
            "frac": round(frac, 2),
            "xte_m": round(xte),
            "label": f"{side}, {along}",
        }
    return pairs


def where_summary(fa: dict) -> list[dict]:
    """Stretches counted by where on the leg they were, beats and runs apart."""
    rows = {}
    for r in fa["races"]:
        for p in r.get("pairs", []):
            w = p.get("where")
            if not w:
                continue
            k = (p["leg_type"], w["along"], w["side"])
            row = rows.setdefault(k, {"n": 0, "duration_s": 0})
            row["n"] += 1
            row["duration_s"] += p["duration_s"]
    order_a = ["first third", "middle third", "last third"]
    order_s = ["left", "middle", "right"]
    return [
        {"leg_type": t, "along": a, "side": sd, **rows[(t, a, sd)]}
        for t in ("upwind", "downwind")
        for a in order_a
        for sd in order_s
        if (t, a, sd) in rows
    ]


def _pair_row(a, b, pa, pb, leg_types, names, ladders) -> dict:
    leg = int(pa.leg.iloc[0])
    up = leg_types[leg] == "upwind"
    geo = ladders[leg]
    u = math.radians(geo["up_deg"])
    tx, ty = math.sin(u), math.cos(u)  # toward the mark, along the ladder
    dur = int(pa.index[-1] - pa.index[0])

    def boat(p):
        dx, dy = p.x.iloc[-1] - p.x.iloc[0], p.y.iloc[-1] - p.y.iloc[0]
        made = float(p.togo.iloc[0] - p.togo.iloc[-1])  # metres closer to the mark, to sail
        sailed = float(np.nansum(np.hypot(p.x.diff(), p.y.diff())))
        return {
            "sog": round(float(p.SOG.mean()), 2),
            "heel": round(float(p.Heel.abs().mean()), 1) if "Heel" in p else None,
            # angle between the track and the leg's axis (straight up or down the ladder)
            "angle": round(abs(float(A.adiff(geo["up_deg"], math.degrees(math.atan2(dx, dy))))), 1),
            "made_m": round(made),
            "sailed_m": round(sailed),
            # share of each metre sailed that brought the mark closer
            "made_pct": round(100 * made / sailed) if sailed else None,
            "to_go_m": [round(float(p.togo.iloc[0])), round(float(p.togo.iloc[-1]))],
            # distance to sail that came from being past a layline, at the end of the stretch
            "overstand_m": round(float(LD.overstand([p.x.iloc[-1]], [p.y.iloc[-1]], geo, geo["targets"])[0])),
        }

    ra, rb = boat(pa), boat(pb)
    gain = ra["made_m"] - rb["made_m"]
    eff = np.mean([r["made_m"] / r["sailed_m"] for r in (ra, rb) if r["sailed_m"]] or [1.0])
    speed = (ra["sog"] - rb["sog"]) * KT * eff * dur

    def rel(k):  # a relative to b: metres less to sail to the mark, and rungs further up the ladder
        dx, dy = pa.x.iloc[k] - pb.x.iloc[k], pa.y.iloc[k] - pb.y.iloc[k]
        return {
            "ahead_m": round(float(pb.togo.iloc[k] - pa.togo.iloc[k])),
            "windward_m": round(dx * tx + dy * ty),
        }

    detail, why = _pair_detail(a, b, pa, pb, geo, eff, ra, rb, up)
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
        "detail": detail,
        "why": why,
    }


def _smooth(v: pd.Series, n: int = 5) -> pd.Series:
    return v.rolling(n, center=True, min_periods=1).mean()


def _band(v: pd.Series, n: int = DETAIL_SMOOTH_S) -> tuple[pd.Series, pd.Series]:
    """Centred rolling mean and standard deviation: the smoothed line and how far the 1 Hz
    readings swing around it (waves, puffs, steering)."""
    r = v.rolling(n, center=True, min_periods=3)
    return r.mean(), r.std().fillna(0)


def _r(v, nd=1):
    return [None if pd.isna(x) else round(float(x), nd) for x in v]


def _pair_detail(a, b, pa, pb, geo, eff, ra, rb, up) -> tuple[dict, dict]:
    """Second by second through a side-by-side stretch, and the numbers behind the gain.

    detail: per boat SOG, track angle to the leg's axis, heading angle (compass), heel, trim and
    VMC (speed toward the mark: how fast the distance to sail shrank); the metres A gained on B
    so far toward the mark, split into speed and course the same way as the stretch totals;
    how much less A had to sail than B, and how many metres further up the ladder; and seconds
    in a wind shadow."""
    t0, t1 = int(pa.index[0]), int(pa.index[-1])
    idx = pd.RangeIndex(t0, t1 + 1)
    cols = ["x", "y", "SOG", "COG", "Heel", "Trim", "hdg_rel", "togo"]
    u = math.radians(geo["up_deg"])
    tx, ty = math.sin(u), math.cos(u)

    def fill(p):
        p = p[[c for c in cols if c in p]].reindex(idx)
        return p.interpolate(limit_direction="both")

    A_, B_ = fill(pa), fill(pb)

    def series(p):
        vx = (p.x.shift(-5) - p.x.shift(5)).bfill().ffill()  # 10 s of track, centred
        vy = (p.y.shift(-5) - p.y.shift(5)).bfill().ffill()
        d = np.degrees(np.arctan2(vx, vy))
        angle = np.abs(A.adiff(geo["up_deg"], d))
        out = {}
        out["sog"], out["sog_sd"] = _band(p.SOG)
        out |= {
            "angle": pd.Series(angle, index=p.index),
            "vmg": _smooth(-p.togo.diff().bfill() / KT),  # VMC: toward the mark, to sail
        }
        if "hdg_rel" in p:
            # compass heading against the leg's axis (hdg_rel is against the race axis, frame 0)
            h = np.abs(A.adiff(geo["up_deg"], p.hdg_rel.to_numpy() % 360))
            out["hdg_angle"] = _smooth(pd.Series(h, index=p.index))
        if "Heel" in p:
            out["heel"], out["heel_sd"] = _band(p.Heel.abs())
        if "Trim" in p:
            out["trim"], out["trim_sd"] = _band(p.Trim)
        return out

    sa, sb = series(A_), series(B_)
    # metres A gained on B toward the mark so far (distance to sail, B's minus A's, vs the start)
    lead = B_.togo - A_.togo
    gain = lead - lead.iloc[0]
    dsog = ((A_.SOG + A_.SOG.shift()) / 2 - (B_.SOG + B_.SOG.shift()) / 2).fillna(0)
    speed = (dsog * KT * eff).cumsum()
    # A relative to B: less to sail (+ = A closer) and rungs further up the ladder
    dx, dy = A_.x - B_.x, A_.y - B_.y
    ahead, windward = lead, dx * tx + dy * ty
    dist = np.hypot(dx, dy)
    # wind from +y: straight downwind of a boat is -y. B in A's shadow if B lies in that cone
    down = lambda ex, ey: np.degrees(
        np.arccos(np.clip(-ey / np.maximum(np.hypot(ex, ey), 1e-9), -1, 1))
    )
    b_in_a = (dist < SHADOW_M) & (down(-dx, -dy) < SHADOW_DEG)
    a_in_b = (dist < SHADOW_M) & (down(dx, dy) < SHADOW_DEG)
    shadow = np.where(b_in_a, 1, np.where(a_in_b, -1, 0))  # 1: B in A's wind, -1: A in B's

    detail = {
        "t": list(range(t0, t1 + 1)),
        "boats": {
            k: {m: _r(v, 2 if m in ("sog", "vmg", "sog_sd") else 1) for m, v in sv.items()}
            for k, sv in ((a, sa), (b, sb))
        },
        "gain": _r(gain),
        "gain_speed": _r(speed),
        "gain_angle": _r(gain - speed),
        "ahead": _r(ahead, 0),
        "windward": _r(windward, 0),
        "shadow": [int(v) for v in shadow],
    }

    def stats(sv, p):
        d = {
            m: (round(float(v.mean()), 2), round(float(v.std()), 2))
            for m, v in sv.items()
            if not m.endswith("_sd")
        }
        d["sailed_m"] = round(float(np.nansum(np.hypot(p.x.diff(), p.y.diff()))))
        if "hdg_angle" in sv:
            d["slip"] = round(float((sv["angle"] - sv["hdg_angle"]).mean()), 1)
        return d

    n = len(idx)
    total = float(gain.iloc[-1])
    win_sign = 1 if total >= 0 else -1
    blocks = [float(gain.iloc[min(i + 10, n - 1)] - gain.iloc[i]) for i in range(0, n - 1, 10)]
    steady = sum(1 for g in blocks if g * win_sign > 0) / max(len(blocks), 1)
    w = min(30, n - 1)
    run = (gain.shift(-w) - gain).dropna() * win_sign
    best_i = int(run.idxmax()) if len(run) else t0
    why = {
        "stats": {a: stats(sa, A_), b: stats(sb, B_)},
        "shadow_pct": {
            b: round(100 * float(b_in_a.mean())),  # b in a's wind
            a: round(100 * float(a_in_b.mean())),
        },
        "steady_pct": round(100 * steady),
        "best_30s": {
            "t0": best_i,
            "t1": best_i + w,
            "m": round(float(run.max()) if len(run) else 0),
        },
    }
    return detail, why


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
    ref_legs = course_xy = targets_xy = origins_xy = None
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
                for key in ("vmc_avg", "vmc_steady", "sog_steady", "vmg_steady", "tacking_angle", "heel_abs_std"):
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
            # where each leg starts for "where on the leg": after a mark with an offset, the offset
            els = [c for c in race.course if c["type"] in ("StartLine", "Mark", "Gate", "Offset")]
            leg_from = []
            for i, c in enumerate(els):
                if c["type"] == "Offset":
                    continue
                nxt = els[i + 1] if i + 1 < len(els) else None
                leg_from.append(_mid(nxt) if nxt and nxt["type"] == "Offset" else _mid(c))
            origins_xy = [[round(float(v), 1) for v in xy(*p)] for p in leg_from]
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
    ladders = race_ladders(per_second, course_xy, origins_xy, leg_types)
    return {
        "stem": stem,
        "race": race_name,
        "gun_local": gun_local,
        "marks": labels,
        "leg_types": leg_types,
        "leg_names": leg_names(leg_types),
        "course": course_xy,
        "targets": targets_xy,
        "ladders": ladders,
        "boats": boats,
        "pairs": place_pairs(
            side_by_side(per_second, leg_types, ladders), per_second, origins_xy, targets_xy, stem
        ),
    }


def race_ladders(per_second: dict, course_xy: list[dict], origins_xy: list, leg_types: list[str]) -> list[dict]:
    """Each leg's laylines and rungs from every tracked boat's GPS tracks (ladder.py), and each
    boat's distance to sail to the mark, second by second (per_second[boat].togo)."""
    els = [c for c in course_xy if c["type"] in ("Mark", "Gate", "FinishLine")]

    def dirs(j):
        d = []
        for g in per_second.values():
            on = g[(g.leg == j) & (g.since_leg >= PAIR_SETTLE_S)]
            d.append(LD.track_dirs(on.x.to_numpy(), on.y.to_numpy(), on.SOG.to_numpy()))
        return np.concatenate(d) if d else np.array([])

    out = LD.build(leg_types, [dirs(j) for j in range(len(leg_types))],
                   [els[j]["pts"] if j < len(els) else [] for j in range(len(leg_types))], origins_xy)
    for j, lad in enumerate(out):
        if not lad.get("targets"):
            continue
        for g in per_second.values():
            on = g.leg == j
            g.loc[on, "togo"] = LD.to_go(g.x[on].to_numpy(), g.y[on].to_numpy(), lad, lad["targets"])
    return out


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
    return {
        "races": races,
        "day": day,
        "pairs": pair_summary(races),
        "detail_smooth_s": DETAIL_SMOOTH_S,
    }


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
                "| Leg | Boat | Time | vs fastest | VMC to mark | SOG | Sailed / straight nm | Tacks | Gybes | "
                "% right of rhumb | Heel |"
            ),
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for j, lab in enumerate(r["marks"]):
            for k in ids:
                lg = bs[k]["legs"][j]
                L.append(
                    f"| {j + 1} {lg['type']} to {lab} | {bs[k]['name']} | {_mmss(lg['duration_s'])} | "
                    f"{_plus(lg['vs_best_s'])} | {lg.get('vmc_avg') if lg.get('vmc_avg') is not None else '–'} | {lg['sog_avg']} | "
                    f"{lg['sailed_nm']} / {lg['straight_nm']} | {lg['tacks']} | {lg['gybes']} | "
                    f"{lg.get('pct_right', '–')} | {lg['heel_abs_avg']}° |"
                )
        L += [
            "",
            "**Roundings (metres lost toward the marks, each boat against its own steady VMC)**",
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
            "*Time and vs fastest are what count. VMC to mark: the leg's distance to sail (at the boats' "
            "tacking or gybing angle) over its time. SOG explains it, over ground. % right of rhumb: share of the leg spent right of the line "
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
  const SMOOTH = FLEET.detail_smooth_s || 15;
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
    // one side-by-side stretch, second by second: the gain, then every channel for both boats
    stretch(el, r) {
      const p = r.pairs.find(x => x.id === el.dataset.id), d = p.detail;
      const A = FLEET.day[p.a], B = FLEET.day[p.b], up = p.leg_type === 'upwind';
      // time after the gun on a date axis, so ticks and the hover read m:ss (h:mm:ss past an hour)
      const pad = v => String(v).padStart(2, '0');
      const x = d.t.map(t => '1970-01-01 ' + pad(Math.floor(t / 3600)) + ':' + pad(Math.floor(t / 60) % 60) + ':' + pad(t % 60));
      const tf = d.t[d.t.length - 1] >= 3600 ? '%-H:%M:%S' : '%-M:%S';
      const ink = css('--ink'), ink2 = css('--ink2'), line = css('--line');
      const win = p.gain_m >= 0 ? A : B;
      const hov = () => undefined;  // no hover label: the panel beside the chart shows the values
      const tr = [];
      const trimChange = {};
      for (const id of [p.a, p.b]) {
        const s = d.boats[id].trim;
        if (!s) continue;
        const v = s.filter(t => t != null), m = v.reduce((a, b) => a + b, 0) / (v.length || 1);
        trimChange[id] = s.map(t => t == null ? null : Math.round((t - m) * 10) / 10);
      }
      const panels = [
        'Metres ' + A.name + ' gained on ' + B.name + ' toward the mark (distance to sail)',
        'SOG (kt): ' + SMOOTH + ' s average, shaded ±1 SD of the 1 s readings',
        'Pointing: angle to straight ' + (up ? 'up' : 'down') + ' the wind (°, lower = ' + (up ? 'higher' : 'deeper') + '; dotted = compass)',
        'Heel (°): ' + SMOOTH + ' s average, shaded ±1 SD',
        'Trim, fore-aft: change from each boat\'s own average (°), ' + SMOOTH + ' s average, shaded ±1 SD',
      ];
      tr.push({ x, y: d.gain, yaxis: 'y', mode: 'lines', name: 'gained', line: { color: col(win), width: 3 },
        hovertemplate: hov('gained', '') });
      tr.push({ x, y: d.gain_speed, yaxis: 'y', mode: 'lines', name: 'from speed', line: { color: ink2, width: 1.5, dash: 'dash' },
        hovertemplate: hov('from speed', '') });
      tr.push({ x, y: d.gain_angle, yaxis: 'y', mode: 'lines', name: 'from course', line: { color: ink2, width: 1.5, dash: 'dot' },
        hovertemplate: hov('from course', '') });
      for (const [id, bt] of [[p.a, A], [p.b, B]]) {
        const s = d.boats[id], c = col(bt);
        tr.push({ x, y: s.sog, yaxis: 'y2', mode: 'lines', name: bt.name, legendgroup: id, line: { color: c, width: 2 }, hovertemplate: hov(bt.name + ' SOG', ' kt') });
        tr.push({ x, y: s.angle, yaxis: 'y3', mode: 'lines', name: bt.name + ' track', legendgroup: id, showlegend: false, line: { color: c, width: 2 }, hovertemplate: hov(bt.name + ' track', '°') });
        if (s.hdg_angle) tr.push({ x, y: s.hdg_angle, yaxis: 'y3', mode: 'lines', name: bt.name + ' compass', legendgroup: id, showlegend: false,
          line: { color: c, width: 1, dash: 'dot' }, hovertemplate: hov(bt.name + ' compass', '°') });
        if (s.heel) tr.push({ x, y: s.heel, yaxis: 'y4', mode: 'lines', name: bt.name + ' heel', legendgroup: id, showlegend: false, line: { color: c, width: 2 }, hovertemplate: hov(bt.name + ' heel', '°') });
        if (s.trim) {  // each boat's trim sensor is zeroed differently: show change from its own average
          tr.push({ x, y: trimChange[id], yaxis: 'y5', mode: 'lines',
            name: bt.name + ' trim', legendgroup: id, showlegend: false, line: { color: c, width: 2 }, hovertemplate: hov(bt.name + ' trim', '°') });
        }
      }
      // shaded band: ±1 standard deviation of the 1 Hz readings around each smoothed line
      const rgba = (hex, a) => { const n = parseInt(hex.slice(1), 16); return 'rgba(' + (n >> 16) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')'; };
      const bands = [];
      for (const [id, bt] of [[p.a, A], [p.b, B]]) {
        const s = d.boats[id];
        for (const [key, axis, mid] of [['sog', 'y2', s.sog], ['heel', 'y4', s.heel], ['trim', 'y5', trimChange[id]]]) {
          const sd = s[key + '_sd'];
          if (!mid || !sd) continue;
          const hi = mid.map((v, k) => v == null ? null : Math.round((v + sd[k]) * 100) / 100);
          const lo = mid.map((v, k) => v == null ? null : Math.round((v - sd[k]) * 100) / 100);
          bands.push({ x, y: hi, yaxis: axis, mode: 'lines', line: { width: 0 }, showlegend: false, legendgroup: id });
          bands.push({ x, y: lo, yaxis: axis, mode: 'lines', line: { width: 0 }, fill: 'tonexty',
            fillcolor: rgba(col(bt), 0.18), showlegend: false, legendgroup: id });
        }
      }
      tr.unshift(...bands);  // behind the lines
      tr.forEach(t => { t.hoverinfo = 'none'; delete t.hovertemplate; });
      const narrow = el.clientWidth < 560;
      const lay = base(p.id + ': ' + A.name + ' and ' + B.name + ', second by second', narrow ? 860 : 960, el);
      const doms = [[0.80, 0.97], [0.60, 0.76], [0.40, 0.56], [0.20, 0.36], [0.0, 0.16]];
      const ax = n => ({ gridcolor: line, zerolinecolor: ink2, linecolor: line, domain: doms[n], anchor: 'x' });
      lay.yaxis = Object.assign(ax(0), { zeroline: true, ticksuffix: ' m' });
      for (let n = 1; n < 5; n++) lay['yaxis' + (n + 1)] = ax(n);
      lay.xaxis = { type: 'date', gridcolor: line, linecolor: line, anchor: 'y5', title: narrow ? '' : 'time after the gun',
        tickformat: tf, hoverformat: tf };
      lay.hovermode = 'x';
      lay.hoversubplots = 'axis';  // hovering any panel reads that second from all of them
      lay.margin = { l: 52, r: 12, t: narrow ? 56 : 72, b: narrow ? 110 : 44 };
      lay.legend = narrow
        ? { orientation: 'h', y: -0.06, yanchor: 'top', x: 0, font: { color: ink, size: 11 } }
        : { orientation: 'h', y: 1.0, yanchor: 'bottom', x: 0, font: { color: ink, size: 11 } };
      lay.annotations = panels.map((t, n) => ({ text: wrap(t, el), xref: 'paper', yref: 'paper', x: 0, y: doms[n][1],
        xanchor: 'left', yanchor: 'bottom', showarrow: false, font: { size: 11, color: ink2 }, align: 'left' }));
      // shade the seconds one boat sat in the other's wind shadow (in the colour of the boat making it)
      lay.shapes = [];
      let i = 0;
      while (i < d.shadow.length) {
        if (!d.shadow[i]) { i++; continue; }
        const v = d.shadow[i], j0 = i;
        while (i < d.shadow.length && d.shadow[i] === v) i++;
        lay.shapes.push({ type: 'rect', xref: 'x', yref: 'paper', x0: x[j0], x1: x[i - 1], y0: 0, y1: 0.97,
          fillcolor: col(v > 0 ? A : B), opacity: 0.08, line: { width: 0 } });
      }
      // a cursor line across all five panels, moved on hover
      const cur = lay.shapes.length;
      lay.shapes.push({ type: 'line', xref: 'x', yref: 'paper', x0: x[0], x1: x[0], y0: 0, y1: 0.97,
        line: { color: ink2, width: 1 }, visible: false });
      Plotly.newPlot(el, tr, lay, CONFIG);
      // ---- the readout panel beside the chart
      const panel = el.parentElement.querySelector('.st-panel');
      if (!panel) return;
      const aw = 'course';
      const f = (v, nd) => v == null ? '–' : Number(v).toFixed(nd);
      const signed = v => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(Math.round(v)) + ' m';
      const rows = [  // key, label (long|short), unit, decimals, better (+1 higher, -1 lower)
        ['sog', 'SOG|SOG', ' kt', 2, 1], ['vmg', 'VMC (to the mark)|VMC', ' kt', 2, 1], ['angle', 'Track angle|Track', '°', 1, -1],
        ['hdg_angle', 'Compass angle|Compass', '°', 1, 0], ['heel', 'Heel|Heel', '°', 1, 0], ['trim', 'Trim change|Trim Δ', '°', 1, 0],
      ];
      function boats(val) {
        return [[p.a, A], [p.b, B]].map(([id, bt], k) => {
          const other = k ? p.a : p.b;
          const items = rows.map(([key, label, unit, nd, better]) => {
            const v = val(id, key), o = val(other, key);
            if (v == null) return '';
            const best = better && o != null && Math.abs(v - o) >= Math.pow(10, -nd) && (better > 0 ? v > o : v < o);
            const [lg, sm] = label.split('|');
            return '<dt><span class="lg">' + lg + '</span><span class="sm">' + sm + '</span></dt><dd>' +
              (best ? '<b>' : '') + f(v, nd) + unit + (best ? '</b>' : '') + '</dd>';
          }).join('');
          return '<div class="rp-boat" style="--c:' + col(bt) + '"><div class="rp-name"><span class="rp-dot"></span>' +
            bt.name + '</div><dl>' + items + '</dl></div>';
        }).join('');
      }
      function gainLine(g, gs, ga) {
        if (Math.abs(g) < 0.5) return '<p class="st-gain">Level so far</p>';
        const lead = g >= 0 ? A : B, lag = g >= 0 ? B : A, sg = g >= 0 ? 1 : -1;
        return '<p class="st-gain"><b style="color:' + col(lead) + '">' + lead.name + ' ' + signed(Math.abs(g)) + '</b> toward the mark on ' + lag.name +
          '<br><span>' + signed(sg * gs) + ' from speed · ' + signed(sg * ga) + ' from ' + aw + '</span></p>';
      }
      function average() {
        const st = p.why.stats;
        const val = (id, key) => key === 'trim' ? null : (st[id][key] == null ? null : (Array.isArray(st[id][key]) ? st[id][key][0] : st[id][key]));
        panel.innerHTML = '<p class="st-head">Whole stretch (averages)</p>' +
          gainLine(p.gain_m, p.gain_speed_m, p.gain_angle_m) + boats(val) +
          '<p class="rp-note">Move along the chart to read any second. <b>Bold</b>: faster, or closer to straight ' +
          (up ? 'up' : 'down') + ' the wind.</p>';
      }
      function readAt(i) {
        const val = (id, key) => key === 'trim' ? (trimChange[id] ? trimChange[id][i] : null) : (d.boats[id][key] ? d.boats[id][key][i] : null);
        const ah = d.ahead[i], ww = d.windward[i];
        let pos = A.name + ' has ' + Math.abs(ah) + ' m ' + (ah >= 0 ? 'less' : 'more') + ' to sail than ' + B.name + ', ' +
          Math.abs(ww) + ' m ' + (ww >= 0 ? 'further up' : 'further down') + ' the ladder';
        if (d.shadow[i]) pos += '<br>' + (d.shadow[i] > 0 ? B.name + ' possibly in ' + A.name : A.name + ' possibly in ' + B.name) + '\'s wind shadow';
        panel.innerHTML = '<p class="st-head">At ' + mmss(d.t[i]) + ' after the gun</p>' +
          gainLine(d.gain[i], d.gain_speed[i], d.gain_angle[i]) + boats(val) + '<p class="st-pos">' + pos + '</p>';
      }
      average();
      el.on('plotly_hover', ev => {
        const pt = ev.points && ev.points[0];
        if (!pt) return;
        const i = pt.pointIndex;
        readAt(i);
        Plotly.relayout(el, { ['shapes[' + cur + '].x0']: x[i], ['shapes[' + cur + '].x1']: x[i], ['shapes[' + cur + '].visible']: true });
      });
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
  // ---------------------------------------------------------------- laylines and rungs
  function ladColors() { return { ink: css('--ink'), ink2: css('--ink2'), line: css('--line'), card: css('--card') }; }
  function ladderTraces(r, j, on, prefix) {
    const lad = r && r.ladders && r.ladders[j];
    return lad ? LADDER.traces(lad, ladColors(), (prefix || '') + r.leg_names[j], on) : [];
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
    let r, ids, T0, T1, t = 0, timer = null, stopAt = null, live = [], ladLeg = 0, ladAt = 0;
    function swapLadder(j) {  // show leg j's laylines and rungs, keeping the toggle's state
      if (j === ladLeg || j < 0 || !r.ladders || j >= r.ladders.length) return;
      const n = plot.data.length - ladAt, on = n > 0 && plot.data[ladAt].visible === true;
      if (n > 0) Plotly.deleteTraces(plot, [...Array(n).keys()].map(k => ladAt + k));
      Plotly.addTraces(plot, ladderTraces(r, j, on));
      ladLeg = j;
    }
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
      // Laylines and rungs of the leading boat's leg, last (so the live traces keep their places)
      ladLeg = 0; ladAt = traces.length;
      traces.push(...ladderTraces(r, 0, false));
      const pad = 60, lay = base(r.race + ' replay (upwind is up)', el.clientWidth < 560 ? 420 : 560, plot);
      lay.xaxis.range = [Math.min(...xs) - pad, Math.max(...xs) + pad];
      lay.yaxis.range = [Math.min(...ys) - pad, Math.max(...ys) + pad];
      lay.xaxis.scaleanchor = 'y'; lay.xaxis.title = 'm'; lay.yaxis.title = 'm';
      lay.margin.b = 40; lay.uirevision = r.stem; lay.showlegend = false;
      lay.updatemenus = LADDER.button(traces, ladColors());
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
        // distance to sail to the next mark: rungs inside the laylines, overstand counted
        const lad = j >= 0 && r.ladders ? r.ladders[j] : null;
        const dist = lad && lad.targets && lad.targets.length ? LADDER.toGo(s.x[k], s.y[k], lad)
          : tgt ? Math.hypot(tgt[0] - s.x[k], tgt[1] - s.y[k]) : null;
        const a0 = Math.max(0, k - 3), a1 = Math.min(s.x.length - 1, k + 2);
        const dx = s.x[a1] - s.x[a0];
        rows.push({ id, b, k, j, n, dist, left: dx < 0, done: j >= n,
          key: j >= n ? -1e9 + b.finish_s : j < 0 ? 1e9 : -j * 1e6 + dist });
      });
      Plotly.restyle(plot, { x: xs, y: ys }, live);
      const racing = rows.filter(x => x.k >= 0 && x.j >= 0).sort((p, q) => p.key - q.key);
      const lead = racing[0];
      if (lead && !lead.done) swapLadder(lead.j);
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
          if (x.dist != null) facts += '<dt>To sail</dt><dd>' + Math.round(x.dist) + ' m</dd>';
          if (x.j > 0 && x.j <= b.gaps_s.length && !x.done) {
            const g = b.gaps_s[x.j - 1];
            facts += '<dt>Last mark</dt><dd>' + (g ? '+' + mmss(g) : 'first') + '</dd>';
          }
          if (lead && x !== lead && !x.done && !lead.done && x.j === lead.j && x.j >= 0)
            facts += '<dt>Behind</dt><dd>' + Math.round(x.dist - lead.dist) + ' m</dd>';
        }
        return '<div class="rp-boat" style="--c:' + col(b) + '"><div class="rp-name"><span class="rp-dot"></span>' +
          b.name + head + '</div><div class="rp-leg">' + leg + '</div><dl>' + facts + '</dl></div>';
      }).join('') + '<p class="rp-note">Position among the tracked boats: legs done, then distance to sail to the next mark ' +
        '(up the ladder inside the laylines; past a layline, the straight line back). “Behind” is the extra distance to sail ' +
        'on the same leg. Laylines &amp; rungs (top left) follow the leading boat\'s leg.</p>';
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
  // ---------------------------------------------------------------- side-by-side map
  // Every side-by-side stretch drawn where it happened: both boats' tracks in their colours,
  // the boat that gained drawn thick. Filter by race (or all races overlaid, each in its own
  // course frame: start line at 0, upwind up) and by pair. Click a stretch to watch it.
  function pairmap(el) {
    const fixed = el.dataset.a ? [el.dataset.a, el.dataset.b] : null;
    const pairs = (FLEET.pairs || []).map(x => [x.a, x.b]);
    const name = id => FLEET.day[id].name;
    let ri = -1, li = 0, pi = fixed ? pairs.findIndex(x => x[0] === fixed[0] && x[1] === fixed[1]) : -1;
    const legs = [['Beats and runs', null], ['Beats', 'upwind'], ['Runs', 'downwind']];
    const row = (cls, labels, cur) => '<div class="rp-races ' + cls + '">' + labels.map((t, i) =>
      '<button type="button" data-i="' + (i + cur) + '">' + t + '</button>').join('') + '</div>';
    el.innerHTML =
      row('pm-races', ['All races', ...FLEET.races.map(r => r.race)], -1) +
      row('pm-legs', legs.map(x => x[0]), 0) +
      (fixed ? '' : row('pm-pairs', ['All pairs', ...pairs.map(x => name(x[0]) + ' vs ' + name(x[1]))], -1)) +
      '<div class="pm-body"><div class="pm-plot"></div><aside class="pm-list"></aside></div>';
    const plot = el.querySelector('.pm-plot'), list = el.querySelector('.pm-list');
    let shown = [];  // [{r, p, traces: [indices]}]
    function seg(s, t0, t1) {
      const i0 = Math.max(0, at(s.t, t0)), i1 = at(s.t, t1);
      return [s.x.slice(i0, i1 + 1), s.y.slice(i0, i1 + 1)];
    }
    function highlight(k) {  // k: index into shown, or -1 for none
      if (!shown.length) return;
      const idx = shown.flatMap(x => x.traces);
      const op = shown.flatMap((x, i) => x.traces.map(() => (k < 0 || i === k ? 1 : 0.15)));
      Plotly.restyle(plot, { opacity: op }, idx);
    }
    function draw() {
      const mark = (sel, v) => el.querySelectorAll(sel + ' button').forEach(b => b.setAttribute('aria-pressed', Number(b.dataset.i) === v));
      mark('.pm-races', ri); mark('.pm-legs', li); mark('.pm-pairs', pi);
      const races = ri < 0 ? FLEET.races : [FLEET.races[ri]];
      const labels = ri >= 0 && plot.clientWidth >= 420;
      const traces = [], ink2 = css('--ink2');
      for (const r of races) {
        const n = (r.race.match(/\d+/) || [''])[0];
        for (const c of r.course) {
          if (ri < 0 && c.type === 'FinishLine') continue;
          traces.push({ x: c.pts.map(p => p[0]), y: c.pts.map(p => p[1]), mode: c.pts.length > 1 ? 'lines+markers' : 'markers',
            marker: { size: ri < 0 ? 6 : 9, color: ink2, symbol: c.type === 'Offset' ? 'circle-open' : 'diamond' },
            line: { color: ink2, dash: 'dot', width: 1 }, opacity: ri < 0 ? 0.45 : 1,
            hovertext: (ri < 0 ? 'Race ' + n + ' ' : '') + c.type.replace('Line', ' line'), hoverinfo: 'text', showlegend: false });
        }
        if (ri >= 0) for (const id of IDS.filter(id => r.boats[id])) {
          const b = r.boats[id];
          traces.push({ x: b.series.x, y: b.series.y, mode: 'lines', line: { color: col(b), width: 1 }, opacity: 0.2,
            hoverinfo: 'skip', showlegend: false });
        }
      }
      shown = [];
      for (const r of races) for (const p of r.pairs || []) {
        if (pi >= 0 && !(p.a === pairs[pi][0] && p.b === pairs[pi][1])) continue;
        if (legs[li][1] && p.leg_type !== legs[li][1]) continue;
        const win = p.gain_m >= 0 ? p.a : p.b, lose = win === p.a ? p.b : p.a;
        const where = p.where ? p.where.label + ' of the ' + (p.leg_type === 'upwind' ? 'beat' : 'run') : '';
        const tip = '<b>' + p.id + '</b> · ' + r.race + ', ' + p.leg_name + ', ' + p.side +
          '<br>' + mmss(p.t0) + '–' + mmss(p.t1) + ' after the gun (' + mmss(p.duration_s) + ')' +
          (where ? '<br>' + where : '') +
          '<br><b>' + name(win) + ' gained ' + Math.abs(p.gain_m) + ' m</b> toward the mark on ' + name(lose) +
          '<br>SOG ' + name(p.a) + ' ' + p.boats[p.a].sog + ' · ' + name(p.b) + ' ' + p.boats[p.b].sog + ' kt' +
          '<br>angle to the wind ' + name(p.a) + ' ' + Math.round(p.boats[p.a].angle) + '° · ' + name(p.b) + ' ' + Math.round(p.boats[p.b].angle) + '°' +
          '<br><i>Click for why, and to watch it</i>';
        const item = { r, p, win, lose, where, traces: [] };
        let mx = 0, my = 0, k = 0;
        for (const id of [lose, win]) {
          const [xs, ys] = seg(r.boats[id].series, p.t0, p.t1);
          xs.forEach((v, i) => { mx += v; my += ys[i]; k++; });
          item.traces.push(traces.length);
          traces.push({ x: xs, y: ys, mode: 'lines', line: { color: col(r.boats[id]), width: id === win ? 6 : 2.5,
            dash: p.leg_type === 'upwind' ? 'solid' : 'dot' },
            hovertext: tip, hoverinfo: 'text', showlegend: false, customdata: xs.map(() => [r.stem, p.t0, p.t1, p.id]) });
        }
        if (labels && k) {
          item.traces.push(traces.length);
          traces.push({ x: [mx / k], y: [my / k], mode: 'text', text: [p.id.split('-')[1]], textposition: 'middle right',
            textfont: { size: 11, color: ink2 }, hovertext: tip, hoverinfo: 'text', showlegend: false,
            customdata: [[r.stem, p.t0, p.t1, p.id]] });
        }
        shown.push(item);
      }
      // laylines and rungs for every race and leg with a stretch in view (hidden until toggled)
      // one ladder per mark (Beat 1 and Beat 2 go to the same one)
      const legsIn = [...new Set(shown.map(x => x.r.stem + '|' + x.p.leg))], drawn = [];
      for (const k of legsIn) {
        const [stem, leg] = k.split('|'), r = FLEET.races.find(q => q.stem === stem);
        const lad = r.ladders && r.ladders[Number(leg)];
        if (!lad || !lad.targets || !lad.targets.length) continue;
        const cx = lad.targets.reduce((a, p) => a + p[0], 0) / lad.targets.length;
        const cy = lad.targets.reduce((a, p) => a + p[1], 0) / lad.targets.length;
        if (drawn.some(([x, y]) => Math.hypot(x - cx, y - cy) < 30)) continue;
        drawn.push([cx, cy]);
        traces.push(...ladderTraces(r, Number(leg), false, ri < 0 ? r.race + ' ' : ''));
      }
      for (const id of IDS) traces.push({ x: [null], y: [null], mode: 'lines', name: name(id),
        line: { color: col(FLEET.day[id]), width: 4 }, hoverinfo: 'skip' });
      traces.push({ x: [null], y: [null], mode: 'lines', name: 'run (dotted)', line: { color: ink2, width: 3, dash: 'dot' }, hoverinfo: 'skip' });
      const title = (ri < 0 ? 'All races' : FLEET.races[ri].race) + ': ' + shown.length + ' stretches side by side';
      const narrow = el.clientWidth < 560;
      const lay = base(title, narrow ? 520 : 640, plot);
      lay.xaxis.scaleanchor = 'y'; lay.xaxis.title = 'm, looking upwind (left −, right +)'; lay.yaxis.title = 'm up the course';
      lay.hovermode = 'closest'; lay.uirevision = 'pm' + ri + '-' + pi + '-' + li;
      lay.margin.r = 8; lay.legend.y = narrow ? -0.18 : -0.12;
      lay.updatemenus = LADDER.button(traces, ladColors());
      Plotly.react(plot, traces, lay, CONFIG);
      list.innerHTML = shown.length ? shown.map((x, i) =>
        '<button type="button" class="pm-row" data-k="' + i + '" style="--w:' + col(FLEET.day[x.win]) + ';--l:' + col(FLEET.day[x.lose]) + '">' +
        '<span><span class="pm-id">' + x.p.id + '</span> <span class="pm-leg">· ' + x.r.race + ', ' + x.p.leg_name + ', ' + x.p.side.split(' ')[0] + '</span></span>' +
        (x.where ? '<span class="pm-where">' + x.where + '</span>' : '') +
        '<span class="pm-gain"><b>' + name(x.win) + ' +' + Math.abs(x.p.gain_m) + ' m</b> on ' + name(x.lose) + ' · ' + mmss(x.p.duration_s) + '</span>' +
        '</button>').join('') : '<p class="rp-note">No stretches for this choice.</p>';
      list.querySelectorAll('.pm-row').forEach(bt => {
        const k = Number(bt.dataset.k), x = shown[k];
        bt.addEventListener('mouseenter', () => highlight(k));
        bt.addEventListener('focus', () => highlight(k));
        bt.addEventListener('mouseleave', () => highlight(-1));
        bt.addEventListener('blur', () => highlight(-1));
        bt.addEventListener('click', () => { location.hash = '#stretch-' + x.p.id; });
      });
    }
    el.querySelectorAll('.pm-races button').forEach(b => b.addEventListener('click', () => { ri = Number(b.dataset.i); draw(); }));
    el.querySelectorAll('.pm-legs button').forEach(b => b.addEventListener('click', () => { li = Number(b.dataset.i); draw(); }));
    el.querySelectorAll('.pm-pairs button').forEach(b => b.addEventListener('click', () => { pi = Number(b.dataset.i); draw(); }));
    draw();
    plot.on('plotly_click', ev => {
      const d = ev.points && ev.points[0] && ev.points[0].customdata;
      if (d) location.hash = '#stretch-' + d[3];
    });
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
    root.querySelectorAll('.pairmap:not([data-done])').forEach(el => {
      el.dataset.done = '1';
      try { pairmap(el); } catch (e) { el.textContent = 'Map failed: ' + e.message; }
    });
    root.querySelectorAll('.replay:not([data-done])').forEach(el => {
      el.dataset.done = '1';
      try { replay(el); } catch (e) { el.textContent = 'Replay failed: ' + e.message; }
    });
    root.querySelectorAll('.chart[data-fleet]:not([data-done])').forEach(el => {
      if (el.closest('details:not([open])')) return;  // drawn when its section opens
      el.dataset.done = '1';
      const r = el.dataset.race ? FLEET.races.find(x => x.stem === el.dataset.race) : null;
      try { CHARTS[el.dataset.fleet](el, r); } catch (e) { el.textContent = 'Chart failed: ' + e.message; }
    });
  }
  window.renderCharts = render;
  // a stretch's chart is drawn when its section opens; #stretch-R2-9 links open the section
  document.addEventListener('toggle', e => {
    if (e.target.matches && e.target.matches('details.stretch') && e.target.open) render(e.target);
  }, true);
  function openStretch() {
    const id = (location.hash || '').slice(1);
    if (!id.startsWith('stretch-')) return;
    setTimeout(() => {
      const d = document.getElementById(id);
      if (d) { d.open = true; d.scrollIntoView({ block: 'start' }); }
    }, 30);
  }
  window.addEventListener('hashchange', openStretch);
  document.addEventListener('DOMContentLoaded', openStretch);
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
.pm-legs button, .pm-pairs button { font-size: 0.82rem; }
.pm-body { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; }
@media (min-width: 760px) { .pm-body { grid-template-columns: minmax(0, 1fr) 270px; } }
.pm-list { display: flex; flex-direction: column; gap: 6px; max-height: 640px; overflow-y: auto; }
@media (max-width: 759px) { .pm-list { max-height: 360px; } }
.pm-row { font: inherit; font-size: 0.82rem; text-align: left; color: var(--ink); background: var(--card);
  border: 1px solid var(--line); border-left: 5px solid var(--w); border-radius: 8px; padding: 5px 9px;
  cursor: pointer; display: grid; gap: 1px; }
.pm-row:hover, .pm-row:focus-visible { border-color: var(--w); }
.pm-id { font-weight: 600; font-variant-numeric: tabular-nums; }
.pm-leg, .pm-where { color: var(--ink2); }
.pm-gain { font-variant-numeric: tabular-nums; }
td.where { text-align: left; }
details.stretch { border: 1px solid var(--line); border-radius: 10px; margin: 8px 0; background: var(--card); }
details.stretch > summary { padding: 8px 12px; color: var(--ink); line-height: 1.4; }
details.stretch[open] > summary { border-bottom: 1px solid var(--line); }
.stretch-body { padding: 4px 12px 8px; }
p.why { margin: 8px 0; }
.stretch-body td:not(:first-child), .stretch-body th:not(:first-child) { text-align: right; }
.st-body { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; }
.st-panel { font-size: 0.84rem; display: grid; gap: 6px; align-content: start; }
.st-panel p { margin: 0; }
.st-head { font-weight: 600; font-variant-numeric: tabular-nums; }
.st-gain span, .st-pos { color: var(--ink2); }
.st-gain, .st-pos { font-variant-numeric: tabular-nums; }
.st-panel dd { white-space: nowrap; }
.st-panel .sm { display: none; }
@media (min-width: 760px) {
  .st-body { grid-template-columns: minmax(0, 1fr) 230px; }
  .st-panel { position: sticky; top: 8px; align-self: start; margin-top: 8px; }
}
@media (max-width: 759px) {
  .st-panel { order: -1; position: sticky; top: 0; z-index: 2; background: var(--card); padding: 6px 0;
    grid-template-columns: 1fr 1fr; font-size: 0.78rem; border-bottom: 1px solid var(--line); }
  .st-panel .st-head, .st-panel .st-gain, .st-panel .st-pos, .st-panel .rp-note { grid-column: 1 / -1; }
  .st-panel .rp-boat { padding: 4px 7px; }
  .st-panel .lg { display: none; } .st-panel .sm { display: inline; }
  .st-panel .rp-boat dl { gap: 0 6px; }
}
"""

# Day-level charts a fleet debrief can place with a line of its own: [[chart:split]].
# [[chart:replay]] puts the race replay there, with a button for each race; [[chart:pairmap]]
# the map of where the boats sailed side by side.
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
        elif m.group(1) == "pairmap":
            out.append('<div class="pairmap"></div>')
        elif m.group(1) in TAKEAWAY_CHARTS:
            out.append(_chart(m.group(1)))
    out.append(H.md_to_html("\n".join(chunk)))
    return "".join(out)


def _pair_names(fa: dict, a: str, b: str) -> tuple[str, str]:
    return fa["day"][a]["name"], fa["day"][b]["name"]


def pair_sentence(fa: dict, row: dict, kind: str) -> str | None:
    """'25 min side by side upwind: 1044 gained 83 m toward the mark on Mojo (3.3 m a minute):
    164 m from height, 81 m lost on speed.'"""
    t = row[kind]
    if not t.get("duration_s"):
        return None
    na, nb = _pair_names(fa, row["a"], row["b"])
    win, lose, sgn = (na, nb, 1) if t["gain_m"] >= 0 else (nb, na, -1)
    sp, an = sgn * t["gain_speed_m"], sgn * t["gain_angle_m"]

    def part(v, what):
        return f"{abs(v)} m {'from' if v >= 0 else 'lost on'} {what}"

    angle = "course"  # height or depth, and staying inside the laylines
    return (
        f"{round(t['duration_s'] / 60)} min side by side {kind} ({t['n']} stretches): "
        f"**{win} gained {abs(t['gain_m'])} m toward the mark on {lose}** ({abs(t['gain_m_per_min'])} m a minute): "
        f"{part(sp, 'speed')}, {part(an, angle)}."
    )


def why_sentence(fa: dict, p: dict) -> str:
    """Why one boat gained in a side-by-side stretch, in plain words from its numbers."""
    a, b = p["a"], p["b"]
    win, lose = (a, b) if p["gain_m"] >= 0 else (b, a)
    nw, nl = _pair_names(fa, win, lose)
    g = abs(p["gain_m"])
    if g < 8:  # a few metres over minutes is within what the tracks and the split can resolve
        return f"About level: {nw} gained {g} m toward the mark on {nl}, too little to call." + _overstood(fa, p)
    sg = 1 if win == a else -1
    sp, an = sg * p["gain_speed_m"], sg * p["gain_angle_m"]
    bw, bl = p["boats"][win], p["boats"][lose]
    dsog = bw["sog"] - bl["sog"]
    dang = bl["angle"] - bw["angle"]  # + = winner's track closer to the leg's wind axis
    up = p["leg_type"] == "upwind"
    word = "course"  # height or depth, and staying inside the laylines

    def part(v, what, how):
        return f"{v} m from {what} ({how})" if v >= 0 else f"{-v} m lost on {what} ({how})"

    speed = part(sp, "speed", f"{abs(dsog):.2f} kt {'faster' if dsog >= 0 else 'slower'}")
    angle = part(
        an,
        word,
        f"track {abs(dang):.1f}° {'closer to' if dang >= 0 else 'further from'} "
        f"straight {'up' if up else 'down'} the wind"
        if abs(dang) >= 0.1
        else "the same track angle",
    )
    big, small = (speed, angle) if abs(sp) >= abs(an) else (angle, speed)
    small_v = an if abs(sp) >= abs(an) else sp
    if abs(small_v) < 3:  # too small to call either way
        level = word if abs(sp) >= abs(an) else "speed"
        out = f"{nw} gained {g} m toward the mark on {nl}: {big}; {level} about level"
    else:
        link = " and " if min(sp, an) >= 0 else ", against "
        out = f"{nw} gained {g} m toward the mark on {nl}: {big}{link}{small}"
    out += "." + _overstood(fa, p)
    sw, sl = p["why"]["stats"][win], p["why"]["stats"][lose]
    if "heel" in sw and "heel" in sl and abs(dh := sw["heel"][0] - sl["heel"][0]) >= 1.5:
        out += f" {nw} carried {abs(dh):.1f}° {'more' if dh > 0 else 'less'} heel."
    w = p["why"]
    best = w["best_30s"]
    if w["steady_pct"] >= 70:
        out += f" The gain came steadily ({w['steady_pct']}% of 10 s spells)."
    elif best["m"] >= 0.5 * g:
        out += (
            f" Most of it came in a burst: {best['m']} m between {_mmss(best['t0'])} and "
            f"{_mmss(best['t1'])}."
        )
    else:
        out += f" It came and went (gained in {w['steady_pct']}% of 10 s spells)."
    for boat, pct in w["shadow_pct"].items():
        if pct >= 20:
            other = b if boat == a else a
            nb_, no_ = _pair_names(fa, boat, other)
            out += f" {nb_} spent {pct}% of it in {no_}'s wind shadow, possibly in bad air."
    return out


def _overstood(fa: dict, p: dict) -> str:
    """' Mojo finished it 18 m past the layline.' for any boat that did (10 m or more)."""
    out = ""
    for bid in (p["a"], p["b"]):
        o = p["boats"][bid].get("overstand_m") or 0
        if o >= 10:
            out += f" {_pair_names(fa, bid, bid)[0]} finished it {o} m past the layline."
    return out


def pairs_md(fa: dict) -> list[str]:
    if not fa.get("pairs"):
        return []
    L = [
        f"## Side by side (within {PAIR_RADIUS_M} m, same leg, same tack or gybe)",
        "",
        (
            "Both boats had the same wind, so the gain is the boats: speed, and course (height on "
            "a beat, depth on a run, and not sailing past the laylines). Gain is distance to sail "
            "to the mark: inside the laylines that's rungs up the ladder, and past a layline the "
            "overstand counts against the boat."
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
            "| # | Race | Leg | Tack | Where | From | Length | Boats | Apart (m) | Gain (m) | "
            "Speed / course (m) | SOG (kt) | Angle to wind (°) | Heel (°) |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in fa["races"]:
        for p in r.get("pairs", []):
            na, nb = _pair_names(fa, p["a"], p["b"])
            ba, bb = p["boats"][p["a"]], p["boats"][p["b"]]
            win = na if p["gain_m"] >= 0 else nb
            L.append(
                f"| {p['id']} | {r['race']} | {p['leg_name']} | {p['side']} | "
                f"{p['where']['label'] if p.get('where') else '–'} | {_mmss(p['t0'])} | "
                f"{_mmss(p['duration_s'])} | {na} / {nb} | {p['apart_m'][0]}→{p['apart_m'][1]} | "
                f"{win} +{abs(p['gain_m'])} | {p['gain_speed_m']:+} / {p['gain_angle_m']:+} | "
                f"{ba['sog']} / {bb['sog']} | {ba['angle']} / {bb['angle']} | "
                f"{ba['heel']} / {bb['heel']} |"
            )
    L += [
        "",
        (
            "*Gain, speed and course are from the first boat's side (+ = it gained toward the mark). "
            "Course is height or depth plus not overstanding: how much of each metre sailed brought "
            "the mark closer. Angle to wind is the track against the leg's wind axis (from the "
            "boats' tacks or gybes); compare the two boats on the same stretch.*"
        ),
        "",
    ]
    rows = where_summary(fa)
    if rows:
        L += [
            "**Where the stretches were** (count, minutes)",
            "",
            "| Leg | Along | Left | Middle | Right |",
            "|---|---|---|---|---|",
        ]
        for t in ("upwind", "downwind"):
            for al in ("first third", "middle third", "last third"):
                cells = {x["side"]: x for x in rows if x["leg_type"] == t and x["along"] == al}
                if cells:
                    L.append(
                        f"| {'Beat' if t == 'upwind' else 'Run'} | {al} | "
                        + " | ".join(
                            f"{c['n']} ({round(c['duration_s'] / 60)} min)"
                            if (c := cells.get(sd))
                            else "–"
                            for sd in ("left", "middle", "right")
                        )
                        + " |"
                    )
        L += ["", f"*{WHERE_NOTE}*", ""]
    L += ["### Why, stretch by stretch", ""]
    for r in fa["races"]:
        for p in r.get("pairs", []):
            L.append(
                f"- **{p['id']}** ({r['race']}, {p['leg_name']}, {p['side']}): {why_sentence(fa, p)}"
            )
    L.append("")
    return L


WHERE_NOTE = (
    "Along the leg: thirds from the last mark (the offset, after a windward mark) to the next. "
    f"Across it: within {PAIR_MIDDLE_M} m of the rhumb line is the middle; left and right are "
    "looking at the next mark (upwind on a beat, downwind on a run)."
)


def where_html(fa: dict) -> str:
    rows = where_summary(fa)
    if not rows:
        return ""
    out = ["<h3>How many stretches, and where</h3>"]
    for t, title in (("upwind", "Beats"), ("downwind", "Runs")):
        cells = {(x["along"], x["side"]): x for x in rows if x["leg_type"] == t}
        if not cells:
            continue
        head = "".join(f"<th>{s.capitalize()}</th>" for s in ("left", "middle", "right"))
        body = ""
        for a in ("first third", "middle third", "last third"):
            tds = "".join(
                f"<td>{c['n']} ({round(c['duration_s'] / 60)} min)</td>"
                if (c := cells.get((a, sd)))
                else "<td>–</td>"
                for sd in ("left", "middle", "right")
            )
            body += f"<tr><td>{a.capitalize()}</td>{tds}</tr>"
        out.append(
            f'<div class="scroll"><table><tr><th>{title}</th>{head}</tr>{body}</table></div>'
        )
    out.append(f'<p class="chart-hint">Stretches (minutes side by side). {WHERE_NOTE}</p>')
    return "".join(out)


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
            "</strong>, and <strong>course</strong>: height on a beat (pointing against footing), "
            "depth on a run (soaking against heading up), and not sailing past the laylines.</li>"
            "<li>Gain is distance to sail to the mark, from the GPS tracks: inside the laylines "
            "that's rungs up the ladder (sailing lower loses rungs), and past a layline the "
            "straight line back to the mark, so overstanding counts against the boat. Laylines "
            "and rungs come from the boats' own tacking and gybing angles on that leg. The split "
            "into speed and angle is from each boat's average SOG and how much of each metre it "
            "sailed brought the mark closer.</li>"
            "<li><strong>Watch</strong> plays the stretch on that race's replay.</li></ul>"
        ),
        H.card(
            "<h2>Where on the course</h2>"
            '<p class="chart-hint">Each stretch is drawn where it happened, both boats in their '
            "colours; the thick line is the boat that gained. Pick a race, or all races overlaid "
            "(each in its own course frame: start line at 0, upwind up). Hover for the numbers; "
            "click a stretch to watch it on the replay.</p>"
            '<div class="pairmap"></div>' + where_html(fa)
        ),
    ]
    for row in fa["pairs"]:
        a, b = row["a"], row["b"]
        na, nb = _pair_names(fa, a, b)
        lines = [x for k in ("upwind", "downwind") if (x := pair_sentence(fa, row, k))]
        body = f"<h2>{html.escape(na)} vs {html.escape(nb)}</h2><ul>"
        body += "".join(f"<li>{H.inline(x)}</li>" for x in lines) + "</ul>"
        body += f'<div class="chart" data-fleet="pair" data-a="{a}" data-b="{b}"></div>'
        body += f'<div class="pairmap" data-a="{a}" data-b="{b}"></div>'
        rows = []
        for r in fa["races"]:
            for p in r.get("pairs", []):
                if (p["a"], p["b"]) != (a, b):
                    continue
                ba, bb = p["boats"][a], p["boats"][b]
                win = na if p["gain_m"] >= 0 else nb
                rel = p["rel"][0]
                where = (
                    f"{abs(rel['ahead_m'])} m {'less' if rel['ahead_m'] >= 0 else 'more'} to sail, "
                    f"{abs(rel['windward_m'])} m {'further up' if rel['windward_m'] >= 0 else 'further down'} the ladder"
                )
                rows.append(
                    {
                        "#": p["id"],
                        "Race": r["race"],
                        "Leg": f"{p['leg_name']}, {p['side']}",
                        "Where": f'<span class="where">{p["where"]["label"]}</span>'
                        if p.get("where")
                        else "–",
                        "From": _mmss(p["t0"]),
                        "Length": _mmss(p["duration_s"]),
                        f"{na} at the start": where,
                        "Gained": f'<span class="gain">{html.escape(win)} {abs(p["gain_m"])} m</span>',
                        "Speed / course": f"{p['gain_speed_m']:+} / {p['gain_angle_m']:+} m",
                        "SOG": f"{ba['sog']} / {bb['sog']} kt",
                        "Angle to wind": f"{ba['angle']:.0f}° / {bb['angle']:.0f}°",
                        "Heel": f"{ba['heel']:.0f}° / {bb['heel']:.0f}°"
                        if ba["heel"] is not None and bb["heel"] is not None
                        else "–",
                        "": f'<a href="#stretch-{p["id"]}">Details</a> · '
                        + _watch_link(r["stem"], p),
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
        body += (
            '<h3>Stretch by stretch: why the gain</h3><p class="chart-hint">Open a stretch for '
            "the reason in numbers, a table of both boats, and every channel second by second. "
            "Pointing is the track (GPS) angle to straight up or down the wind; the compass "
            "heading is dotted, and differs between boats' sensors.</p>"
            + "".join(stretch_html(fa, r, p) for r, p in stretches(fa, a, b))
        )
        out.append(H.card(body))
    return "".join(out)


def _watch_link(stem: str, p: dict, text: str = "Watch") -> str:
    return (
        f'<a href="#replay-{stem}" data-watch="{stem}" data-t0="{p["t0"]}" '
        f'data-t1="{p["t1"]}">{text}</a>'
    )


def stretches(fa: dict, a: str, b: str):
    return [(r, p) for r in fa["races"] for p in r.get("pairs", []) if (p["a"], p["b"]) == (a, b)]


STRETCH_ROWS = [  # key, label, unit, decimals, better (+1 higher, -1 lower, 0 neither)
    ("sog", "SOG", "kt", 2, 1),
    ("vmg", "VMC: speed toward the mark", "kt", 2, 1),
    ("angle", "Track angle to the wind axis", "°", 1, -1),
    ("hdg_angle", "Heading angle to the wind axis*", "°", 1, 0),
    ("slip", "Slip (track − heading)*", "°", 1, 0),
    ("heel", "Heel", "°", 1, 0),
    ("trim", "Trim, fore-aft*", "°", 1, 0),
    ("sailed_m", "Distance sailed", "m", 0, 0),
]


def stretch_html(fa: dict, r: dict, p: dict) -> str:
    """One collapsible stretch: the why sentence, a table of both boats, and the chart."""
    a, b = p["a"], p["b"]
    na, nb = _pair_names(fa, a, b)
    win = na if p["gain_m"] >= 0 else nb
    lose = nb if win == na else na
    where = f" · {p['where']['label']}" if p.get("where") else ""
    head = (
        f"<strong>{p['id']}</strong> · {html.escape(r['race'])}, {p['leg_name']}, {p['side']}"
        f'{where} · <span class="gain">{html.escape(win)} +{abs(p["gain_m"])} m</span> on '
        f"{html.escape(lose)} · {_mmss(p['duration_s'])}"
    )
    st = p["why"]["stats"]
    rows = ""
    for key, label, unit, nd, better in STRETCH_ROWS:
        va, vb = st[a].get(key), st[b].get(key)
        if va is None or vb is None:
            continue
        va, vb = (va[0], vb[0]) if isinstance(va, tuple | list) else (va, vb)
        diff = va - vb
        mark = ""
        if better and round(diff, nd):
            mark = na if (diff > 0) == (better > 0) else nb
        rows += (
            f"<tr><td>{label}</td><td>{va:.{nd}f} {unit}</td><td>{vb:.{nd}f} {unit}</td>"
            f"<td>{'+' if diff > 0 else ''}{diff:.{nd}f}</td><td>{html.escape(mark)}</td></tr>"
        )
    sh = p["why"]["shadow_pct"]
    rows += (
        f"<tr><td>In the other's wind shadow</td><td>{sh[a]}%</td><td>{sh[b]}%</td>"
        "<td></td><td></td></tr>"
    )
    rel0, rel1 = p["rel"]

    def rel(x):
        return (
            f"{abs(x['ahead_m'])} m {'less' if x['ahead_m'] >= 0 else 'more'} to sail, "
            f"{abs(x['windward_m'])} m {'further up' if x['windward_m'] >= 0 else 'further down'} the ladder"
        )

    table = (
        '<div class="scroll"><table><tr><th></th>'
        f"<th>{html.escape(na)}</th><th>{html.escape(nb)}</th><th>Difference</th><th>Better</th>"
        f"</tr>{rows}</table></div>"
    )
    return (
        f'<details class="stretch" id="stretch-{p["id"]}"><summary>{head}</summary>'
        f'<div class="stretch-body"><p class="why">{H.inline(why_sentence(fa, p))}</p>'
        f'<p class="chart-hint">{html.escape(na)} was {rel(rel0)} of {html.escape(nb)} at the '
        f"start and {rel(rel1)} at the end. {_watch_link(r['stem'], p, 'Watch it on the replay')}"
        "</p>"
        f"{table}"
        '<p class="chart-hint">Averages over the stretch. * From each boat\'s own sensors, '
        "zeroed differently: compare how they change, not the two boats' values (trim differs "
        "by several degrees between these boats; heel on one tack can carry a degree or two). "
        "The track angle is from GPS and doesn't depend on them. Better: faster, or closer "
        "to straight up (beat) or down (run) the wind. Wind shadow: within "
        f"{SHADOW_M} m and {SHADOW_DEG}° of straight downwind of the other boat, taking the "
        "course axis as the wind.</p>"
        '<div class="st-body">'
        f'<div class="chart" data-fleet="stretch" data-race="{r["stem"]}" data-id="{p["id"]}">'
        '</div><aside class="st-panel" aria-live="polite"></aside></div></div></details>'
    )


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
    cur = CU.page_parts([reports_dir / b["id"] for b in boats], embedded=True)
    if cur:
        pages.append(H.page("current", "Current", CU.INTRO, cur[0]))
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
        "current": "Current",
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
        f"<script>{(Path(__file__).parent / 'ladder.js').read_text()}</script>"
        f"<script>{FLEET_JS}</script>{cur[1] if cur else ''}</body></html>"
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
