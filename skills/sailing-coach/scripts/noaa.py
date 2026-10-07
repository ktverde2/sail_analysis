#!/usr/bin/env python3
"""Tide and current from NOAA CO-OPS (tidesandcurrents.noaa.gov), for the course and race times.

Finds the NOAA current-prediction station and water-level (tide) station nearest the course,
from the boats' own GPS, and saves what they predicted (and the tide gauge observed) around the
races to noaa.json. The Current page (current.py) reads it; nothing is fetched when reports are
rebuilt, so they still build offline.

  python noaa.py --reports <dir>/report            # writes <dir>/report/noaa.json
  python noaa.py --reports <dir>/report --current-station PCT0031   # choose the station

--reports is analyze.py's output: one boat, or a folder of boats (fleet layout). It needs
drift.json in the race folders (analyze.py writes them). Needs network access to
api.tidesandcurrents.noaa.gov.

NOAA current stations come in two kinds. Harmonic stations (type H) are predicted directly, so
NOAA returns a 6-minute series. Subordinate stations (type S) only have times of maximum flood,
maximum ebb and slack; between them this uses NOAA's own rule (speed rises as a sine from slack to
maximum and falls as a cosine back to slack). Velocity is along the station's flood/ebb axis:
+ flood (toward meanFloodDir), − ebb (toward meanEbbDir).
"""

from __future__ import annotations

import argparse
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
APP = "sail_analysis_sailing_coach"
STEP_MIN = 6
PAD_H = 1  # hours shown before the first gun and after the last finish
EVENT_PAD_H = 14  # hours of max/slack events either side, to interpolate between
MAX_KM = 40  # further than this isn't the course's water


def _get(url: str, params: dict | None = None) -> dict:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": APP})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(f"NOAA: {data['error'].get('message', data['error'])}")
    return data


def km(lat1, lon1, lat2, lon2) -> float:
    k = math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lat2 - lat1) * 110.54, (lon2 - lon1) * 111.32 * k)


