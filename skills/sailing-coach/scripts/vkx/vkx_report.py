#!/usr/bin/env python3
"""Generate sailing-coach report inputs from Vakaros .vkx logs.

Writes, per boat per race, the three files the analysis scripts read
(see report-input-spec.txt):

    <data-dir>/<boat>/raceN.csv            1 Hz track
    <data-dir>/<boat>/raceN-race.json      gun time + course
    <data-dir>/<boat>/raceN-raceInfo.json  events

Races are detected from the VKX race-timer records: the gun is the
timer-expiry event (code 3), logged at the exact start time, and the
race ends at the next timer reset (codes 4+0). Start-line geometry
comes from the 0x05 line-broadcast records. Marks, gates, and the
finish line are not in a VKX file; supply them (and fixed race
numbering for the whole fleet) with --regatta:

    {
      "event": "Sept ODW",
      "timezone": "America/Los_Angeles",
      "races": [
        {"race": 1,
         "startTime": "2026-09-26T19:55:00.000Z",
         "endTime": "2026-09-26T20:46:09.101Z",      // optional override
         "course": [
           {"type": "StartLine", "coord1": {"lat": ..., "lon": ...},
                                 "coord2": {"lat": ..., "lon": ...}},
           {"type": "Mark", "coord1": {...}, "coord2": null},
           ...
         ]}
      ]
    }

Guns found in the VKX are matched to regatta races by startTime
(within 5 s). Without --regatta, races are numbered 1..N in gun-time
order across all input files and the course holds only the StartLine.

Usage:
    python3 vkx_report.py [options] file1.vkx [file2.vkx ...]

All input files must belong to one boat; run once per boat.

With a FinishLine in the course, each race ends at this boat's own
finish-line crossing plus --finish-pad seconds (default 30), so the
analysis stops at the finish rather than at the timer reset. An endTime
in the regatta file still wins.

Vendored into sailing-coach from github.com/ktverde2/vkx-parser
(Apache-2.0, see LICENSE). Changes from that version: race.json carries
"source": "Vakaros"; the finish leg is timed at the finish-line crossing;
races end at each boat's finish (--finish-pad).
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from vkx_parser import parse_vkx, ts_to_iso

MS_TO_KN = 1.94384
PRESTART_S = 419     # track starts this many seconds before the gun (Njord's window)
POST_END_S = 60      # track continues this long past endTime (Njord's window)
GUN_MATCH_TOL_MS = 5000


def iso_ms(ms: int) -> str:
    return ts_to_iso(ms)


def parse_iso(s: str) -> int:
    dt = datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")
    return int(dt.timestamp() * 1000)


def quat_to_ypr(w: float, x: float, y: float, z: float) -> tuple[float, float, float]:
    """ZYX Euler angles in degrees: yaw (magnetic heading 0..360), pitch (trim), roll (heel)."""
    yaw = math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))) % 360
    sp = max(-1.0, min(1.0, 2 * (w * y - z * x)))
    pitch = math.degrees(math.asin(sp))
    roll = math.degrees(math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)))
    return yaw, pitch, roll


def circular_mean_deg(angles: list[float]) -> float:
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    return math.degrees(math.atan2(s, c)) % 360


@dataclass
class Race:
    number: int
    gun_ms: int
    end_ms: int
    end_is_estimate: bool
    course: list[dict] = field(default_factory=list)
    end_fixed: bool = False  # endTime given in the regatta file


class LineTracker:
    """Latest known position of each start-line end, from 0x05 broadcasts."""

    def __init__(self, line_ends):
        self.by_end: dict[int, tuple[list[int], list[tuple[float, float]]]] = {}
        for le in sorted(line_ends, key=lambda e: e.timestamp_ms):
            ts_list, pos_list = self.by_end.setdefault(le.end_id, ([], []))
            ts_list.append(le.timestamp_ms)
            pos_list.append((le.lat, le.lon))

    def at(self, ms: int, max_age_ms: int = 15 * 60 * 1000):
        """(end0, end1) lat/lon at time ms, or None if either end is unknown/stale."""
        out = []
        for end_id in (0, 1):
            if end_id not in self.by_end:
                return None
            ts_list, pos_list = self.by_end[end_id]
            i = bisect.bisect_right(ts_list, ms) - 1
            if i < 0:
                i = 0  # back-fill before the first ping
                if ts_list[0] - ms > max_age_ms:
                    return None
            elif ms - ts_list[i] > max_age_ms:
                return None
            out.append(pos_list[i])
        return out[0], out[1]

    def mean_over(self, start_ms: int, end_ms: int):
        """Average position of each end over a window, or None."""
        out = []
        for end_id in (0, 1):
            if end_id not in self.by_end:
                return None
            ts_list, pos_list = self.by_end[end_id]
            lo = bisect.bisect_left(ts_list, start_ms)
            hi = bisect.bisect_right(ts_list, end_ms)
            pts = pos_list[lo:hi]
            if not pts:
                return None
            out.append(
                (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            )
        return out[0], out[1]


def line_distance_m(lat, lon, e0, e1, ref_lat):
    """Signed perpendicular distance (m) from a point to the infinite line
    through e0-e1, and the along-line parameter u (0 at e0, 1 at e1).
    Positive side is arbitrary here; the caller orients the sign."""
    kx = 111320.0 * math.cos(math.radians(ref_lat))
    ky = 111320.0
    ax, ay = (e0[1]) * kx, (e0[0]) * ky
    bx, by = (e1[1]) * kx, (e1[0]) * ky
    px, py = lon * kx, lat * ky
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return None, None
    u = ((px - ax) * dx + (py - ay) * dy) / L2
    cross = dx * (py - ay) - dy * (px - ax)
    return cross / math.sqrt(L2), u


def detect_races(timer_events, last_fix_ms) -> list[tuple[int, int, bool]]:
    """(gun_ms, end_ms, end_is_estimate) for each gun (code 3) event."""
    guns = [ev.timestamp_ms for ev in timer_events if ev.code == 3]
    resets = sorted(ev.timestamp_ms for ev in timer_events if ev.code == 0)
    races = []
    for gun in sorted(guns):
        i = bisect.bisect_right(resets, gun)
        if i < len(resets):
            races.append((gun, resets[i], False))
        else:
            races.append((gun, last_fix_ms, True))
    return races


def resample_rows(fixes, start_ms, end_ms, variation):
    """One row per whole second: position interpolated at the second,
    scalar fields averaged over the surrounding second (circular for angles)."""
    ts = [f.timestamp_ms for f in fixes]
    rows = []
    t = ((start_ms + 999) // 1000) * 1000
    while t <= end_ms:
        i = bisect.bisect_left(ts, t)
        lo = bisect.bisect_left(ts, t - 500)
        hi = bisect.bisect_left(ts, t + 500)
        window = fixes[lo:hi]
        # bracketing fixes for position
        prev = fixes[i - 1] if i > 0 else None
        nxt = fixes[i] if i < len(fixes) else None
        if not window or prev is None or nxt is None or \
                nxt.timestamp_ms - prev.timestamp_ms > 3000:
            t += 1000
            continue  # gap in the log
        if nxt.timestamp_ms == prev.timestamp_ms:
            lat, lon = prev.lat, prev.lon
        else:
            a = (t - prev.timestamp_ms) / (nxt.timestamp_ms - prev.timestamp_ms)
            lat = prev.lat + a * (nxt.lat - prev.lat)
            lon = prev.lon + a * (nxt.lon - prev.lon)

        sog = sum(f.sog_ms for f in window) / len(window) * MS_TO_KN
        cog = circular_mean_deg([math.degrees(f.cog_rad) for f in window])
        yprs = [quat_to_ypr(f.qw, f.qx, f.qy, f.qz) for f in window]
        hdg_mag = circular_mean_deg([y for y, _, _ in yprs])
        trim = sum(p for _, p, _ in yprs) / len(yprs)
        heel = sum(r for _, _, r in yprs) / len(yprs)

        rows.append({
            "ms": t,
            "Lat": lat,
            "Lon": lon,
            "SOG": sog,
            "COG": cog,
            "COG_Mag": (cog - variation) % 360,
            "Heading": (hdg_mag + variation) % 360,
            "Heading_Mag": hdg_mag,
            "Heel": heel,
            "Trim": trim,
        })
        t += 1000
    return rows


def add_below_line(rows, line: LineTracker, gun_ms):
    """BelowLineCalc: metres from the start line, + behind the line,
    − on the course side. Sign is oriented so the boat's median pre-start
    position (2 min to 30 s before the gun) is positive."""
    dists = []
    for row in rows:
        ends = line.at(row["ms"])
        if ends is None:
            dists.append(None)
            continue
        d, _u = line_distance_m(row["Lat"], row["Lon"], ends[0], ends[1],
                                ref_lat=row["Lat"])
        dists.append(d)

    pre = [d for row, d in zip(rows, dists)
           if d is not None and gun_ms - 120000 <= row["ms"] <= gun_ms - 30000]
    sign = 1.0
    if pre:
        pre.sort()
        if pre[len(pre) // 2] < 0:
            sign = -1.0
    for row, d in zip(rows, dists):
        row["BelowLineCalc"] = None if d is None else sign * d
    return sign


def detect_line_crossings(fixes, start_ms, stop_ms, line: LineTracker, sign):
    """Start-line crossings from the raw 2 Hz fixes, as (ms, direction).
    Forward = from behind the line (+) to the course side (−), crossing
    within the line's extent."""
    events = []
    prev_d = prev_u = prev_ms = None
    lo = bisect.bisect_left([f.timestamp_ms for f in fixes], start_ms)
    for f in fixes[lo:]:
        if f.timestamp_ms > stop_ms:
            break
        ends = line.at(f.timestamp_ms)
        if ends is None:
            prev_d = None
            continue
        d, u = line_distance_m(f.lat, f.lon, ends[0], ends[1], ref_lat=f.lat)
        d *= sign
        if prev_d is not None and d != prev_d and (prev_d >= 0) != (d >= 0):
            a = prev_d / (prev_d - d)
            u_cross = prev_u + a * (u - prev_u)
            if -0.05 <= u_cross <= 1.05:
                t_cross = int(prev_ms + a * (f.timestamp_ms - prev_ms))
                events.append((t_cross, "Forward" if prev_d > 0 else "Backward"))
        prev_d, prev_u, prev_ms = d, u, f.timestamp_ms
    return events


