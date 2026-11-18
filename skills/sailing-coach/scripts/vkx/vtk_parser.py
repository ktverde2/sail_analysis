#!/usr/bin/env python3
"""Parser for Velocitek `.vtk` logs (ProStart / Shift family), into the same
objects vkx_parser.parse_vkx returns, so vkx_report.py and everything after it
work unchanged.

Format (reverse-engineered from two J24 logs, Nov 14 2026, firmware 1.7.46):
a flat sequence of records, each a u16 little-endian length then that many
bytes of one protobuf message. The message has one field:

  1  fix, ~5 Hz        {1: unix s, 2: hundredths, 3: lat, 4: lon (sint, 1e-7 deg),
                        5: SOG (0.1 kt), 6: COG (deg true),
                        7..10: orientation quaternion w, x, y, z (sint, 1e-3)}
  2  race timer        {1: code, 2: seconds}: 2 = timer set (value), 1 = started or
                        synced (seconds left), 3 = timer stopped. A stop that lands
                        where the running countdown reaches zero is the gun; the
                        others are the crew stopping it by hand.
  16 line ping         {1: 1 or 2}: an end of the start line pinged at the boat's
                        position (1 and 2 are the two ends); {1: 4} is logged at
                        power off.
  17 device header     {1, 2, 3}: firmware version.

Protobuf drops zero-valued fields, so a missing field is 0.

The quaternion's yaw runs the other way from Vakaros's (heading = -yaw). The
parser turns heading, trim and heel into a quaternion in the Vakaros
convention, so vkx_report.quat_to_ypr reads them correctly: heading as the
device gives it, and heel negative on starboard tack, as Njord and Vakaros
logs have it.
"""

from __future__ import annotations

import math
import sys

from vkx_parser import Fix, LineEnd, TimerEvent, VkxFile, ts_to_iso

KN_TO_MS = 0.514444

# vkx_parser timer codes the report reads: 3 = gun, 0 = reset, 1 = start/sync, 4 = set
VK_GUN, VK_RESET, VK_SYNC, VK_SET = 3, 0, 1, 4

# Velocitek ping end ids -> LineEnd.end_id (the report only needs two distinct ids;
# which one is the pin comes from the geometry)
PING_END = {1: 0, 2: 1}
GUN_TOL_S = 1.5  # a timer stop within this of the countdown's zero is the gun


def _varint(b: bytes, i: int):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return r, i


def _msg(b: bytes) -> dict:
    i, out = 0, {}
    while i < len(b):
        k, i = _varint(b, i)
        f, w = k >> 3, k & 7
        if w == 0:
            v, i = _varint(b, i)
        elif w == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        else:
            raise ValueError(f"unexpected wire type {w}")
        out[f] = v
    return out


def _zz(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def _ypr(w, x, y, z):
    yaw = math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x)))))
    roll = math.degrees(math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)))
    return yaw, pitch, roll


def _quat(yaw, pitch, roll):
    """ZYX Euler (deg) to quaternion (w, x, y, z): the inverse of vkx_report.quat_to_ypr."""
    cy, sy = math.cos(math.radians(yaw) / 2), math.sin(math.radians(yaw) / 2)
    cp, sp = math.cos(math.radians(pitch) / 2), math.sin(math.radians(pitch) / 2)
    cr, sr = math.cos(math.radians(roll) / 2), math.sin(math.radians(roll) / 2)
    return (cr * cp * cy + sr * sp * sy,
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy)


def _heel_sign(raw):
    """+1 or -1 so that heel comes out negative on starboard tack (wind over the right).

    Upwind samples (|roll| > 8 deg, moving) split by roll sign into two heading clusters;
    their bisector on the short arc is the wind. The cluster turned left of the wind is
    starboard tack."""
    a = {1: [0.0, 0.0], -1: [0.0, 0.0]}
    for hdg, roll, sog in raw:
        if sog > 2 and abs(roll) > 8:
            s = 1 if roll > 0 else -1
            a[s][0] += math.sin(math.radians(hdg))
            a[s][1] += math.cos(math.radians(hdg))
    if not all(any(v) for v in a.values()):
        return 1
    hp = math.degrees(math.atan2(*a[1])) % 360   # roll > 0 cluster
    hn = math.degrees(math.atan2(*a[-1])) % 360
    d = ((hp - hn + 180) % 360) - 180
    wind = (hn + d / 2) % 360
    # starboard tack heads left of the wind: heading - wind < 0
    pos_is_stbd = ((hp - wind + 180) % 360) - 180 < 0
    return -1 if pos_is_stbd else 1


def parse_vtk(path: str) -> VkxFile:
    data = open(path, "rb").read()
    out = VkxFile(path=path)
    raw, timer = [], []
    last_ms = None
    off = 0
    while off < len(data):
        n = int.from_bytes(data[off:off + 2], "little")
        msg = _msg(data[off + 2:off + 2 + n])
        off += 2 + n
        for f, v in msg.items():
            sub = _msg(v)
            out.counts[f] = out.counts.get(f, 0) + 1
            if f == 1:
                g = lambda k: sub.get(k, 0)
                ms = g(1) * 1000 + g(2) * 10
                w, x, y, z = (_zz(g(k)) / 1000 for k in (7, 8, 9, 10))
                yaw, pitch, roll = _ypr(w, x, y, z)
                raw.append((ms, _zz(g(3)) / 1e7, _zz(g(4)) / 1e7, g(5) / 10, g(6),
                            (-yaw) % 360, pitch, roll))
                last_ms = ms
            elif f == 2:
                timer.append((last_ms, sub.get(1, 0), sub.get(2, 0)))
            elif f == 16:
                end = sub.get(1, 0)
                if end in PING_END and raw:
                    r = raw[-1]
                    out.line_ends.append(LineEnd(r[0], PING_END[end], r[1], r[2]))
            else:
                out.other.setdefault(f, []).append(v)

    sign = _heel_sign([(r[5], r[7], r[3]) for r in raw])
    for ms, lat, lon, sog, cog, hdg, pitch, roll in raw:
        qw, qx, qy, qz = _quat(hdg, pitch, sign * roll)
        out.fixes.append(Fix(ms, lat, lon, sog * KN_TO_MS, math.radians(cog), 0.0,
                             qw, qx, qy, qz))

    # Timer: a stop is the gun when it lands at the running countdown's zero.
    zero_at = None
    for ms, code, val in timer:
        if ms is None:
            continue
        if code == 1:
            zero_at = ms + val * 1000
            out.timer_events.append(TimerEvent(ms, VK_SYNC, val))
        elif code == 2:
            out.timer_events.append(TimerEvent(ms, VK_SET, val))
        elif code == 3:
            gun = zero_at is not None and abs(ms - zero_at) <= GUN_TOL_S * 1000
            out.timer_events.append(TimerEvent(zero_at if gun else ms,
                                               VK_GUN if gun else VK_RESET, 0))
            zero_at = None
    out.heel_sign = sign
    return out


if __name__ == "__main__":
    for p in sys.argv[1:]:
        v = parse_vtk(p)
        print(f"\n=== {p} ===  {len(v.fixes)} fixes  "
              f"{ts_to_iso(v.fixes[0].timestamp_ms)} .. {ts_to_iso(v.fixes[-1].timestamp_ms)}"
              f"  heel sign {v.heel_sign:+d}")
        for e in v.timer_events:
            print(f"  {ts_to_iso(e.timestamp_ms)}  {e.name:16s} {e.value}")
        for le in v.line_ends:
            print(f"  ping end {le.end_id} at {ts_to_iso(le.timestamp_ms)}: {le.lat:.6f}, {le.lon:.6f}")
