#!/usr/bin/env python3
"""Course marks for races logged without them (Vakaros .vkx): estimate, check on a map, verify.

A .vkx log has the start line (the device pings both ends) but no marks or finish. Parse each
boat once with vkx/vkx_report.py and no regatta file, then:

  course.py detect --data data --out regatta-proposed.json [--event "Oct 11 2026"]
      Estimates each race's windward and leeward marks and the finish line from every boat's
      track (windward-leeward courses). A leeward mark becomes a gate when boats rounded points
      more than GATE_SPLIT_M apart across the course. Notes on each guess go in "_notes".

  course.py page --data data --regatta regatta-proposed.json --out course-check.html
                 [--doc courses/<slug>] [--title "..."]
      A course-up map of every boat's track with the course on top: drag marks and line ends,
      switch a mark to a gate, add or remove marks. Each mark shows how close every boat passed
      it, and the finish shows when each boat crossed. Save writes the course to the page's
      shared store (doc id --doc) when the page is a published claude.ai artifact; anywhere else
      it downloads regatta.json.

  course.py check --data data --regatta regatta.json
      Each boat's closest pass at every mark (and which gate end) and its finish crossing.

Then parse again with --regatta regatta.json: vkx_report.py ends each race 30 s after the boat's
own finish crossing.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

KY = 110540.0  # m per degree of latitude
GATE_SPLIT_M = 25  # rounding points further apart than this across the course make a gate
SMOOTH_S = 21  # COG smoothing window (s) for leg detection
UP_DEG, DOWN_DEG = 60, 125  # |COG - wind| below UP_DEG is a beat, above DOWN_DEG a run
GAP_S = 90  # a stretch this long that's neither a beat nor a run ends a leg
SHORT_S = 60  # beats or runs shorter than this are folded into the leg before
PAGE = Path(__file__).with_name("course_check.html")


# ---------------------------------------------------------------- loading


def iso_s(s: str) -> float:
    return pd.Timestamp(s).timestamp()


def load_races(data_dir: Path) -> list[dict]:
    """Every boat's races, grouped by gun time: [{gun, boats: {name: {...}}}] in gun order."""
    groups: list[dict] = []
    # Mojo first, as in the fleet report, so it keeps the same colour
    for bdir in sorted((p for p in data_dir.iterdir() if p.is_dir()), key=lambda p: (p.name != "Mojo", p.name)):
        for rj in sorted(bdir.glob("race*-race.json")):
            meta = json.loads(rj.read_text())
            csv = rj.with_name(rj.name.replace("-race.json", ".csv"))
            if not csv.exists():
                continue
            gun = iso_s(meta["startTime"])
            g = next((g for g in groups if abs(g["gun"] - gun) <= 5), None)
            if g is None:
                g = {"gun": gun, "startTime": meta["startTime"], "meta": meta, "boats": {}}
                groups.append(g)
            df = pd.read_csv(csv)
            df["t"] = df.SecondsSince1970 - gun
            g["boats"][meta.get("boat") or bdir.name] = {"df": df, "meta": meta, "stem": csv.stem}
    groups.sort(key=lambda g: g["gun"])
    return groups


def start_line(group: dict) -> dict | None:
    for b in group["boats"].values():
        sl = next((c for c in b["meta"].get("course", []) if c.get("type") == "StartLine"), None)
        if sl:
            return sl
    return None


