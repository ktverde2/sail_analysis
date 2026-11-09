#!/usr/bin/env python3
"""3D race replay: one self-contained HTML page with every boat's track, in the style of
RaceQs or Vakaros RaceSense. Follow a boat, orbit and zoom around it, pan to the others.

    python3 replay3d.py --data data --report report --out Oct-11-2026_3d_replay.html

--data    the per-boat race files (data/<boat>/raceN.csv and raceN-race.json, as vkx_report.py
          or the Njord fetch writes them)
--report  analyze.py's output (report/<boat>/raceN/summary.json: legs and the gun) and, when
          there are two or more boats, fleet.py's report/fleet.json (colours, wind over time and
          ladder geometry). Without fleet.json the wind comes from each leg's estimate.

The page needs no network: three.js (vendor/three.min.js, MIT) is inlined, and all the data is
in one JSON block. Where the course lies inside a known terrain grid (terrain/*.npz; San Diego
Bay is included) the shoreline and water depth are drawn too.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
TERRAIN_MARGIN_M = 3500  # shoreline drawn this far around the tracks
SLOW_KT, FAST_KT = 1.0, 2.5  # below SLOW the hull points along the compass, above FAST along COG


def utm(lat: float, lon: float, zone: int) -> tuple[float, float]:
    """WGS84 lat/lon to UTM easting/northing (Krüger series, sub-millimetre in the zone)."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    n = f / (2 - f)
    A = a / (1 + n) * (1 + n * n / 4 + n ** 4 / 64)
    al = [n / 2 - 2 / 3 * n * n + 5 / 16 * n ** 3, 13 / 48 * n * n - 3 / 5 * n ** 3, 61 / 240 * n ** 3]
    phi, lam = math.radians(lat), math.radians(lon - (zone * 6 - 183))
    c = 2 * math.sqrt(n) / (1 + n)
    t = math.sinh(math.atanh(math.sin(phi)) - c * math.atanh(c * math.sin(phi)))
    xi, eta = math.atan2(t, math.cos(lam)), math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    E = 500000 + k0 * A * (eta + sum(al[j] * math.cos(2 * (j + 1) * xi) * math.sinh(2 * (j + 1) * eta) for j in range(3)))
    N = k0 * A * (xi + sum(al[j] * math.sin(2 * (j + 1) * xi) * math.cosh(2 * (j + 1) * eta) for j in range(3)))
    return E, N


def wrap(d):
    return (np.asarray(d) + 180) % 360 - 180


def circ_smooth(deg: np.ndarray, half: int) -> np.ndarray:
    """Centred circular moving average over 2*half+1 samples."""
    s, c = np.sin(np.radians(deg)), np.cos(np.radians(deg))
    k = np.ones(2 * half + 1)
    s = np.convolve(np.pad(s, half, mode="edge"), k, "valid")
    c = np.convolve(np.pad(c, half, mode="edge"), k, "valid")
    return np.degrees(np.arctan2(s, c)) % 360


def read_csv(path: Path) -> dict[str, np.ndarray]:
    rows = list(csv.DictReader(open(path)))
    out = {}
    for k in ("SecondsSince1970", "Lat", "Lon", "SOG", "COG", "Heading", "Heel", "Trim"):
        out[k] = np.array([float(r[k]) if r.get(k) not in (None, "") else np.nan for r in rows])
    return out


def boat_yaw(d: dict[str, np.ndarray]) -> np.ndarray:
    """Which way the hull points: COG when moving (no compass error), the compass, corrected by its
    median offset from COG, when slow or stopped (COG is noise then)."""
    cog, hdg, sog = d["COG"], d["Heading"], d["SOG"]
    fast = (sog > 4) & np.isfinite(cog) & np.isfinite(hdg)
    off = float(np.median(wrap(cog[fast] - hdg[fast]))) if fast.sum() > 30 else 0.0
    h = (hdg + off) % 360 if np.isfinite(hdg).any() else cog
    h = np.where(np.isfinite(h), h, cog)
    w = np.clip((sog - SLOW_KT) / (FAST_KT - SLOW_KT), 0, 1)
    w = np.where(np.isfinite(cog), w, 0)
    yaw = (h + w * wrap(np.nan_to_num(cog) - h)) % 360
    return circ_smooth(yaw, 1)


