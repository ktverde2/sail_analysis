#!/usr/bin/env python3
"""COG vs heading: how the water moved the boats, and where on the course.

The gap between course over ground and heading (the drift angle) is three things added together:

  compass offset   the same on every heading (a mounting or calibration error, or magnetic
                   variation applied twice)
  leeway           upwind only, to leeward: COG right of heading on port, left on starboard
  current          the water's sideways push on whatever heading the boat is on

Without a paddlewheel (speed through the water) the current along the wind axis can't be told
apart from leeway: it pushes the boat to leeward on both tacks exactly as leeway does, and on the
runs it's along the track. So for each boat this fits, over all its races:

  drift x SOG = offset x SOG + slip x (+1 port / -1 stbd, upwind only) x SOG + C x (r . n)

where n is the unit vector to starboard of the (corrected) heading and r points across the course
(to the right, looking upwind). offset is the compass offset, slip is leeway plus any along-wind
current (between boats in the same water, the slip difference is a leeway difference), and C is
the current across the course, in knots. Every sample's drift left after the offset and slip,
divided by how square it was to the course, gives a per-second estimate of the cross-course
current: the map averages those, from every boat, in cells across the course.

analyze.py calls race_drift() for each race and writes <race>/drift.json. html_report.py and
fleet.py call report_page() to add a Current page from every boat's drift.json they can find.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import noaa

HERE = Path(__file__).resolve().parent
STEP_S = 2  # one sample every 2 s is plenty for 50 m cells
TRIM_LEG_S = 20  # skip the first and last 20 s of each leg (roundings)
TRIM_MANEUVER_S = 15  # and 15 s either side of every tack and gybe
MIN_SOG = 2.0
MAX_TURN_DEG_S = 4.0  # steady sailing only
MIN_SQUARE = 0.4  # skip samples sailing nearly along the cross-course axis
M_PER_DEG_LAT = 110_540.0
M_PER_DEG_LON = 111_320.0


def adiff(a, b):
    """Signed smallest angle from a to b, degrees."""
    return (np.asarray(b) - np.asarray(a) + 180.0) % 360.0 - 180.0


# --- per race (analyze.py) ---------------------------------------------------------------------


def race_drift(df: pd.DataFrame, res: dict, course: list[dict], boat: str) -> dict:
    """drift.json for one race: steady-sailing samples with heading, COG and the leg they're on."""
    out = {
        "boat": boat,
        "race": res["race"],
        "stem": res.get("stem"),
        "axis": res.get("upwind_axis"),
        "twd": (res.get("wind") or {}).get("twd_estimated"),
        "mag_var": res.get("mag_var"),  # true = magnetic + mag_var; the page shows magnetic
        "course": course,
        "samples": None,
    }
    need = {"tg", "Lat", "Lon", "SOG", "COG", "Heading"}
    if not need <= set(df.columns) or out["twd"] is None or df.Heading.isna().all():
        return out
    d = df.dropna(subset=list(need)).drop_duplicates("tg").copy()
    d["mode"], d["leg"] = None, None
    for lg in res.get("legs", []):
        if lg.get("type") not in ("upwind", "downwind") or lg.get("start_s") is None:
            continue
        a = lg["start_s"] + TRIM_LEG_S
        b = lg["start_s"] + lg["duration_s"] - TRIM_LEG_S
        on = (d.tg >= a) & (d.tg <= b)
        d.loc[on, "mode"], d.loc[on, "leg"] = lg["type"], lg["leg"]
    for m in res.get("maneuvers", []):
        if m.get("time_s") is not None:
            d.loc[(d.tg - m["time_s"]).abs() <= TRIM_MANEUVER_S, "mode"] = None
    turn = np.abs(adiff(d.Heading.shift(), d.Heading))
    dt = d.tg.diff()
    steady = (turn / dt.clip(lower=1)).fillna(0) <= MAX_TURN_DEG_S
    d = d[d["mode"].notna() & (d.SOG >= MIN_SOG) & steady]
    d = d[np.round(d.tg) % STEP_S == 0]
    # Starboard when the wind comes over the starboard side (wind from the boat's own estimate,
    # so a compass offset doesn't flip it)
    stbd = (out["twd"] - d.Heading) % 360 < 180
    r = lambda s, n: [round(float(v), n) for v in s]
    out["samples"] = {
        "t": [int(round(v)) for v in d.tg],
        "lat": r(d.Lat, 6),
        "lon": r(d.Lon, 6),
        "sog": r(d.SOG, 2),
        "hdg": r(d.Heading, 1),
        "cog": r(d.COG, 1),
        "mode": ["U" if m == "upwind" else "D" for m in d["mode"]],
        "side": ["S" if s else "P" for s in stbd],
        "leg": [int(v) for v in d.leg],
    }
    return out