class Frame:
    """Local metres east/north of the start line's middle, and the course axis."""

    def __init__(self, sl: dict):
        self.lat0 = (sl["coord1"]["lat"] + sl["coord2"]["lat"]) / 2
        self.lon0 = (sl["coord1"]["lon"] + sl["coord2"]["lon"]) / 2
        self.kx = 111320.0 * math.cos(math.radians(self.lat0))
        self.axis = 0.0

    def xy(self, lat, lon):
        return (np.asarray(lon) - self.lon0) * self.kx, (np.asarray(lat) - self.lat0) * KY

    def latlon(self, x, y) -> dict:
        return {"lat": round(self.lat0 + float(y) / KY, 6), "lon": round(self.lon0 + float(x) / self.kx, 6)}

    def up_right(self):
        a = math.radians(self.axis)
        return (math.sin(a), math.cos(a)), (math.cos(a), -math.sin(a))

    def pr(self, x, y):
        """(metres up the course toward the wind, metres right of the axis looking upwind)"""
        (ux, uy), (rx, ry) = self.up_right()
        return x * ux + y * uy, x * rx + y * ry

    def from_pr(self, p, r) -> dict:
        (ux, uy), (rx, ry) = self.up_right()
        return self.latlon(p * ux + r * rx, p * uy + r * ry)


# ---------------------------------------------------------------- angles


def adiff(a, b):
    return (np.asarray(a) - b + 180) % 360 - 180


def cmean(a) -> float:
    a = np.radians(np.asarray(a, dtype=float))
    return float(np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean())) % 360)


def smooth_cog(cog: pd.Series, win: int = SMOOTH_S) -> np.ndarray:
    r = np.radians(cog.astype(float))
    s = np.sin(r).rolling(win, center=True, min_periods=3).mean()
    c = np.cos(r).rolling(win, center=True, min_periods=3).mean()
    return (np.degrees(np.arctan2(s, c)) % 360).to_numpy()


def course_axis(group: dict, fr: Frame, sl: dict) -> float:
    """Direction up the course (toward the wind): starts square to the start line on the side
    the boats sail to after the gun, then the bisector of the two tacks' mean COG."""
    x1, y1 = fr.xy(sl["coord1"]["lat"], sl["coord1"]["lon"])
    x2, y2 = fr.xy(sl["coord2"]["lat"], sl["coord2"]["lon"])
    nx, ny = -(y2 - y1), x2 - x1
    side = []
    for b in group["boats"].values():
        d = b["df"][(b["df"].t > 60) & (b["df"].t < 240)]
        bx, by = fr.xy(d.Lat, d.Lon)
        side.append(float(np.mean(bx * nx + by * ny)) if len(d) else 0.0)
    if np.nanmean(side) < 0:
        nx, ny = -nx, -ny
    axis = math.degrees(math.atan2(nx, ny)) % 360
    cogs = np.concatenate(
        [b["df"].COG[(b["df"].t > 20) & (b["df"].SOG > 3)].to_numpy() for b in group["boats"].values()]
    )
    for _ in range(5):
        off = adiff(cogs, axis)
        left, right = cogs[(off < 0) & (off > -UP_DEG)], cogs[(off > 0) & (off < UP_DEG)]
        if len(left) < 30 or len(right) < 30:
            break
        axis = cmean([cmean(left), cmean(right)])
    return axis


# ---------------------------------------------------------------- legs


def legs_of(df: pd.DataFrame, axis: float) -> list[list]:
    """[mode, i0, i1] stretches of beat ('U') and run ('D') sailing from the gun on."""
    cog = smooth_cog(df.COG)
    off = np.abs(adiff(cog, axis))
    sog = df.SOG.to_numpy()
    t = df.t.to_numpy()
    mode = np.where(sog < 2, "?", np.where(off < UP_DEG, "U", np.where(off > DOWN_DEG, "D", "?")))
    runs: list[list] = []
    for i in np.flatnonzero((mode != "?") & (t >= -10)):
        m = mode[i]
        if runs and runs[-1][0] == m and t[i] - t[runs[-1][2]] <= GAP_S:
            runs[-1][2] = i
        else:
            runs.append([m, i, i])
    merged: list[list] = []
    for r in runs:
        if merged and (t[r[2]] - t[r[1]] < SHORT_S) and t[r[1]] - t[merged[-1][2]] <= GAP_S:
            merged[-1][2] = r[2]
        elif merged and merged[-1][0] == r[0] and t[r[1]] - t[merged[-1][2]] <= GAP_S:
            merged[-1][2] = r[2]
        else:
            merged.append(r)
    return merged