def dist_m(lat1, lon1, lat2, lon2):
    """Equirectangular distance in metres (fine for race-course scales)."""
    kx = 111320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lat1 - lat2) * 111320.0, (lon1 - lon2) * kx)


def start_summary(rows, gun_ms, crossings):
    """Start scorecard: distance behind the line and speed at −60/−30/−10 s
    and the gun, seconds late crossing, OCS, and a recross after the gun."""
    by_ms = {r["ms"]: r for r in rows}
    marks = {}
    for lead in (60, 30, 10, 0):
        r = by_ms.get(gun_ms - lead * 1000)
        if r is None:
            continue
        marks[f"-{lead}s" if lead else "gun"] = {
            "distanceToLine_m": None if r.get("BelowLineCalc") is None
                                else round(r["BelowLineCalc"], 1),
            "sog_kn": round(r["SOG"], 2),
        }

    cross_after = next((t for t, d in crossings
                        if d == "Forward" and t >= gun_ms - 5000), None)
    recross = any(d == "Backward" and gun_ms < t <= gun_ms + 120000
                  for t, d in crossings)
    gun_row = by_ms.get(gun_ms)
    ocs = (gun_row is not None and gun_row.get("BelowLineCalc") is not None
           and gun_row["BelowLineCalc"] < 0)
    return {
        "at": marks,
        "crossedAfterGun_s": None if cross_after is None
                             else round((cross_after - gun_ms) / 1000, 1),
        "ocs": ocs,
        "recrossWithin2Min": recross,
    }