# --- per boat fit ------------------------------------------------------------------------------


def _frame(drifts: list[dict]) -> pd.DataFrame:
    rows = []
    for dr in drifts:
        s = dr.get("samples")
        if not s or not s["t"]:
            continue
        f = pd.DataFrame(s)
        f["boat"], f["race"], f["axis"] = dr["boat"], dr["race"], dr["axis"]
        rows.append(f)
    if not rows:
        return pd.DataFrame()
    f = pd.concat(rows, ignore_index=True)
    f["drift"] = adiff(f.hdg, f.cog)
    f["up"] = (f["mode"] == "U").astype(float)
    f["sgn"] = np.where(f.side == "P", 1.0, -1.0)  # leeway pushes COG right of heading on port
    return f


def _square(f: pd.DataFrame, offset) -> np.ndarray:
    """r . n: how square the boat's sideways direction was to the cross-course axis (offset: one
    number, or one per row)."""
    h = np.radians(f.hdg + offset + 90)
    ax = np.radians(f["axis"])
    return np.cos(ax) * np.sin(h) - np.sin(ax) * np.cos(h)


def fit_boat(f: pd.DataFrame) -> dict | None:
    """Compass offset, upwind slip and cross-course current for one boat's samples."""
    if len(f) < 60 or f.up.sum() < 30 or (1 - f.up).sum() < 30:
        return None
    y = np.radians(f.drift) * f.sog
    offset = 0.0
    for _ in range(3):  # the offset changes n a little; iterate
        A = np.c_[f.sog, f.up * f.sgn * f.sog, _square(f, offset)]
        x, *_ = np.linalg.lstsq(A, y, rcond=None)
        offset = math.degrees(x[0])
    resid = y - A @ x
    return {
        "offset": round(offset, 1),
        "slip": round(math.degrees(x[1]), 1),
        "cross_kt": round(float(x[2]), 2),
        "n": int(len(f)),
        "resid_deg": round(float(np.degrees(np.std(resid / f.sog))), 1),
    }


SUSPECT_CROSS_KT = 0.25  # a boat's current this far from the other boats' in the same race
SUSPECT_SHARE = 0.75  # ...in this share of its races: the boat's compass, not the water


def compass_check(fits: dict, per_race: list[dict]) -> None:
    """Every boat in the same race sailed the same water, so their cross-course currents should
    agree. A boat that reads a different current race after race (against at least two other
    boats) has a heading error that changes with heading, like an uncompensated compass: flag it
    in fits[boat]["suspect"], and keep it off the current map and out of the fleet comparisons.
    Negative slip (the track to windward of the heading) is the same error showing upwind."""
    by_race: dict[str, dict[str, float]] = {}
    for p in per_race:
        by_race.setdefault(p["race"], {})[p["boat"]] = p["cross_kt"]
    for b, fb in fits.items():
        diffs = []
        for cur in by_race.values():
            others = [v for o, v in cur.items() if o != b]
            if b in cur and len(others) >= 2:
                diffs.append(cur[b] - float(np.median(others)))
        if len(diffs) < 2:
            continue
        off = [d for d in diffs if abs(d) >= SUSPECT_CROSS_KT]
        if len(off) >= SUSPECT_SHARE * len(diffs):
            why = (f"reads {abs(float(np.median(diffs))):.2f} kt more current across the course than the "
                   f"other boats in {len(off)} of {len(diffs)} races")
            if fb["slip"] < -1:
                why += f", and its track runs {abs(fb['slip']):.1f}° to windward of its heading upwind"
            fb["suspect"] = why