def boat_course(b: dict, fr: Frame) -> dict:
    """One boat's roundings and finish candidate on a windward-leeward course."""
    df = b["df"]
    x, y = fr.xy(df.Lat.to_numpy(), df.Lon.to_numpy())
    p, r = fr.pr(x, y)
    t = df.t.to_numpy()
    legs = [lg for lg in legs_of(df, fr.axis) if t[lg[2]] > 0]
    out = {"p": p, "r": r, "t": t, "legs": [], "roundings": [], "finish": None, "note": None}
    if len(legs) < 2 or legs[0][0] != "U":
        out["note"] = "no beat then run found after the gun"
        return out
    # first windward rounding sets the course length
    a, nxt = legs[0], legs[1]
    lo, hi = max(a[2] - 45, 0), min(nxt[1] + 45, len(p) - 1)
    top = float(p[lo + int(np.argmax(p[lo : hi + 1]))])
    valid = [legs[0]]
    for lg in legs[1:]:
        want = "D" if valid[-1][0] == "U" else "U"
        seg = p[lg[1] : lg[2] + 1]
        if lg[0] != want or t[lg[1]] - t[valid[-1][2]] > GAP_S:
            break
        if (want == "U" and seg.max() < 0.75 * top) or (want == "D" and seg.min() > 0.35 * top):
            break
        valid.append(lg)
    out["legs"] = valid
    for a, nxt in zip(valid, valid[1:]):
        lo, hi = max(a[2] - 45, 0), min(nxt[1] + 45, len(p) - 1)
        w = p[lo : hi + 1]
        i = lo + int(np.argmax(w) if a[0] == "U" else np.argmin(w))
        out["roundings"].append({"kind": "windward" if a[0] == "U" else "leeward", "i": i})
    last = valid[-1]
    if last[0] == "D":
        # the run ends where the boat stops running: it rounded up or slowed after finishing
        out["finish"] = {"kind": "downwind", "i": int(last[2])}
    else:
        cog = smooth_cog(df.COG)
        i_top = next((i for i in range(last[1], last[2] + 1) if p[i] >= top - 20), last[2])
        i_turn = next(
            (i for i in range(i_top, min(last[2], len(p) - 16)) if abs(adiff(cog[i + 15], cog[i])) > 45),
            last[2],
        )
        out["finish"] = {"kind": "upwind", "i": int(i_turn)}
    return out


