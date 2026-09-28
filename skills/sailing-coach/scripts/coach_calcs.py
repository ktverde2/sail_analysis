#!/usr/bin/env python3
"""Etchells coaching calculations.

Subcommands:
  targets <csv> [<csv> ...] [--speed-col BoatSpeed|SOG] [--twa-max 60] [--trim-start 30] [--json]
      Compare upwind steady-state data to the Etchells flat-water target card,
      binned by wind band. CSVs are Njord get_data output
      (ISODateTimeUTC, SecondsSince1970, <metrics...>).

  tod <meters> <knots>
      Seconds to cover <meters> at <knots> (time-on-distance for starts).
"""
import argparse
import json
import sys

import numpy as np
import pandas as pd

KT_TO_MS = 0.514444

# Etchells upwind targets, flat water: TWS -> (Vb, TWA, Heel)
TARGETS = pd.DataFrame(
    [
        (7, 5.1, 43, 12),
        (8, 5.5, 41, 14),
        (9, 5.7, 39, 16),
        (10, 5.8, 38, 17),
        (11, 5.8, 38, 18),
        (13, 5.9, 37, 21),
        (15, 6.0, 36, 22),
        (17, 6.0, 37, 22),
        (19, 6.1, 37, 22),
    ],
    columns=["TWS", "Vb", "TWA", "Heel"],
)
TWS_MIN, TWS_MAX = TARGETS.TWS.min(), TARGETS.TWS.max()
BANDS = [(0, 7), (7, 9), (9, 11), (11, 13), (13, 15), (15, 17), (17, 19), (19, 40)]


def interp_targets(tws):
    tws = np.asarray(tws, dtype=float)
    clamped = np.clip(tws, TWS_MIN, TWS_MAX)
    return {
        col: np.interp(clamped, TARGETS.TWS, TARGETS[col])
        for col in ("Vb", "TWA", "Heel")
    }


def load(path, trim_start):
    df = pd.read_csv(path, na_values=[""])
    if "SecondsSince1970" not in df.columns:
        sys.exit(f"{path}: missing SecondsSince1970 column (not a Njord get_data CSV?)")
    df = df.sort_values("SecondsSince1970")
    if trim_start > 0 and len(df):
        t0 = df.SecondsSince1970.iloc[0]
        df = df[df.SecondsSince1970 >= t0 + trim_start]
    dt = df.SecondsSince1970.diff().median()
    df["_dt"] = dt if pd.notna(dt) and dt > 0 else 1.0
    df["_src"] = path
    return df


def pick(df, *names):
    for n in names:
        if n in df.columns and df[n].notna().any():
            return n
    return None


def targets_cmd(args):
    df = pd.concat([load(p, args.trim_start) for p in args.csv], ignore_index=True)

    tws_col = pick(df, "TWS")
    twa_col = pick(df, "TWA_Abs", "TWA")
    heel_col = pick(df, "Heel_Abs", "Heel_Lwd", "Heel")
    spd_col = args.speed_col or pick(df, "BoatSpeed", "SOG")
    if not tws_col or not twa_col:
        sys.exit("Need TWS and TWA (or TWA_Abs) columns for target comparison.")
    if not spd_col or spd_col not in df.columns:
        sys.exit("No speed column (BoatSpeed or SOG) found.")

    df = df.dropna(subset=[tws_col, twa_col, spd_col]).copy()
    df["twa"] = df[twa_col].abs()
    df = df[df.twa <= args.twa_max]
    if heel_col:
        df["heel"] = df[heel_col].abs()

    t = interp_targets(df[tws_col])
    df["spd_pct"] = 100 * df[spd_col] / t["Vb"]
    df["twa_delta"] = df.twa - t["TWA"]
    df["tgt_heel"] = t["Heel"]
    if heel_col:
        df["heel_delta"] = df.heel - t["Heel"]

    rows = []
    for lo, hi in BANDS:
        b = df[(df[tws_col] >= lo) & (df[tws_col] < hi)]
        secs = float(b._dt.sum())
        if secs == 0:
            continue
        rows.append(
            {
                "band": f"{lo}-{hi}" if hi < 40 else f"{lo}+",
                "seconds": round(secs),
                "thin": secs < 60,
                "extrapolated": lo < TWS_MIN or hi > TWS_MAX,
                "tws_avg": round(b[tws_col].mean(), 1),
                "speed_avg": round(b[spd_col].mean(), 2),
                "speed_pct": round(b.spd_pct.mean(), 1),
                "twa_avg": round(b.twa.mean(), 1),
                "twa_delta": round(b.twa_delta.mean(), 1),
                "twa_std": round(b.twa.std(), 1),
                "heel_avg": round(b.heel.mean(), 1) if heel_col else None,
                "heel_tgt": round(b.tgt_heel.mean(), 1),
                "heel_delta": round(b.heel_delta.mean(), 1) if heel_col else None,
                "heel_std": round(b.heel.std(), 1) if heel_col else None,
            }
        )

    meta = {
        "speed_source": spd_col,
        "speed_note": "SOG includes current; compare tacks before coaching small differences"
        if spd_col == "SOG"
        else "",
        "heel_source": heel_col,
        "files": args.csv,
        "upwind_seconds": round(float(df._dt.sum())),
    }

    if args.json:
        print(json.dumps({"meta": meta, "bands": rows}, indent=2))
        return

    print(f"Speed source: {spd_col}. Heel source: {heel_col or 'none'}. "
          f"Upwind steady-state time: {meta['upwind_seconds']} s")
    if meta["speed_note"]:
        print(f"Note: {meta['speed_note']}")
    hdr = "| TWS band | Time (s) | Speed | % target | TWA | Δ TWA | TWA sd | Heel | Δ heel | Heel sd |"
    print(hdr)
    print("|" + "---|" * 10)
    for r in rows:
        flags = (" (thin)" if r["thin"] else "") + (" (extrap.)" if r["extrapolated"] else "")
        def fmt(k, signed=False):
            v = r[k]
            if v is None:
                return "–"
            return f"{v:+}" if signed else str(v)

        print(
            f"| {r['band']}{flags} | {r['seconds']} | {r['speed_avg']} | {r['speed_pct']}% "
            f"| {r['twa_avg']} | {fmt('twa_delta', True)} | {r['twa_std']} "
            f"| {fmt('heel_avg')} | {fmt('heel_delta', True)} | {fmt('heel_std')} |"
        )


def tod_cmd(args):
    if args.knots <= 0:
        sys.exit("Speed must be > 0 kts.")
    secs = args.meters / (args.knots * KT_TO_MS)
    print(f"{args.meters:g} m at {args.knots:g} kts = {secs:.1f} s")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("targets")
    t.add_argument("csv", nargs="+")
    t.add_argument("--speed-col", choices=["BoatSpeed", "SOG"])
    t.add_argument("--twa-max", type=float, default=60.0)
    t.add_argument("--trim-start", type=float, default=30.0,
                   help="seconds to drop from the start of each file (post-start/rounding transient)")
    t.add_argument("--json", action="store_true")
    t.set_defaults(func=targets_cmd)

    d = sub.add_parser("tod")
    d.add_argument("meters", type=float)
    d.add_argument("knots", type=float)
    d.set_defaults(func=tod_cmd)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