T2T_MIN_N = 15  # steady samples (2 s apart) on each tack of a leg to compare them
T2T_SIG_KT = 0.1  # a speed difference between tacks smaller than this is noise


def tack_to_tack(f: pd.DataFrame, fits: dict) -> dict:
    """Port against starboard on every leg, per boat: speed over ground and drift.

    A current across the course makes one tack faster over the ground and the other slower. Each
    leg's SOG difference is turned into the cross-course current that would explain it (dSOG over
    how differently the two tracks point across the course), so the compass never enters it. The
    drift (each boat's own compass offset and slip removed) gives a second, independent estimate.
    Real current shows on every boat, holds from a beat to the run after it (same sign across the
    course) and agrees with the drift; waves or pressure on one tack don't."""
    rows = []
    for (b, race, leg, mode), g in f.groupby(["boat", "race", "leg", "mode"]):
        if b not in fits:
            continue
        st, pt = g[g.side == "S"], g[g.side == "P"]
        if len(st) < T2T_MIN_N or len(pt) < T2T_MIN_N:
            continue
        def unit(x):
            a = np.radians(x.cog.to_numpy())
            v = np.array([np.mean(np.sin(a)), np.mean(np.cos(a))])
            return v / (np.linalg.norm(v) or 1)
        dv = unit(st) - unit(pt)
        th = math.radians(float(g["axis"].iloc[0]) + 90)  # right across the course, looking upwind
        across = math.sin(th) * dv[0] + math.cos(th) * dv[1]
        d_sog = float(st.sog.median() - pt.sog.median())
        c_sog = d_sog / across if abs(across) >= 0.3 else None
        fb = fits[b]
        sq = _square(g, fb["offset"])
        left = np.radians(g.drift - fb["offset"] - fb["slip"] * g.up * g.sgn) * g.sog
        ok = np.abs(sq) >= MIN_SQUARE
        c_drift = float(np.median(left[ok] / sq[ok])) if ok.sum() >= 10 else None
        rows.append({
            "boat": b, "race": race, "leg": int(leg), "mode": mode,
            "sog_s": round(float(st.sog.median()), 2), "sog_p": round(float(pt.sog.median()), 2),
            "d_sog": round(d_sog, 2),
            "c_sog": None if c_sog is None else round(c_sog, 2),
            "c_drift": None if c_drift is None else round(c_drift, 2),
            "suspect": bool(fb.get("suspect")),
        })
    t = pd.DataFrame(rows)
    out = {"rows": rows}
    if t.empty:
        return out
    good = t[~t.suspect]
    # 1. shared: on each beat, do all boats show the same faster tack?
    beats = t[(t["mode"] == "U") & (t.d_sog.abs() >= T2T_SIG_KT)]
    shared = [g.d_sog.gt(0).nunique() == 1 for _, g in beats.groupby(["race", "leg"]) if len(g) >= 2]
    out["shared"] = {"n": len(shared), "same": int(sum(shared))}
    ub = t[t["mode"] == "U"]
    out["faster_up"] = {"stbd": int((ub.d_sog >= T2T_SIG_KT).sum()), "port": int((ub.d_sog <= -T2T_SIG_KT).sum()),
                        "n": int(len(ub)), "mean_kt": round(float(ub.d_sog.mean()), 2)}
    # 2. held from beats to runs: a current's sign in the course frame doesn't depend on the leg
    held = []
    for (b, race), g in good.dropna(subset=["c_sog"]).groupby(["boat", "race"]):
        u, dn = g[g["mode"] == "U"].c_sog, g[g["mode"] == "D"].c_sog
        if len(u) and len(dn):
            held.append(bool(np.sign(u.median()) == np.sign(dn.median())))
    out["held"] = {"n": len(held), "same": int(sum(held))}
    # runs of one race disagreeing with each other is the clearest tell
    flips = []
    for (b, race), g in good[good["mode"] == "D"].dropna(subset=["c_sog"]).groupby(["boat", "race"]):
        if len(g) >= 2:
            flips.append(bool(g.c_sog.gt(0).nunique() > 1))
    out["run_flips"] = {"n": len(flips), "flipped": int(sum(flips))}
    # 3. the drift agrees?
    both = good.dropna(subset=["c_sog", "c_drift"])
    out["agree"] = {
        "n": int(len(both)),
        "r": round(float(both.c_sog.corr(both.c_drift)), 2) if len(both) >= 6 else None,
        "sog_median": round(float(both.c_sog.median()), 2) if len(both) else None,
        "drift_median": round(float(both.c_drift.median()), 2) if len(both) else None,
    }
    return out