def detect(data_dir: Path, event: str | None) -> dict:
    groups = load_races(data_dir)
    if not groups:
        raise SystemExit(f"no raceN-race.json files under {data_dir}/<boat>/")
    first = groups[0]["meta"]
    reg = {"event": event or first.get("event", "Regatta"), "timezone": first.get("timezone", "UTC"), "races": []}
    for n, g in enumerate(groups, start=1):
        sl = start_line(g)
        notes: list[str] = []
        if sl is None:
            reg["races"].append({"race": n, "startTime": g["startTime"], "course": [],
                                 "_notes": ["No start line in the log, so no course could be estimated."]})
            continue
        fr = Frame(sl)
        fr.axis = course_axis(g, fr, sl)
        boats = {name: boat_course(b, fr) for name, b in g["boats"].items()}
        for name, bc in boats.items():
            if bc["note"]:
                notes.append(f"{name}: {bc['note']}; left out of the estimate.")
        ok = {k: v for k, v in boats.items() if not v["note"]}
        course = [{"type": "StartLine", "coord1": sl["coord1"], "coord2": sl["coord2"]}]
        if not ok:
            reg["races"].append({"race": n, "startTime": g["startTime"], "course": course, "_notes": notes})
            continue
        nr = max(len(v["roundings"]) for v in ok.values())
        counts = {k: len(v["roundings"]) for k, v in ok.items()}
        if len(set(counts.values())) > 1:
            notes.append("Boats disagree on the number of roundings (" + ", ".join(f"{k} {v}" for k, v in counts.items())
                         + "); the course follows the boat with the most.")
        # leeward roundings of every lap: one mark, or a gate when they spread across the course
        lee = [(bc["p"][rd["i"]], bc["r"][rd["i"]]) for bc in ok.values() for rd in bc["roundings"] if rd["kind"] == "leeward"]
        gate = None
        if lee:
            rs = sorted(r for _, r in lee)
            gaps = np.diff(rs) if len(rs) > 1 else np.array([0.0])
            if rs[-1] - rs[0] > GATE_SPLIT_M and gaps.max() > GATE_SPLIT_M / 2:
                cut = rs[int(np.argmax(gaps))]
                L = [(p, r) for p, r in lee if r <= cut]
                R = [(p, r) for p, r in lee if r > cut]
                gate = [tuple(np.mean(L, axis=0)), tuple(np.mean(R, axis=0))]
                width = gate[1][1] - gate[0][1]
                notes.append(f"Leeward: boats rounded points {width:.0f} m apart across the course, so it's set as a gate "
                             f"with an end at each. Check its width.")
            else:
                spread = "at the same spot" if rs[-1] - rs[0] < 5 else f"within {rs[-1] - rs[0]:.0f} m across the course"
                notes.append(f"Leeward: every rounding {spread}, so it's one mark. "
                             "If it was a gate, switch it and drag the other end into place.")
        for k in range(nr):
            pts = [(bc["p"][bc["roundings"][k]["i"]], bc["r"][bc["roundings"][k]["i"]]) for bc in ok.values() if len(bc["roundings"]) > k]
            kind = next(bc["roundings"][k]["kind"] for bc in ok.values() if len(bc["roundings"]) > k)
            if kind == "leeward" and gate:
                course.append({"type": "Gate", "coord1": fr.from_pr(*gate[0]), "coord2": fr.from_pr(*gate[1])})
                continue
            pm, rm = np.mean(pts, axis=0)
            course.append({"type": "Mark", "coord1": fr.from_pr(pm, rm), "coord2": None})
            if kind == "windward":
                spread = max(math.hypot(a[0] - pm, a[1] - rm) for a in pts)
                notes.append(f"Windward mark {k // 2 + 1}: from {len(pts)} rounding{'s' if len(pts) > 1 else ''}"
                             + (f", within {spread:.0f} m of each other." if len(pts) > 1 else "."))
        # finish: perpendicular to the course, where the first boat stopped racing
        fins = {k: v for k, v in ok.items() if v["finish"]}
        kinds = [v["finish"]["kind"] for v in fins.values()]
        if fins:
            kind = max(set(kinds), key=kinds.count)
            cand = [v["p"][v["finish"]["i"]] for v in fins.values() if v["finish"]["kind"] == kind]
            level = max(cand) if kind == "downwind" else min(cand)
            lats = []
            for v in fins.values():
                lg = v["legs"][-1]
                seg = range(lg[1], len(v["p"]))
                i = next((i for i in seg if (v["p"][i] <= level if kind == "downwind" else v["p"][i] >= level)), None)
                if i is not None:
                    lats.append(v["r"][i])
            c = float(np.mean(lats)) if lats else 0.0
            half = max(40.0, (max(lats) - min(lats)) / 2 + 25 if lats else 40.0)
            course.append({"type": "FinishLine", "coord1": fr.from_pr(level, c - half), "coord2": fr.from_pr(level, c + half)})
            notes.append(
                f"Finish ({kind}): set square to the course where the first boat stopped "
                + ("running (rounded up or slowed)" if kind == "downwind" else "beating (its first big turn at the top)")
                + ". This is the guess most worth checking: drag it to where the committee boat and pin were."
            )
        reg["races"].append({"race": n, "startTime": g["startTime"], "course": course,
                             "_axis": round(fr.axis), "_notes": notes})
    gated = [R["race"] for R in reg["races"] if any(c["type"] == "Gate" for c in R["course"])]
    for R in reg["races"]:
        i = next((i for i, n in enumerate(R.get("_notes", [])) if n.startswith("Leeward: every rounding")), None)
        if gated and R["race"] not in gated and i is not None:
            others = " and ".join(f"Race {r}" for r in gated[:2])
            R["_notes"][i] = (R["_notes"][i].split(", so")[0] + f", so it's set as one mark. {others} had a gate here: "
                              "if this race did too, switch the leeward mark to a gate and drag the other end into place.")
    return reg