def course_targets(course):
    """(label, lat, lon) for each course element after the start line;
    two-point elements (gates, finish) use their midpoint."""
    targets = []
    for c in course:
        if c.get("type") == "StartLine":
            continue
        c1, c2 = c.get("coord1"), c.get("coord2")
        if not c1:
            continue
        if c2:
            targets.append((c["type"], (c1["lat"] + c2["lat"]) / 2,
                            (c1["lon"] + c2["lon"]) / 2))
        else:
            targets.append((c["type"], c1["lat"], c1["lon"]))
    return targets


def segment_crossing(rows, i0, c1, c2, pad_m=20.0):
    """Index of the first row after i0 where the track crosses the segment
    c1-c2 (lengthened by pad_m at each end), or None."""
    lat0 = (c1["lat"] + c2["lat"]) / 2
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 111320.0
    ax, ay = c1["lon"] * kx, c1["lat"] * ky
    bx, by = c2["lon"] * kx, c2["lat"] * ky
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy)
    if L == 0:
        return None
    pad = pad_m / L
    prev = None
    for i in range(i0, len(rows)):
        px, py = rows[i]["Lon"] * kx, rows[i]["Lat"] * ky
        side = dx * (py - ay) - dy * (px - ax)
        u = ((px - ax) * dx + (py - ay) * dy) / (L * L)
        if prev is not None and (prev[0] < 0) != (side < 0) and side != prev[0]:
            a = prev[0] / (prev[0] - side)
            if -pad <= prev[1] + a * (u - prev[1]) <= 1 + pad:
                return i
        prev = (side, u)
    return None