def load_terrain(box: tuple[float, float, float, float], zone: int, origin: tuple[float, float], path: Path | None):
    """Crop a land/depth grid to the box (UTM metres) and return it in the page's local frame."""
    cands = [path] if path else sorted((HERE / "terrain").glob("*.npz"))
    for p in cands:
        if not p or not p.exists():
            continue
        g = np.load(p)
        if int(g["zone"]) != zone:
            continue
        dx, E0, N1 = float(g["dx"]), float(g["E0"]), float(g["N1"])
        land, dq = g["land"], g["depth_q"]
        ny, nx = land.shape
        e0, n0, e1, n1 = box
        c0, c1 = int((e0 - E0) // dx), int((e1 - E0) // dx) + 1
        r0, r1 = int((N1 - n1) // dx), int((N1 - n0) // dx) + 1
        if c1 <= 0 or r1 <= 0 or c0 >= nx or r0 >= ny:
            continue
        c0, r0, c1, r1 = max(c0, 0), max(r0, 0), min(c1, nx), min(r1, ny)
        L, Dq = land[r0:r1, c0:c1][::-1], dq[r0:r1, c0:c1][::-1]  # row 0 = south
        return {
            "x0": round(E0 + c0 * dx + dx / 2 - origin[0], 1),
            "y0": round(N1 - (r1 - 1) * dx - dx / 2 - origin[1], 1),
            "dx": dx, "nx": int(c1 - c0), "ny": int(r1 - r0),
            "land": base64.b64encode(np.ascontiguousarray(L).tobytes()).decode(),
            "depth": base64.b64encode(np.ascontiguousarray(Dq).tobytes()).decode(),
            "depth_scale": float(g["depth_scale"]),
            "source": str(g["source"]),
        }
    return None


def race_stems(data: Path, boats: list[str]) -> list[str]:
    stems = set()
    for b in boats:
        stems |= {p.stem for p in (data / b).glob("race*.csv")}
    return sorted(stems, key=lambda s: int("".join(ch for ch in s if ch.isdigit()) or 0))


def build_data(data_dir: Path, report_dir: Path, title: str | None = None, terrain_path: Path | None = None,
               fleet: dict | None = None) -> dict:
    """Everything the replay draws, as one JSON-ready dict. `fleet` is fleet.json's content (read from
    report_dir when not given)."""

    boats = sorted(p.name for p in data_dir.iterdir() if p.is_dir() and any(p.glob("race*.csv")))
    if fleet is None and (report_dir / "fleet.json").exists():
        fleet = json.loads((report_dir / "fleet.json").read_text())
    fleet_races = {r["stem"]: r for r in fleet["races"]} if fleet else {}
    colors = {}
    if fleet:
        for r in fleet["races"]:
            for k, b in r["boats"].items():
                colors.setdefault(k, b["color"])
    # the fleet report's order (its first boat is the one the reports are about)
    lead = list((fleet or {}).get("day") or {})
    order = [b for b in lead if b in boats] + [b for b in boats if b not in lead]
    boats = order
    for i, b in enumerate(boats):
        colors.setdefault(b, PALETTE[i % len(PALETTE)])

    stems = race_stems(data_dir, boats)
    raw = {}
    meta = {}
    for s in stems:
        for b in boats:
            f = data_dir / b / f"{s}.csv"
            if f.exists():
                raw[(s, b)] = read_csv(f)
                if s not in meta:
                    meta[s] = json.loads((data_dir / b / f"{s}-race.json").read_text())

    lats = np.concatenate([d["Lat"][np.isfinite(d["Lat"])] for d in raw.values()])
    lons = np.concatenate([d["Lon"][np.isfinite(d["Lon"])] for d in raw.values()])
    lat0, lon0 = float(np.median(lats)), float(np.median(lons))
    zone = int((lon0 + 180) // 6) + 1
    E0, N0 = utm(lat0, lon0, zone)

    def xy(lat, lon):
        e, n = utm(lat, lon, zone)
        return [round(e - E0, 1), round(n - N0, 1)]

    races = []
    ex, ny_ = [], []
    event = None
    tz = None
    for s in stems:
        m = meta[s]
        event = event or m.get("event")
        tz = tz or m.get("timezone")
        gun = datetime.fromisoformat(m["startTime"].replace("Z", "+00:00")).timestamp()
        fr = fleet_races.get(s)
        course = []
        for c in m.get("course", []):
            pts = [xy(c[k]["lat"], c[k]["lon"]) for k in ("coord1", "coord2") if c.get(k)]
            course.append({"type": c["type"], "pts": pts})
        legs_by_boat = {}
        gun_local = None
        rb = {}
        for b in boats:
            d = raw.get((s, b))
            if d is None:
                continue
            summ = report_dir / b / s / "summary.json"
            sm = json.loads(summ.read_text()) if summ.exists() else {}
            gun_local = gun_local or sm.get("gun_local")
            legs = [[l["start_s"], l["start_s"] + l["duration_s"], l["type"]] for l in sm.get("legs", [])]
            legs_by_boat[b] = sm.get("legs", [])
            t = d["SecondsSince1970"] - gun
            ok = np.isfinite(d["Lat"]) & np.isfinite(d["Lon"])
            # one sample a second, starting on a whole second from the gun
            t0 = math.ceil(t[ok][0])
            ts = np.arange(t0, math.floor(t[ok][-1]) + 1)
            E = np.array([utm(la, lo, zone)[0] for la, lo in zip(d["Lat"][ok], d["Lon"][ok])]) - E0
            N = np.array([utm(la, lo, zone)[1] for la, lo in zip(d["Lat"][ok], d["Lon"][ok])]) - N0
            yaw = boat_yaw({k: v[ok] for k, v in d.items()})
            tt = t[ok]

            def interp(v):
                v = np.asarray(v, float)
                good = np.isfinite(v)
                return np.interp(ts, tt[good], v[good]) if good.any() else np.zeros(len(ts))

            # yaw interpolates through its unwrapped form
            yu = np.degrees(np.unwrap(np.radians(yaw)))
            ex += [float(E.min()), float(E.max())]
            ny_ += [float(N.min()), float(N.max())]
            rb[b] = {
                "t0": int(t0),
                "x": np.round(interp(E), 1).tolist(),
                "y": np.round(interp(N), 1).tolist(),
                "sog": np.round(interp(d["SOG"][ok]), 2).tolist(),
                "yaw": np.round(interp(yu) % 360, 1).tolist(),
                "heel": np.round(interp(d["Heel"][ok]), 1).tolist(),
                "trim": np.round(interp(d["Trim"][ok]), 1).tolist(),
                "legs": legs,
            }
        # wind: fleet.json's series over time, else each leg's estimate, else the day's
        wind = []
        if fr and fr.get("wind", {}).get("series"):
            wind = [[w["s"], round(w["twd"], 1)] for w in fr["wind"]["series"]]
        else:
            for b, ls in legs_by_boat.items():
                for l in ls:
                    if l.get("twd_est") is not None:
                        wind.append([l["start_s"] + l["duration_s"] / 2, l["twd_est"]])
                break
            if not wind:
                for b in boats:
                    summ = report_dir / b / s / "summary.json"
                    if summ.exists():
                        sm = json.loads(summ.read_text())
                        twd = (sm.get("wind") or {}).get("twd_estimated") or sm.get("upwind_axis")
                        if twd is not None:
                            wind = [[0, twd]]
                            break
            wind.sort()
        ladders = []
        if fr:
            ladders = [{"type": l["type"], "up": round((fr["axis"] + l["up_deg"]) % 360, 1), "half": l["half_deg"]}
                       for l in fr.get("ladders", [])]
        names = (fr or {}).get("marks") or []
        mag_var = (fr or {}).get("mag_var")
        if mag_var is None:
            for b in boats:
                summ = report_dir / b / s / "summary.json"
                if summ.exists():
                    mag_var = json.loads(summ.read_text()).get("mag_var")
                    break
        races.append({
            "name": m.get("race") or s,
            "gun": gun,
            "gun_local": (gun_local or "")[-8:-3],
            "course": course,
            "mark_names": names,
            "leg_names": (fr or {}).get("leg_names") or [],
            "wind": wind,
            "ladders": ladders,
            "mag_var": mag_var or 0.0,
            "boats": rb,
        })

    box_local = (min(ex), min(ny_), max(ex), max(ny_))
    box = (box_local[0] + E0 - TERRAIN_MARGIN_M, box_local[1] + N0 - TERRAIN_MARGIN_M,
           box_local[2] + E0 + TERRAIN_MARGIN_M, box_local[3] + N0 + TERRAIN_MARGIN_M)
    terrain = load_terrain(box, zone, (E0, N0), terrain_path)

    title = title or f"{event or 'Race'} · 3D replay"
    return {
        "title": title,
        "event": event,
        "timezone": tz,
        "origin": {"lat": round(lat0, 6), "lon": round(lon0, 6)},
        "boats": [{"name": b, "color": colors[b]} for b in boats],
        "races": races,
        "terrain": terrain,
    }


def assets() -> str:
    """The replay's template, three.js and its code, for a page that holds the data elsewhere and calls
    Replay3D.mount(element, data) (fleet.py's report does)."""
    tpl = (HERE / "replay3d.html").read_text()
    return (f'<template id="r3-template">{tpl}</template>'
            f"<script>{script_safe((HERE / 'vendor' / 'three.min.js').read_text())}</script>"
            f"<script>{script_safe((HERE / 'replay3d.js').read_text())}</script>")


def page(data: dict) -> str:
    """The standalone replay: one file, the replay filling the window."""
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            f"<title>{html.escape(data['title'])}</title>"
            "<style>html, body { margin: 0; height: 100%; overflow: hidden; background: #9fc3e0; }"
            " #r3 { position: fixed; inset: 0; }</style></head>"
            '<body><div id="r3"></div>'
            f'<script type="application/json" id="replay-data">{blob}</script>{assets()}'
            "<script>Replay3D.mount(document.getElementById('r3'), "
            "JSON.parse(document.getElementById('replay-data').textContent));</script></body></html>")


def script_safe(s: str) -> str:
    return s.replace("</script", "<\\/script")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--title", help="page title (default: the event name)")
    ap.add_argument("--terrain", type=Path, help="land/depth grid .npz (default: any in terrain/ that covers the course)")
    a = ap.parse_args()
    data = build_data(a.data, a.report, a.title, a.terrain)
    a.out.write_text(page(data))
    print(f"wrote {a.out} ({a.out.stat().st_size / 1e6:.1f} MB): {len(data['boats'])} boats, "
          f"{len(data['races'])} races, terrain {'yes' if data['terrain'] else 'no'}")


if __name__ == "__main__":
    main()