# ---------------------------------------------------------------- passages


def passages(df: pd.DataFrame, course: list[dict], fr: Frame) -> list[dict | None]:
    """Each course element after the start: closest pass (marks, gates) or crossing (finish)."""
    x, y = fr.xy(df.Lat.to_numpy(), df.Lon.to_numpy())
    t = df.t.to_numpy()
    i0 = int(np.searchsorted(t, 0))
    out: list[dict | None] = []
    for el in course:
        if el.get("type") == "StartLine":
            continue
        if i0 >= len(t):
            out.append(None)
            continue
        if el["type"] == "FinishLine" and el.get("coord2"):
            ax, ay = fr.xy(el["coord1"]["lat"], el["coord1"]["lon"])
            bx, by = fr.xy(el["coord2"]["lat"], el["coord2"]["lon"])
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            side = dx * (y - ay) - dy * (x - ax)
            u = ((x - ax) * dx + (y - ay) * dy) / L2
            pad = 20 / math.sqrt(L2)
            hit = None
            for i in range(max(i0, 1), len(t)):
                if (side[i - 1] < 0) != (side[i] < 0):
                    a = side[i - 1] / (side[i - 1] - side[i])
                    if -pad <= u[i - 1] + a * (u[i] - u[i - 1]) <= 1 + pad:
                        hit = i
                        break
            out.append(None if hit is None else {"t": float(t[hit]), "i": hit})
            if hit is not None:
                i0 = hit
            continue
        ends = [el["coord1"]] + ([el["coord2"]] if el.get("coord2") else [])
        ds = []
        for e in ends:
            ex, ey = fr.xy(e["lat"], e["lon"])
            ds.append(np.hypot(x - ex, y - ey))
        d = np.min(ds, axis=0)
        near = np.flatnonzero(d[i0:] < 120)
        if not len(near):
            out.append(None)
            continue
        k = i0 + near[0]
        e2 = k
        while e2 + 1 < len(d) and d[e2 + 1] < 200:
            e2 += 1
        j = k + int(np.argmin(d[k : e2 + 1]))
        end = None if len(ends) == 1 else "ab"[int(np.argmin([q[j] for q in ds]))]
        out.append({"t": float(t[j]), "i": j, "d": float(d[j]), "end": end})
        i0 = j
    return out


def fmt_t(s: float) -> str:
    s = int(round(s))
    return f"{'-' if s < 0 else '+'}{abs(s) // 60}:{abs(s) % 60:02d}"


def check(data_dir: Path, regatta: dict) -> str:
    groups = load_races(data_dir)
    lines = []
    for R in regatta["races"]:
        g = next((g for g in groups if abs(g["gun"] - iso_s(R["startTime"])) <= 5), None)
        lines.append(f"Race {R['race']} ({R['startTime']})")
        if g is None:
            lines.append("  no boat has this gun")
            continue
        sl = next((c for c in R["course"] if c.get("type") == "StartLine"), None) or start_line(g)
        fr = Frame(sl)
        names = [c["type"] for c in R["course"] if c.get("type") != "StartLine"]
        for name, b in g["boats"].items():
            ps = passages(b["df"], R["course"], fr)
            parts = []
            for el, ps_ in zip(names, ps):
                if ps_ is None:
                    parts.append(f"{el}: not reached")
                    break
                if el == "FinishLine":
                    parts.append(f"finish {fmt_t(ps_['t'])}")
                else:
                    parts.append(f"{el} {ps_['d']:.0f} m{' (end ' + ps_['end'] + ')' if ps_['end'] else ''} at {fmt_t(ps_['t'])}")
            lines.append(f"  {name}: " + "; ".join(parts))
    return "\n".join(lines)