ROUNDING_ENTER_M = 120   # a rounding counts once the boat gets this close
ROUNDING_EXIT_M = 200    # ... and is timed at the closest point before leaving


def leg_summary(rows, course, gun_ms, end_ms):
    """Leg-by-leg VMG: legs run gun → each mark rounding → finish. VMG is
    distance made good toward the leg's target over the leg's duration."""
    targets = course_targets(course)
    if not targets:
        return []
    finish = next((c for c in course if c.get("type") == "FinishLine"
                   and c.get("coord1") and c.get("coord2")), None)

    i0 = bisect.bisect_left([r["ms"] for r in rows], gun_ms)
    legs = []
    frm = "Start"
    for label, tlat, tlon in targets:
        best_i, inside = None, False
        if label == "FinishLine" and finish is not None:
            # timed where the track crosses the line, not at its midpoint
            best_i = segment_crossing(rows, i0 + 1, finish["coord1"], finish["coord2"])
            inside = best_i is not None
        # first close approach to the target after the previous rounding
        for i in range(i0, len(rows)):
            if inside and label == "FinishLine":
                break
            if rows[i]["ms"] > end_ms and not inside:
                break
            d = dist_m(rows[i]["Lat"], rows[i]["Lon"], tlat, tlon)
            if d <= ROUNDING_ENTER_M:
                inside = True
                if best_i is None or d < dist_m(rows[best_i]["Lat"],
                                                rows[best_i]["Lon"], tlat, tlon):
                    best_i = i
            elif inside and d > ROUNDING_EXIT_M:
                break
        if best_i is None or best_i <= i0:
            legs.append({"leg": len(legs) + 1, "from": frm, "to": label,
                         "note": "no rounding detected"})
            break

        seg = rows[i0:best_i + 1]
        d_start = dist_m(seg[0]["Lat"], seg[0]["Lon"], tlat, tlon)
        d_end = dist_m(seg[-1]["Lat"], seg[-1]["Lon"], tlat, tlon)
        dur_s = (seg[-1]["ms"] - seg[0]["ms"]) / 1000
        sailed = sum(dist_m(a["Lat"], a["Lon"], b["Lat"], b["Lon"])
                     for a, b in zip(seg, seg[1:]))
        legs.append({
            "leg": len(legs) + 1,
            "from": frm,
            "to": label,
            "startTime": iso_ms(seg[0]["ms"]),
            "endTime": iso_ms(seg[-1]["ms"]),
            "duration_s": round(dur_s, 1),
            "vmg_kn": round((d_start - d_end) / dur_s * MS_TO_KN, 2) if dur_s else None,
            "avgSog_kn": round(sum(r["SOG"] for r in seg) / len(seg), 2),
            "sailed_m": round(sailed),
            "straightLine_m": round(d_start - d_end),
        })
        frm, i0 = label, best_i
    return legs


def print_summary(s):
    start = s["start"]
    parts = []
    for key in ("-60s", "-30s", "-10s", "gun"):
        m = start["at"].get(key)
        if m:
            d = m["distanceToLine_m"]
            parts.append(f"{key} {'—' if d is None else f'{d:.0f}m'}@{m['sog_kn']:.1f}kn")
    late = start["crossedAfterGun_s"]
    flags = ("  OCS!" if start["ocs"] else "") + \
            ("  recrossed<2min" if start["recrossWithin2Min"] else "")
    print(f"  start: {'  '.join(parts)}  "
          f"crossed {'—' if late is None else f'+{late:.1f}s'}{flags}")
    for leg in s["legs"]:
        if "note" in leg:
            print(f"  leg {leg['leg']} {leg['from']}→{leg['to']}: {leg['note']}")
            continue
        mins, secs = divmod(int(leg["duration_s"]), 60)
        print(f"  leg {leg['leg']} {leg['from']}→{leg['to']}: {mins}:{secs:02d}  "
              f"VMG {leg['vmg_kn']:.2f}kn  SOG {leg['avgSog_kn']:.2f}kn  "
              f"sailed {leg['sailed_m']}m (line {leg['straightLine_m']}m)")


