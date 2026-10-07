"""Laylines, ladder rungs and distance to sail to the mark, for any leg.

Everything is in a course frame in metres (x right, y up the course), the frame analyze.py's
plotdata and fleet.py's series use. A leg's geometry comes from the boats' own GPS tracks:

  up_deg    the direction the boats were making toward the mark (the wind axis on a beat, the
            downwind axis on a run), as a frame bearing (0 = up the page, clockwise +). It's the
            bisector of the two tacks' (or gybes') median track directions.
  half_deg  half the tacking (or gybing) angle over the ground: the laylines run from the mark
            at +-half_deg off the axis.

Distance to sail to the mark (to_go) is what's left to sail at those angles: inside the
laylines that depends only on the ladder rung (a / cos(half), a = distance up the ladder to
the mark), so sailing lower loses rungs; past a layline it's the straight line back to the mark,
so every metre of overstanding is counted. A gate or a line counts its nearest point.

Rungs are perpendicular to the axis; the ladder is the same measure as to_go inside the laylines.
"""

from __future__ import annotations

import math

import numpy as np

DEFAULT_HALF = {"upwind": 42.0, "downwind": 25.0}  # when the tracks don't show both sides
MIN_SIDE_S = 30  # seconds of steady sailing on each side to trust the tracks
MIN_SOG_KT = 2.0
STEADY_DEG = 25.0  # track direction within this of its side's median counts as steady
RUNG_M = 100
LINE_SAMPLES = 9  # points along a gate or line, for the nearest one


def _circ_median(deg: np.ndarray) -> float:
    """Median direction, taken around the circular mean (fine for a tight spread)."""
    m = math.degrees(math.atan2(np.sin(np.radians(deg)).mean(), np.cos(np.radians(deg)).mean()))
    rel = (deg - m + 180) % 360 - 180
    return (m + float(np.median(rel))) % 360


def track_dirs(x: np.ndarray, y: np.ndarray, sog: np.ndarray | None = None, span: int = 10) -> np.ndarray:
    """Frame bearing of travel over `span` samples, centred (NaN where too slow to tell)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    h = span // 2
    vx = np.full(len(x), np.nan)
    vy = np.full(len(x), np.nan)
    if len(x) > span:
        vx[h:-h] = x[span:] - x[:-span]
        vy[h:-h] = y[span:] - y[:-span]
    d = np.degrees(np.arctan2(vx, vy)) % 360
    moving = np.hypot(vx, vy) > 1.0 * span  # at least ~2 kt over the window
    if sog is not None:
        moving &= np.asarray(sog, float) >= MIN_SOG_KT
    return np.where(moving, d, np.nan)


def leg_geometry(dirs: np.ndarray, leg_type: str) -> dict:
    """up_deg and half_deg for a leg from every boat's track directions on it (frame bearings).

    On a beat the tacks are split by which way they cross the course (x increasing = port tack,
    upwind is +y); on a run the same split gives the two gybes."""
    d = np.asarray(dirs, float)
    d = d[~np.isnan(d)]
    sign = 1 if leg_type == "upwind" else -1
    ref = 0.0 if sign > 0 else 180.0
    rel = (d - ref + 180) % 360 - 180  # direction relative to straight up (or down) the course
    near = np.abs(rel) < 90  # on this leg's general heading
    right = rel[near & (rel > 0)]
    left = rel[near & (rel < 0)]
    out = {"type": leg_type, "source": "tracks"}
    if len(right) >= MIN_SIDE_S and len(left) >= MIN_SIDE_S:
        mr, ml = float(np.median(right)), float(np.median(left))
        # drop turns and wiggles, then take the medians again
        right = right[np.abs(right - mr) < STEADY_DEG]
        left = left[np.abs(left - ml) < STEADY_DEG]
        mr, ml = float(np.median(right)), float(np.median(left))
        out["up_deg"] = round((ref + (mr + ml) / 2) % 360, 1)
        out["half_deg"] = round((mr - ml) / 2, 1)
    else:
        out["up_deg"] = ref
        out["half_deg"] = DEFAULT_HALF[leg_type]
        out["source"] = "assumed"
    return out


def run_geometry(dirs: np.ndarray, axis_deg: float) -> dict:
    """A run's half gybing angle against a known downwind axis (from the beats either side: the
    same wind), so a run sailed mostly on one gybe still gives its angle."""
    d = np.asarray(dirs, float)
    d = d[~np.isnan(d)]
    rel = (d - axis_deg + 180) % 360 - 180
    rel = rel[np.abs(rel) < 90]
    out = {"type": "downwind", "up_deg": round(axis_deg % 360, 1), "source": "tracks"}
    sides = [np.abs(rel[rel > 0]), np.abs(rel[rel < 0])]
    meds = [float(np.median(x)) for x in sides if len(x) >= MIN_SIDE_S]
    if meds:
        out["half_deg"] = round(float(np.mean(meds)), 1)
    else:
        out["half_deg"] = DEFAULT_HALF["downwind"]
        out["source"] = "assumed"
    return out


def target_points(pts: list[list[float]]) -> list[list[float]]:
    """A mark is one point; a gate or a line counts every point along it."""
    if len(pts) < 2:
        return [list(map(float, p)) for p in pts]
    (ax, ay), (bx, by) = pts[0], pts[1]
    return [[ax + (bx - ax) * f, ay + (by - ay) * f] for f in np.linspace(0, 1, LINE_SAMPLES)]


def to_go(x, y, geo: dict, targets: list[list[float]]) -> np.ndarray:
    """Metres still to sail to the nearest target point at the leg's angles (see the module doc)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    u = math.radians(geo["up_deg"])
    tx, ty = math.sin(u), math.cos(u)  # toward the mark, along the ladder
    nx, ny = math.cos(u), -math.sin(u)  # across it
    k = 1 / math.cos(math.radians(geo["half_deg"]))
    best = np.full(len(x), np.inf)
    for px, py in targets:
        dx, dy = px - x, py - y
        a = dx * tx + dy * ty  # up the ladder to the mark
        c = dx * nx + dy * ny
        d = np.hypot(a, c)
        d = np.where(a > 0, np.maximum(a * k, d), d)
        best = np.minimum(best, d)
    return best