# --- page data ---------------------------------------------------------------------------------


def build(drifts: list[dict], boat_order: list[str]) -> dict | None:
    """Everything the Current page needs, for every boat with drift samples."""
    f = _frame(drifts)
    if f.empty:
        return None
    fits, per_race = {}, []
    for b, g in f.groupby("boat"):
        fb = fit_boat(g)
        if fb:
            fits[b] = fb
            for race, gr in g.groupby("race"):
                fr = fit_boat(gr)
                if fr:
                    per_race.append({"boat": b, "race": race, **fr})
    compass_check(fits, per_race)
    f = f[f.boat.isin(fits)].copy()
    if f.empty:
        return None
    t2t = tack_to_tack(f, fits)
    f["offset"] = f.boat.map(lambda b: fits[b]["offset"])
    f["slip"] = f.boat.map(lambda b: fits[b]["slip"])
    f["corr"] = f.drift - f.offset  # drift with the compass offset removed
    sq = _square(f, f.offset)
    left = np.radians(f["corr"] - f.slip * f.up * f.sgn) * f.sog  # what the current is left to explain
    f["cross"] = np.where(np.abs(sq) >= MIN_SQUARE, left / np.where(sq == 0, np.nan, sq), np.nan)
    f.loc[f.boat.map(lambda b: bool(fits[b].get("suspect"))), "cross"] = np.nan  # not on the map

    # Course frame: metres, rotated so the median upwind axis points up the page
    lat0, lon0 = f.lat.mean(), f.lon.mean()
    axis = float(np.nanmedian([d["axis"] for d in drifts if d.get("axis") is not None]))
    th = math.radians(axis)
    k = math.cos(math.radians(lat0))

    def to_xy(lat, lon):
        e = (np.asarray(lon) - lon0) * M_PER_DEG_LON * k
        n = (np.asarray(lat) - lat0) * M_PER_DEG_LAT
        return e * math.cos(th) - n * math.sin(th), e * math.sin(th) + n * math.cos(th)

    f["x"], f["y"] = to_xy(f.lat, f.lon)
    races = sorted(f.race.unique(), key=lambda r: (len(r), r))
    boats = [b for b in boat_order if b in fits] + sorted(set(fits) - set(boat_order))

    # Breakdown: point of sail x tack, per boat and per leg
    def summ(g):
        return {
            "n": int(len(g)),
            "raw": round(float(g.drift.median()), 1),
            "corr": round(float(g["corr"].median()), 1),
            "sog": round(float(g.sog.median()), 2),
        }

    breakdown = []
    for (b, m, s), g in f.groupby(["boat", "mode", "side"]):
        breakdown.append({"boat": b, "mode": m, "side": s, **summ(g)})
    legs = []
    for (b, race, leg, m, s), g in f.groupby(["boat", "race", "leg", "mode", "side"]):
        if len(g) >= 10:  # 20 s or more
            legs.append({"boat": b, "race": race, "leg": int(leg), "mode": m, "side": s,
                         "t": int(g.t.min()), **summ(g)})

    marks = []
    for d in drifts:
        for el in d.get("course") or []:
            pts = [el.get("coord1"), el.get("coord2")]
            xy = [to_xy(p["lat"], p["lon"]) for p in pts if p]
            marks.append({"race": d["race"], "type": el["type"],
                          "pts": [[round(float(x), 1), round(float(y), 1)] for x, y in xy]})
    uniq, seen = [], set()
    for m in marks:
        key = (m["race"], m["type"], json.dumps(m["pts"]))
        if key not in seen:
            seen.add(key)
            uniq.append(m)

    r1 = lambda s, n=1: [None if pd.isna(v) else round(float(v), n) for v in s]
    mvs = [d["mag_var"] for d in drifts if d.get("mag_var") is not None]
    return {
        "axis": round(axis, 1),
        "mag_var": round(float(np.median(mvs)), 1) if mvs else None,
        "boats": boats,
        "races": races,
        "fits": fits,
        "per_race": per_race,
        "t2t": t2t,
        "breakdown": breakdown,
        "legs": legs,
        "marks": uniq,
        "s": {
            "b": [boats.index(b) for b in f.boat],
            "r": [races.index(r) for r in f.race],
            "t": [int(v) for v in f.t],
            "m": list(f["mode"]),
            "p": list(f.side),
            "x": r1(f.x),
            "y": r1(f.y),
            "sog": r1(f.sog, 2),
            "raw": r1(f.drift),
            "corr": r1(f["corr"]),
            "cross": r1(f.cross, 2),
        },
    }