CSV_COLUMNS = ["ISODateTimeUTC", "SecondsSince1970", "Lat", "Lon", "SOG", "COG",
               "COG_Mag", "Heading", "Heading_Mag", "MagneticVariation", "Heel",
               "Trim", "BelowLineCalc"]


def write_race(out_dir: Path, boat, event, tz, race: Race, rows, info_events,
               variation):
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"race{race.number}"

    with open(out_dir / f"{stem}.csv", "w", newline="") as fh:
        fh.write(",".join(CSV_COLUMNS) + "\n")
        for r in rows:
            blc = r.get("BelowLineCalc")
            fh.write(",".join([
                iso_ms(r["ms"]),
                f"{r['ms'] / 1000:.3f}",
                f"{r['Lat']:.9f}",
                f"{r['Lon']:.9f}",
                f"{r['SOG']:.3f}",
                f"{r['COG']:.3f}",
                f"{r['COG_Mag']:.3f}",
                f"{r['Heading']:.3f}",
                f"{r['Heading_Mag']:.3f}",
                f"{variation}",
                f"{r['Heel']:.3f}",
                f"{r['Trim']:.3f}",
                "" if blc is None else f"{blc:.3f}",
            ]) + "\n")

    race_json = {
        "event": event,
        "race": f"Race {race.number}",
        "boat": boat,
        "timezone": tz,
        # the device logs the gun within a few ms of the scheduled start;
        # the fleet-shared startTime is the whole second (matches Njord)
        "startTime": iso_ms(round(race.gun_ms / 1000) * 1000),
        "endTime": iso_ms(race.end_ms),
        "source": "Vakaros",
        "course": race.course,
    }
    with open(out_dir / f"{stem}-race.json", "w") as fh:
        json.dump(race_json, fh, indent=2)

    with open(out_dir / f"{stem}-raceInfo.json", "w") as fh:
        json.dump({"events": info_events}, fh, indent=2)