# ---------------------------------------------------------------- page


def page(data_dir: Path, regatta: dict, title: str | None, doc: str | None) -> str:
    groups = load_races(data_dir)
    races, boats = [], []
    for R in regatta["races"]:
        g = next((g for g in groups if abs(g["gun"] - iso_s(R["startTime"])) <= 5), None)
        if g is None or not R.get("course"):
            continue
        fr = Frame(R["course"][0])
        fr.axis = R.get("_axis") or course_axis(g, fr, R["course"][0])
        rb = []
        for name, b in g["boats"].items():
            df = b["df"]
            ps = passages(df, R["course"], fr)
            fin = ps[-1]["t"] if ps and ps[-1] and R["course"][-1]["type"] == "FinishLine" else None
            stop = (fin + 240) if fin is not None else df.t.iloc[-1]
            s = df[(df.t >= -120) & (df.t <= stop)].iloc[::2]
            rb.append({"name": name, "track": [[round(a, 6), round(o, 6), int(round(t))] for a, o, t in zip(s.Lat, s.Lon, s.t)]})
            if name not in boats:
                boats.append(name)
        races.append({"race": R["race"], "gun": R["startTime"], "axis": round(fr.axis), "boats": rb,
                      "notes": R.get("_notes", [])})
    clean = {
        "event": regatta.get("event", "Regatta"),
        "timezone": regatta.get("timezone", "UTC"),
        "races": [{"race": R["race"], "startTime": R["startTime"], "course": R["course"]}
                  for R in regatta["races"] if any(r["race"] == R["race"] for r in races)],
    }
    slug = re.sub(r"[^a-z0-9]+", "-", clean["event"].lower()).strip("-") or "regatta"
    data = {
        "title": title or f"{clean['event']} course check",
        "boats": boats,
        "doc": doc or f"courses/{slug}",
        "file": f"regatta-{slug}.json",
        "regatta": clean,
        "races": races,
    }
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = PAGE.read_text()
    return html.replace("__TITLE__", data["title"].replace("<", "&lt;")).replace("__DATA__", payload)


# ---------------------------------------------------------------- main


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect", help="estimate marks, gates and finish from the tracks")
    d.add_argument("--data", type=Path, default=Path("data"))
    d.add_argument("--out", type=Path, default=Path("regatta-proposed.json"))
    d.add_argument("--event")
    p = sub.add_parser("page", help="build the course-check map page")
    p.add_argument("--data", type=Path, default=Path("data"))
    p.add_argument("--regatta", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("course-check.html"))
    p.add_argument("--title")
    p.add_argument("--doc", help="shared-store doc id the page saves to (default courses/<event>)")
    c = sub.add_parser("check", help="each boat's pass at every mark and its finish")
    c.add_argument("--data", type=Path, default=Path("data"))
    c.add_argument("--regatta", type=Path, required=True)
    a = ap.parse_args(argv)

    if a.cmd == "detect":
        reg = detect(a.data, a.event)
        a.out.write_text(json.dumps(reg, indent=1))
        for R in reg["races"]:
            kinds = " → ".join(c["type"] for c in R["course"])
            print(f"Race {R['race']} ({R['startTime']}): {kinds}")
            for n in R.get("_notes", []):
                print(f"  - {n}")
        print(f"\nwrote {a.out}")
        print(check(a.data, reg))
    elif a.cmd == "page":
        reg = json.loads(a.regatta.read_text())
        a.out.write_text(page(a.data, reg, a.title, a.doc))
        print(f"wrote {a.out}")
    else:
        print(check(a.data, json.loads(a.regatta.read_text())))


if __name__ == "__main__":
    main()