# --- NOAA tide and current (noaa.py) ------------------------------------------------------------


def noaa_view(nd: dict | None, axis: float, tz: str) -> dict | None:
    """NOAA's predictions in local time, with the current resolved across and up the course."""
    if not nd or not (nd.get("current") or nd.get("tide")):
        return None
    zone = ZoneInfo(tz)
    loc = lambda e: datetime.fromtimestamp(e, zone).strftime("%Y-%m-%d %H:%M:%S")
    out = {"source": nd.get("source"), "fetched": nd.get("fetched"), "tz": tz,
           "races": [{"race": r, "start": loc(a), "end": loc(b), "a": a, "b": b} for r, (a, b) in nd["races"].items()]}
    # Only the racing days: an hour either side of each day's racing, not the night in between
    days: dict[str, list[float]] = {}
    for a, b in nd["races"].values():
        day = datetime.fromtimestamp(a, zone).date().isoformat()
        lo, hi = days.get(day, [a, b])
        days[day] = [min(lo, a), max(hi, b)]
    wins = sorted((lo - NOAA_PAD_S, hi + NOAA_PAD_S) for lo, hi in days.values())
    out["windows"] = [[loc(a), loc(b)] for a, b in wins]

    def keep(t):
        t = np.asarray(t, float)
        return np.any([(t >= a) & (t <= b) for a, b in wins], axis=0) if wins else np.ones(len(t), bool)

    def gapped(t, *cols):
        """Points inside the windows, with a break (None) between days so lines don't join across the night."""
        t = np.asarray(t, float)
        on = keep(t)
        xs, ys = [], [[] for _ in cols]
        prev = None
        for i in np.flatnonzero(on):
            w = next(k for k, (a, b) in enumerate(wins) if a <= t[i] <= b)
            if prev is not None and w != prev:
                xs.append(loc(wins[prev][1]))
                for y in ys:
                    y.append(None)
            prev = w
            xs.append(loc(t[i]))
            for y, c in zip(ys, cols):
                y.append(c[i])
        return xs, ys
    c = nd.get("current")
    if c and c.get("series"):
        t = np.array([p[0] for p in c["series"]])
        v = np.array([p[1] for p in c["series"]])
        # Toward the flood direction when flooding, the ebb direction when ebbing
        d = np.where(v >= 0, c["flood_dir"], c["ebb_dir"])
        spd = np.abs(v)
        across = spd * np.cos(np.radians(d - (axis + 90)))  # + toward the right side, looking upwind
        up = spd * np.cos(np.radians(d - axis))  # + up the course (into the wind)
        for r in out["races"]:
            on = (t >= r["a"]) & (t <= r["b"])
            if on.any():
                r.update(v=round(float(v[on].mean()), 2), across=round(float(across[on].mean()), 2),
                         up=round(float(up[on].mean()), 2), mid=loc((r["a"] + r["b"]) / 2))
        cx, (cv, ca, cu) = gapped(t, np.round(v, 2).tolist(), np.round(across, 2).tolist(), np.round(up, 2).tolist())
        ev = [e for e in c["events"] if keep([e[0]])[0]]
        out["current"] = {
            "station": c["station"], "method": c["method"], "flood_dir": c["flood_dir"], "ebb_dir": c["ebb_dir"],
            "x": cx, "v": cv, "across": ca, "up": cu,
            "events": [[loc(e[0]), e[1], e[2]] for e in ev],
        }
    tdd = nd.get("tide")
    if tdd and tdd.get("pred"):
        px, (pv,) = gapped([p[0] for p in tdd["pred"]], [p[1] for p in tdd["pred"]])
        ox, (ov,) = gapped([p[0] for p in tdd["obs"]], [p[1] for p in tdd["obs"]]) if tdd["obs"] else ([], ([],))
        out["tide"] = {
            "station": tdd["station"], "datum": tdd.get("datum", "MLLW"),
            "x": px, "pred": pv, "obs_x": ox, "obs": ov,
            "hilo": [[loc(h[0]), h[1], h[2]] for h in tdd["hilo"] if keep([h[0]])[0]],
        }
    return out