def boat_from_filename(path: str) -> str:
    stem = Path(path).stem
    return re.sub(r"\s+\d{1,2}-\d{1,2}-\d{2,4}$", "", stem).strip() or stem


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Generate report input files (raceN.csv, raceN-race.json, "
                    "raceN-raceInfo.json) from Vakaros .vkx logs for one boat.")
    ap.add_argument("files", nargs="+", help=".vkx files for ONE boat")
    ap.add_argument("--data-dir", default="data", help="output root (default: data)")
    ap.add_argument("--boat", help="boat name (default: from the file name)")
    ap.add_argument("--event", default="Regatta", help='event name for race.json')
    ap.add_argument("--timezone", default="America/Los_Angeles")
    ap.add_argument("--variation", type=float, default=10.7,
                    help="magnetic variation, °E positive (default 10.7, San Diego)")
    ap.add_argument("--regatta", help="regatta JSON with race numbers and courses")
    ap.add_argument("--finish-pad", type=float, default=30,
                    help="with a FinishLine in the course, end each race this many "
                         "seconds after this boat crosses it (default 30; -1 keeps "
                         "the timer reset as the end)")
    args = ap.parse_args(argv)

    boat = args.boat or boat_from_filename(args.files[0])
    event, tz = args.event, args.timezone

    fixes, line_ends, timer_events = [], [], []
    for path in args.files:
        v = parse_vkx(path)
        fixes.extend(v.fixes)
        line_ends.extend(v.line_ends)
        timer_events.extend(v.timer_events)
        print(f"parsed {path}: {len(v.fixes)} fixes, {len(v.timer_events)} timer events")
    fixes.sort(key=lambda f: f.timestamp_ms)
    line = LineTracker(line_ends)

    regatta = None
    if args.regatta:
        regatta = json.load(open(args.regatta))
        event = regatta.get("event", event)
        tz = regatta.get("timezone", tz)

    detected = detect_races(timer_events, fixes[-1].timestamp_ms if fixes else 0)
    if not detected:
        sys.exit("no gun (timer-expiry) events found — nothing to export")

    races: list[Race] = []
    if regatta:
        for spec in regatta["races"]:
            spec_gun = parse_iso(spec["startTime"])
            match = next((d for d in detected if abs(d[0] - spec_gun) <= GUN_MATCH_TOL_MS), None)
            if match is None:
                print(f"  regatta race {spec['race']} ({spec['startTime']}): "
                      f"no matching gun in these files — skipped")
                continue
            gun, end, est = match
            if spec.get("endTime"):
                end, est = parse_iso(spec["endTime"]), False
            races.append(Race(spec["race"], gun, end, est, list(spec.get("course", [])),
                              end_fixed=bool(spec.get("endTime"))))
        matched = {r.gun_ms for r in races}
        for gun, _, _ in detected:
            if gun not in matched:
                print(f"  WARNING: gun at {iso_ms(gun)} not in the regatta file — skipped")
    else:
        for n, (gun, end, est) in enumerate(detected, start=1):
            races.append(Race(n, gun, end, est))

    for race in races:
        gun_whole_s = round(race.gun_ms / 1000) * 1000
        start_ms = gun_whole_s - PRESTART_S * 1000
        stop_ms = race.end_ms + POST_END_S * 1000
        rows = resample_rows(fixes, start_ms, stop_ms, args.variation)
        if not rows:
            print(f"Race {race.number}: no track data in window — skipped")
            continue

        sign = add_below_line(rows, line, race.gun_ms)
        crossings = detect_line_crossings(fixes, start_ms, stop_ms, line, sign)

        # course: VKX start line (averaged over the last minute before the gun)
        if not any(c.get("type") == "StartLine" for c in race.course):
            ends = line.mean_over(race.gun_ms - 60000, race.gun_ms)
            if ends:
                race.course.insert(0, {
                    "type": "StartLine",
                    "coord1": {"lat": round(ends[0][0], 6), "lon": round(ends[0][1], 6)},
                    "coord2": {"lat": round(ends[1][0], 6), "lon": round(ends[1][1], 6)},
                })

        gun_s = round(race.gun_ms / 1000) * 1000
        legs = leg_summary(rows, race.course, gun_s, race.end_ms)
        notes = []
        finished = legs and legs[-1].get("to") == "FinishLine" and "note" not in legs[-1]
        if (finished and args.finish_pad >= 0
                and not race.end_fixed):
            race.end_ms = parse_iso(legs[-1]["endTime"]) + int(args.finish_pad * 1000)
            race.end_is_estimate = False
            rows = [r for r in rows if r["ms"] <= race.end_ms + POST_END_S * 1000]
            crossings = [(t, d) for t, d in crossings
                         if t <= race.end_ms + POST_END_S * 1000]
            notes.append(f"ends {args.finish_pad:g} s after the finish")
        elif any(c.get("type") == "FinishLine" for c in race.course) and not finished:
            notes.append("finish line not crossed — check the course")

        info_events = [{"time": iso_ms(race.gun_ms), "eventType": "Gun"}]
        info_events += [{"time": iso_ms(t), "eventType": "LineCrossed",
                         "LineCrossDirection": d} for t, d in crossings]
        info_events.sort(key=lambda e: e["time"])

        out_dir = Path(args.data_dir) / boat
        write_race(out_dir, boat, event, tz, race, rows, info_events,
                   args.variation)

        if race.end_is_estimate:
            notes.append("endTime = end of data (no timer reset after the gun; "
                         "set endTime in the regatta file to trim it)")
        if not any(c.get("type") == "StartLine" for c in race.course):
            notes.append("no start-line broadcasts — BelowLineCalc empty")
        if len(race.course) <= 1:
            notes.append("course has StartLine only (marks need a regatta file)")
        print(f"Race {race.number}: gun {iso_ms(race.gun_ms)}, {len(rows)} rows, "
              f"{len(crossings)} line crossings"
              + (" — " + "; ".join(notes) if notes else ""))

        summary = {
            "boat": boat,
            "race": f"Race {race.number}",
            "gun": iso_ms(gun_s),
            "start": start_summary(rows, gun_s, crossings),
            "legs": legs,
        }
        with open(out_dir / f"race{race.number}-summary.json", "w") as fh:
            json.dump(summary, fh, indent=2)
        print_summary(summary)


if __name__ == "__main__":
    main()
