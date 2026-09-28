#!/usr/bin/env python3
"""Interactive tack overlay: SOG, heading and tacking angle through every tack.

Reads the sailing-coach layout:
  <data>/<boat>/raceN.csv            Njord get_data export at 1 Hz (SOG, COG, Heading, Heel)
  <data>/<boat>/raceN-race.json      race metadata (startTime = gun)
  <reports>/<boat>/raceN/maneuvers.csv   tacks found by analyze.py

Each tack is re-anchored the same way for every boat: t = 0 is the moment the heading
passes halfway between the settled heading before and after the tack (head to wind).
The ranking and the "what the best tacks did" panel are computed in the page, so they
follow the boat/race filters.

  python tools/tack_overlay.py --data <dir>/data --reports <dir>/report \
      --out <dir>/report/<name>_tacks.html --title "Sept ODW 2026 · Tacks"
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

PRE, POST = 15, 40  # seconds shown either side of head to wind
ENTRY = (-10, -4)  # entry speed window, same as analyze.py
RECOVERY_PCT = 0.95
KT = 0.514444
HERE = Path(__file__).resolve().parent


def adiff(a, b):
    """Signed smallest angle from a to b, degrees."""
    return (b - a + 180.0) % 360.0 - 180.0


def circ_mean(deg):
    r = np.radians(np.asarray(deg, dtype=float))
    r = r[~np.isnan(r)]
    if not len(r):
        return np.nan
    return math.degrees(math.atan2(np.sin(r).mean(), np.cos(r).mean())) % 360


def unwrap_from(ref, series):
    """Heading relative to ref, unwrapped so a turn reads as a continuous line."""
    rel = adiff(ref, np.asarray(series, dtype=float))
    out = rel.copy()
    for i in range(1, len(out)):
        if np.isnan(out[i]) or np.isnan(out[i - 1]):
            continue
        out[i] = out[i - 1] + adiff(out[i - 1], out[i])
    return out


def tack_record(df: pd.DataFrame, t_rep: float, meta: dict) -> dict | None:
    s = df.set_index("tg")
    win = lambda a, b, c: s.loc[(s.index >= t_rep + a) & (s.index <= t_rep + b), c]

    h_pre = circ_mean(win(-14, -8, "Heading"))
    h_post = circ_mean(win(15, 25, "Heading"))
    if np.isnan(h_pre) or np.isnan(h_post):
        return None
    turn = adiff(h_pre, h_post)
    sign = 1 if turn > 0 else -1

    # Head to wind: first second the heading has turned half-way, near the reported time.
    near = s.loc[(s.index >= t_rep - 12) & (s.index <= t_rep + 12)]
    rel = sign * unwrap_from(h_pre, near.Heading.values)
    past = np.where(rel >= abs(turn) / 2)[0]
    if not len(past):
        return None
    t0 = float(near.index[past[0]])

    seg = s.loc[(s.index >= t0 - PRE) & (s.index <= t0 + POST)]
    x = (seg.index.values - t0).round().astype(int)
    sog = seg.SOG.values.astype(float)
    hdg = sign * unwrap_from(h_pre, seg.Heading.values)
    heel = seg.Heel.abs().values.astype(float) if "Heel" in seg else np.full(len(x), np.nan)

    at = lambda a, b: (x >= a) & (x <= b)
    entry = float(np.nanmean(sog[at(*ENTRY)]))
    look = at(-3, 15)
    i_min = np.flatnonzero(look)[np.nanargmin(sog[look])]
    min_sog, t_min = float(sog[i_min]), int(x[i_min])
    rec = np.flatnonzero((x > t_min) & (sog >= RECOVERY_PCT * entry))
    t_rec = int(x[rec[0]]) if len(rec) else None

    # Time lost to the tack: seconds at entry speed given away until recovered (or +40 s).
    end = t_rec if t_rec is not None else POST
    span = at(-5, end)
    deficit = np.clip(1 - sog[span] / entry, 0, None)
    secs_lost = float(np.nansum(deficit))

    # Turn: 10% -> 90% of the heading change, and the overshoot past the new heading.
    a = abs(turn)
    t10 = x[np.flatnonzero(hdg >= 0.1 * a)[0]] if (hdg >= 0.1 * a).any() else None
    t90 = x[np.flatnonzero(hdg >= 0.9 * a)[0]] if (hdg >= 0.9 * a).any() else None
    turn_s = int(t90 - t10) if t10 is not None and t90 is not None else None
    after_turn = at(0, 15)
    overshoot = float(np.nanmax(hdg[after_turn]) - a) if after_turn.any() else None

    cog_pre = circ_mean(win(-14, -8, "COG"))
    cog_post = circ_mean(win(15, 25, "COG"))
    cog_angle = abs(adiff(cog_pre, cog_post)) if not np.isnan(cog_pre + cog_post) else None

    heel_pre = float(np.nanmean(heel[at(-10, -4)]))
    heel_post = float(np.nanmean(heel[at(15, 25)]))
    heel_at10 = float(np.nanmean(heel[at(8, 12)]))

    return {
        **meta,
        "t0": round(t0),
        "clock": f"{int(t0 // 60)}:{int(t0 % 60):02d}",
        "entry": round(entry, 2),
        "min": round(min_sog, 2),
        "loss_pct": round(100 * (entry - min_sog) / entry, 1),
        "t_min": t_min,
        "t_rec": t_rec,
        "secs_lost": round(secs_lost, 1),
        "m_lost": round(secs_lost * entry * KT, 1),
        "hdg_angle": round(a, 1),
        "cog_angle": round(cog_angle, 1) if cog_angle is not None else None,
        "turn_s": turn_s,
        "overshoot": round(overshoot, 1) if overshoot is not None else None,
        "heel_pre": round(heel_pre, 1),
        "heel_at10": round(heel_at10, 1),
        "heel_post": round(heel_post, 1),
        "x": x.tolist(),
        "sog": [None if np.isnan(v) else round(float(v), 2) for v in sog],
        "hdg": [None if np.isnan(v) else round(float(v), 1) for v in hdg],
    }


def load(data: Path, reports: Path) -> tuple[list[dict], dict]:
    tacks, skipped = [], {"double": 0, "mark or stall": 0}
    for bdir in sorted(p for p in data.iterdir() if p.is_dir()):
        boat = bdir.name
        for csv in sorted(bdir.glob("race*.csv")):
            stem = csv.stem
            meta = json.loads(csv.with_name(f"{stem}-race.json").read_text())
            gun = pd.Timestamp(meta["startTime"]).timestamp()
            df = pd.read_csv(csv)
            df["tg"] = (df.SecondsSince1970 - gun).round()
            man = pd.read_csv(reports / boat / stem / "maneuvers.csv")
            for _, m in man[man.kind == "Tack"].iterrows():
                if isinstance(m.note, str) and m.note:
                    skipped["double"] += 1
                    continue  # doubles: the two tacks' losses overlap
                rec = tack_record(
                    df,
                    float(m.time_s),
                    {
                        "boat": boat,
                        "race": meta.get("race", stem),
                        "leg": None if pd.isna(m.leg) else int(m.leg),
                        "onto": m.onto,
                    },
                )
                # Beat-to-beat tacks only: a tack from a stall (start, traffic) or into or out of
                # a mark rounding (heel from a run, or a 100°+ turn into a bear-away) isn't a
                # tack to compare.
                if (
                    rec
                    and rec["entry"] >= 4.0
                    and rec["heel_pre"] >= 10
                    and rec["heel_post"] >= 10
                    and rec["hdg_angle"] <= 110
                ):
                    rec["id"] = f"{boat} · {rec['race']} · {rec['clock']}"
                    tacks.append(rec)
                else:
                    skipped["mark or stall"] += 1
    return tacks, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--reports", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--title", default="Tacks")
    ap.add_argument("--cdn", action="store_true", help="load Plotly online instead of inlining it")
    a = ap.parse_args()

    tacks, skipped = load(a.data, a.reports)
    note = (
        f"Left out: {skipped['double']} tacks in doubles (under 30 s apart, so their losses overlap) and "
        f"{skipped['mark or stall']} tacks at a mark rounding or from a stall (entry under 4 kt, heel under "
        "10° on either side, or a turn over 110°)."
    )
    boats = sorted({t["boat"] for t in tacks}, key=lambda b: (b != "Mojo", b))
    plotly = (
        '<script src="https://cdn.jsdelivr.net/npm/plotly.js-basic-dist-min@2.35.3/plotly-basic.min.js"></script>'
        if a.cdn
        else "<script>" + (HERE / "vendor" / "plotly-basic.min.js").read_text() + "</script>"
    )
    page = (HERE / "tack_overlay.html").read_text()
    page = (
        page.replace("{{TITLE}}", a.title)
        .replace("{{PLOTLY}}", plotly)
        .replace("{{DATA}}", json.dumps({"boats": boats, "tacks": tacks, "note": note}, separators=(",", ":")))
    )
    a.out.write_text(page)
    print(f"{a.out} ({len(tacks)} tacks)")


if __name__ == "__main__":
    main()