# --- html ----------------------------------------------------------------------------------------

NOAA_PAD_S = 3600  # tide and current shown from an hour before each day's first gun to an hour after its last finish


INTRO = (
    "Course over ground against heading: how far the boats were set sideways, and where on the course. "
    "Each boat's compass offset is taken out first, so boats with different compasses can share one map."
)


def fragment(extra_class: str = "") -> str:
    return f"""<div class="tk cur {extra_class}">
  <div class="tk-card">
    <h2>What COG − heading is made of</h2>
    <p class="tk-note" style="font-size:14px;color:var(--tk-ink2)">The gap between course over ground and heading
      adds up three things: the <b>compass offset</b> (the same on every heading), <b>leeway</b> (upwind, to leeward)
      and <b>current</b>. Without a paddlewheel, current running along the wind can't be told apart from leeway, so
      upwind the two are reported together as <b>slip</b>. Between boats in the same water, a difference in slip is a
      difference in leeway. Current <b>across</b> the course can be measured, and so can where the set was stronger.</p>
    <div class="tk-tablewrap" style="margin-top:8px"><table class="tk-cmp" data-r="fits"></table></div>
    <div data-r="fitnotes"></div>
  </div>
  <div class="tk-card">
    <h2>Tack to tack: speed and drift</h2>
    <p class="tk-note" style="font-size:14px;color:var(--tk-ink2)">A current across the course makes one tack faster over the ground
      and the other slower, and pushes both tracks the same way. Comparing port with starboard on the same leg, for the same boat,
      takes the compass out of the speed test completely (each boat's compass is calibrated differently). Real current shows up on
      every boat, holds from a beat to the run after it, and agrees with the drift. Waves or pressure on one tack don't.</p>
    <div class="tk-legend" data-r="leg-t2t"></div>
    <div class="tk-chart" data-r="t2t"></div>
    <p class="tk-note">Each dot is one boat on one leg: its median SOG on starboard minus port (steady sailing only). Circles: beats;
      diamonds: runs. Hover for the current across the course each would imply, and what the drift says.</p>
    <div data-r="t2tnotes"></div>
  </div>
  <div class="tk-card" data-r="noaa-card" hidden>
    <h2>Tide and current from NOAA</h2>
    <p class="tk-note" data-r="noaa-src"></p>
    <div data-r="noaa-notes"></div>
    <div class="tk-grid2">
      <div><h3 class="cur-h3">Tide</h3><div class="tk-chart cur-small" data-r="tide"></div></div>
      <div><h3 class="cur-h3">Current at the station</h3><div class="tk-chart cur-small" data-r="cur"></div></div>
    </div>
    <h3 class="cur-h3">Across the course: NOAA's prediction against what the boats measured</h3>
    <div class="tk-legend" data-r="leg-cmp"></div>
    <div class="tk-chart cur-small" data-r="cmp"></div>
    <p class="tk-note">Shaded: the races. The line is the station's current resolved across the course (+ toward the right side,
      looking upwind); the dots are each boat's fit for each race, at mid-race. NOAA's current up and down the course can't be
      checked this way (it looks like leeway), so it's in the hover only.</p>
  </div>
  <div class="tk-card">
    <h2>Upwind and downwind, port and starboard</h2>
    <p class="tk-note">Median COG − heading, compass offset removed. + means the boat tracked to the right of its heading.
      Upwind, the gap between port and starboard is twice the slip.</p>
    <div class="tk-tablewrap"><table class="tk-cmp" data-r="brk"></table></div>
  </div>
  <div class="tk-card">
    <h2>Leg by leg</h2>
    <div class="tk-legend" data-r="leg-legs"></div>
    <div class="tk-chart" data-r="legs"></div>
    <p class="tk-note">Each dot is one boat on one tack or gybe of one leg (20 s or more of steady sailing). If both tacks
      move the same way from one beat to the next, the water changed; if they move apart, the slip did.</p>
  </div>
  <div class="tk-card">
    <h2>The course</h2>
    <div class="tk-filters" role="toolbar">
      <div><label>Show</label><span class="tk-seg" data-r="f-metric"></span></div>
      <div><label>Boat</label><span class="tk-seg" data-r="f-boat"></span></div>
      <div><label>Race</label><span class="tk-seg" data-r="f-race"></span></div>
      <div><label>Leg</label><span class="tk-seg" data-r="f-mode"></span></div>
      <div><label>Tack</label><span class="tk-seg" data-r="f-side"></span></div>
      <div><label>Cell</label><span class="tk-seg" data-r="f-cell"></span></div>
    </div>
    <p class="tk-note" data-r="metric-note"></p>
    <div class="cur-map" data-r="map"></div>
    <p class="tk-note">Upwind is up the page. Cells are coloured by the median of every sample in them from the boats
      and races selected; faint cells have few samples. Hover a cell for its numbers. Drag to zoom, double-click to reset.</p>
  </div>
</div>"""