def overstand(x, y, geo: dict, targets: list[list[float]]) -> np.ndarray:
    """Metres of the distance to sail that come from being past a layline (0 inside them)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    u = math.radians(geo["up_deg"])
    tx, ty = math.sin(u), math.cos(u)
    k = 1 / math.cos(math.radians(geo["half_deg"]))
    rung = np.full(len(x), np.inf)
    for px, py in targets:
        a = (px - x) * tx + (py - y) * ty
        rung = np.minimum(rung, np.maximum(a, 0) * k)
    return np.maximum(to_go(x, y, geo, targets) - rung, 0)


def rungs(geo: dict, targets: list[list[float]], reach_m: float, step: int = RUNG_M) -> dict:
    """Laylines and rungs to draw: polylines (None-separated) in the course frame.

    A mark gives both laylines from the mark. A gate or a line gives each layline from the end on
    its own side (the left layline from the left end, looking at the mark), and the rungs span
    the gate plus both laylines. Rungs are counted from the end further down the ladder (the one
    a boat reaches first). reach_m: how far down the ladder to draw (the leg's length or so)."""
    u = math.radians(geo["up_deg"])
    tx, ty = math.sin(u), math.cos(u)  # up the ladder, toward the mark
    nx, ny = math.cos(u), -math.sin(u)  # across it, to the right looking at the mark
    t = math.tan(math.radians(geo["half_deg"]))
    k = 1 / math.cos(math.radians(geo["half_deg"]))
    ends = [targets[0], targets[-1]]
    lad = [(p[0] * tx + p[1] * ty, p[0] * nx + p[1] * ny) for p in ends]  # (along, across)
    (al, cl), (ar, cr) = sorted(lad, key=lambda q: q[1])  # left end, right end
    a0 = min(al, ar)
    xy = lambda A, C: (A * tx + C * nx, A * ty + C * ny)
    left_at = lambda L: cl - (al - L) * t  # left layline's across position at along = L
    right_at = lambda L: cr + (ar - L) * t
    bottom = a0 - reach_m
    lay_x, lay_y, rung_x, rung_y, labels = [], [], [], [], []
    for (A, C), at in (((al, cl), left_at), ((ar, cr), right_at)):
        (x0, y0), (x1, y1) = xy(A, C), xy(bottom, at(bottom))
        lay_x += [x0, x1, None]
        lay_y += [y0, y1, None]
    for n in range(1, int(reach_m // step) + 1):
        a = n * step  # this rung is a metres down the ladder from the nearer end
        L = a0 - a
        (x0, y0), (x1, y1) = xy(L, left_at(L)), xy(L, right_at(L))
        rung_x += [x0, x1, None]
        rung_y += [y0, y1, None]
        labels.append({"x": round(x0, 1), "y": round(y0, 1), "rung_m": a, "to_go_m": round(a * k)})
    r = lambda v: [None if q is None else round(q, 1) for q in v]
    return {"layline": {"x": r(lay_x), "y": r(lay_y)}, "rungs": {"x": r(rung_x), "y": r(rung_y)}, "labels": labels}


def build(leg_types: list[str], dirs_by_leg: list[np.ndarray], targets_by_leg: list[list[list[float]]],
          origins: list[list[float]]) -> list[dict]:
    """Every leg's geometry and what to draw. Beats first: their tacks give the wind axis, which
    the runs share (a run sailed on one gybe can't show its own axis)."""
    geos = {j: leg_geometry(dirs_by_leg[j], k) for j, k in enumerate(leg_types) if k == "upwind"}
    for j, k in enumerate(leg_types):
        if k != "downwind":
            continue
        near = [geos[i]["up_deg"] for i in (j - 1, j + 1) if i in geos and geos[i]["source"] == "tracks"]
        if near:
            ax = math.degrees(math.atan2(np.mean(np.sin(np.radians(near))), np.mean(np.cos(np.radians(near)))))
            geos[j] = run_geometry(dirs_by_leg[j], ax + 180)
        else:
            geos[j] = leg_geometry(dirs_by_leg[j], k)
    out = []
    for j, k in enumerate(leg_types):
        tg = target_points(targets_by_leg[j]) if j < len(targets_by_leg) and targets_by_leg[j] else []
        if not tg:
            out.append({"leg": j, **geos[j], "targets": []})
            continue
        ox, oy = origins[j] if j < len(origins) else (0.0, 0.0)
        cx, cy = np.mean([p[0] for p in tg]), np.mean([p[1] for p in tg])
        reach = 1.15 * math.hypot(cx - ox, cy - oy)
        out.append({"leg": j, **geos[j], "targets": [[round(x, 1), round(y, 1)] for x, y in tg],
                    **rungs(geos[j], tg, reach)})
    return out