def _utc(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y%m%d %H:%M")


def _parse(t: str) -> float:
    """NOAA time (GMT, 'YYYY-MM-DD HH:MM') to epoch seconds."""
    return datetime.strptime(t, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()


# --- stations ------------------------------------------------------------------------------------


def readable(name: str) -> str:
    """NOAA writes 'Point Loma Light, 0.8 nmi. east of'; make it '0.8 nmi east of Point Loma Light'."""
    place, _, where = name.rpartition(", ")
    if place and where.endswith(" of"):
        return f"{where.replace('nmi.', 'nmi')} {place}"
    return name


def nearest(kind: str, lat: float, lon: float, n: int = 5) -> list[dict]:
    """The n nearest stations of a kind ('currentpredictions' or 'waterlevels'), one row per
    station (current stations: the shallowest bin)."""
    st = _get(MDAPI, {"type": kind})["stations"]
    best = {}
    for s in st:
        d = km(lat, lon, s["lat"], s["lng"])
        row = {"id": s["id"], "name": readable(s["name"]), "lat": s["lat"], "lon": s["lng"], "km": round(d, 1)}
        if kind == "currentpredictions":
            row.update(bin=s.get("currbin"), depth_ft=s.get("depth"), type=s.get("type"))
            old = best.get(s["id"])
            # Shallowest bin: nearest to what a keelboat sails in (no depth listed = surface)
            if old and (old["depth_ft"] or 0) <= (row["depth_ft"] or 0):
                continue
        best[s["id"]] = row
    return sorted(best.values(), key=lambda r: r["km"])[:n]


# --- currents ------------------------------------------------------------------------------------


def current_events(station: dict, start: datetime, end: datetime) -> tuple[list[dict], dict]:
    """Max flood / max ebb / slack between start and end, plus the station's flood and ebb directions."""
    d = _get(API, {
        "product": "currents_predictions", "station": station["id"], "bin": station["bin"],
        "begin_date": _fmt(start), "end_date": _fmt(end), "time_zone": "gmt", "units": "english",
        "interval": "MAX_SLACK", "format": "json", "application": APP,
    })["current_predictions"]["cp"]
    axis = {"flood_dir": d[0].get("meanFloodDir"), "ebb_dir": d[0].get("meanEbbDir")} if d else {}
    ev = [{"t": _parse(c["Time"]), "type": c["Type"], "v": float(c["Velocity_Major"])} for c in d]
    return ev, axis


def current_series_harmonic(station: dict, start: datetime, end: datetime) -> list[tuple[float, float]]:
    d = _get(API, {
        "product": "currents_predictions", "station": station["id"], "bin": station["bin"],
        "begin_date": _fmt(start), "end_date": _fmt(end), "time_zone": "gmt", "units": "english",
        "interval": STEP_MIN, "format": "json", "application": APP,
    })["current_predictions"]["cp"]
    return [(_parse(c["Time"]), float(c["Velocity_Major"])) for c in d]


def interpolate(events: list[dict], times: list[float]) -> list[float | None]:
    """NOAA's rule between a subordinate station's events: a sine from slack up to maximum, a cosine
    from maximum down to slack (and a smooth blend between two maxima with no slack between)."""
    out = []
    for t in times:
        i = next((k for k in range(len(events) - 1) if events[k]["t"] <= t <= events[k + 1]["t"]), None)
        if i is None:
            out.append(None)
            continue
        a, b = events[i], events[i + 1]
        f = (t - a["t"]) / (b["t"] - a["t"]) if b["t"] > a["t"] else 0.0
        if a["type"] == "slack" and b["type"] != "slack":
            v = b["v"] * math.sin(math.pi / 2 * f)
        elif a["type"] != "slack" and b["type"] == "slack":
            v = a["v"] * math.cos(math.pi / 2 * f)
        else:
            v = a["v"] + (b["v"] - a["v"]) * (1 - math.cos(math.pi * f)) / 2
        out.append(round(v, 3))
    return out


# --- tide ----------------------------------------------------------------------------------------


def tide(station: dict, start: datetime, end: datetime) -> dict:
    common = {"station": station["id"], "begin_date": _fmt(start), "end_date": _fmt(end),
              "time_zone": "gmt", "units": "english", "datum": "MLLW", "format": "json", "application": APP}
    pred = _get(API, {**common, "product": "predictions", "interval": STEP_MIN})["predictions"]
    hilo = _get(API, {**common, "product": "predictions", "interval": "hilo"})["predictions"]
    try:  # observations exist only for the past, and gauges have gaps
        obs = _get(API, {**common, "product": "water_level"}).get("data", [])
    except Exception:  # noqa: BLE001 - a missing observation shouldn't stop the predictions
        obs = []
    num = lambda v: None if v in ("", None) else float(v)
    return {
        "pred": [[_parse(p["t"]), num(p["v"])] for p in pred],
        "obs": [[_parse(p["t"]), num(p["v"])] for p in obs if num(p["v"]) is not None],
        "hilo": [[_parse(p["t"]), num(p["v"]), p["type"]] for p in hilo],
    }


# --- all of it -----------------------------------------------------------------------------------


def race_windows(drifts: list[dict]) -> tuple[float, float, float, dict]:
    """Course centre (lat, lon) and each race's (gun, last sample) in epoch seconds."""
    lats, lons, races = [], [], {}
    for d in drifts:
        s = d.get("samples")
        if not s or not s["t"] or d.get("gun") is None:
            continue
        lats += s["lat"]
        lons += s["lon"]
        g, e = d["gun"], d["gun"] + max(s["t"])
        a, b = races.get(d["race"], (g, e))
        races[d["race"]] = (min(a, g), max(b, e))
    if not lats:
        raise SystemExit("no drift.json with GPS and a gun time: re-run analyze.py first")
    return sum(lats) / len(lats), sum(lons) / len(lons), races


def fetch(drifts: list[dict], current_station: str | None = None, tide_station: str | None = None) -> dict:
    lat, lon, races = race_windows(drifts)
    t0 = _utc(min(a for a, _ in races.values())) - timedelta(hours=PAD_H)
    t1 = _utc(max(b for _, b in races.values())) + timedelta(hours=PAD_H)

    cur_near = nearest("currentpredictions", lat, lon)
    wl_near = nearest("waterlevels", lat, lon)
    pick = lambda rows, sid: next((r for r in rows if r["id"] == sid), None) if sid else (rows[0] if rows else None)
    cs = pick(cur_near, current_station)
    if current_station and not cs:  # asked for one outside the nearest few
        cs = next(r for r in nearest("currentpredictions", lat, lon, n=10_000) if r["id"] == current_station)
    ws = pick(wl_near, tide_station)
    if tide_station and not ws:
        ws = next(r for r in nearest("waterlevels", lat, lon, n=10_000) if r["id"] == tide_station)

    out = {
        "source": "NOAA CO-OPS, tidesandcurrents.noaa.gov",
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "course": {"lat": round(lat, 5), "lon": round(lon, 5)},
        "races": {r: [a, b] for r, (a, b) in races.items()},
        "window": [t0.timestamp(), t1.timestamp()],
        "current_stations_nearby": cur_near,
        "tide_stations_nearby": wl_near,
        "current": None,
        "tide": None,
    }
    times = [t0.timestamp() + 60 * STEP_MIN * i for i in range(int((t1 - t0).total_seconds() // (60 * STEP_MIN)) + 1)]
    if cs and cs["km"] <= MAX_KM:
        ev, axis = current_events(cs, t0 - timedelta(hours=EVENT_PAD_H), t1 + timedelta(hours=EVENT_PAD_H))
        if cs.get("type") == "H":
            series = current_series_harmonic(cs, t0, t1)
        else:
            series = list(zip(times, interpolate(ev, times)))
        out["current"] = {
            "station": cs, **axis,
            "method": "NOAA harmonic prediction" if cs.get("type") == "H"
            else "NOAA subordinate station: max/slack times, interpolated",
            "events": [[e["t"], e["v"], e["type"]] for e in ev if t0.timestamp() - 6 * 3600 <= e["t"] <= t1.timestamp() + 6 * 3600],
            "series": [[t, v] for t, v in series if v is not None],
        }
    if ws and ws["km"] <= MAX_KM:
        out["tide"] = {"station": ws, "datum": "MLLW", "units": "ft", **tide(ws, t0, t1)}
    return out


def find(report_dirs: list[Path]) -> dict | None:
    """noaa.json beside the reports: in a boat's report folder or in the fleet folder above it."""
    for d in report_dirs:
        for p in (Path(d) / "noaa.json", Path(d).parent / "noaa.json"):
            if p.exists():
                return json.loads(p.read_text())
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reports", type=Path, required=True)
    ap.add_argument("--out", type=Path, help="default: <reports>/noaa.json")
    ap.add_argument("--current-station", help="NOAA current station id to use instead of the nearest")
    ap.add_argument("--tide-station", help="NOAA water-level station id to use instead of the nearest")
    a = ap.parse_args()
    drifts = [json.loads(p.read_text()) for p in sorted(a.reports.glob("**/drift.json"))]
    data = fetch(drifts, a.current_station, a.tide_station)
    out = a.out or a.reports / "noaa.json"
    out.write_text(json.dumps(data, indent=1))
    c, t = data["current"], data["tide"]
    print(f"{out}")
    print(f"  current: {c['station']['name']} ({c['station']['id']}), {c['station']['km']} km, {c['method']}" if c else "  current: no station within range")
    print(f"  tide:    {t['station']['name']} ({t['station']['id']}), {t['station']['km']} km" if t else "  tide:    no station within range")


if __name__ == "__main__":
    main()