def assets() -> tuple[str, str]:
    css = (HERE / "maneuver_overlay.css").read_text() + (HERE / "current.css").read_text()
    return css, (HERE / "current.js").read_text()


def payload(data: dict) -> str:
    return json.dumps(data, separators=(",", ":")).replace("</", "<\\/")


def load_drifts(report_dirs: list[Path]) -> list[dict]:
    out = []
    for d in report_dirs:
        for p in sorted(Path(d).glob("*/drift.json")):
            out.append(json.loads(p.read_text()))
    return out


def fleet_dirs(report_dir: Path) -> list[Path]:
    """A boat's report and, in the fleet layout (<reports>/<boat>/), its sibling boats'."""
    report_dir = Path(report_dir)
    sibs = [p for p in report_dir.parent.iterdir() if p.is_dir() and list(p.glob("*/drift.json"))]
    return sibs if report_dir in sibs else [report_dir]


def page_parts(report_dirs: list[Path], focus: str | None = None, embedded: bool = True):
    """(body html, scripts) for a Current page, or None when there's no drift data."""
    from maneuver_overlay import boat_order  # same boat colours as the Tacks and Gybes pages

    drifts = load_drifts(report_dirs)
    data = build(drifts, boat_order(d["boat"] for d in drifts))
    if not data:
        return None
    data["focus"] = focus
    tz = next((d.get("timezone") for d in drifts if d.get("timezone")), "UTC")
    data["noaa"] = noaa_view(noaa.find(report_dirs), data["axis"], tz)
    css, js = assets()
    body = fragment("tk-embedded" if embedded else "")
    script = (
        f"<style>{css}</style><script>{js}</script>"
        f'<script>CurrentMap(document.querySelector("#current .cur"), {payload(data)});</script>'
    )
    return body, script
