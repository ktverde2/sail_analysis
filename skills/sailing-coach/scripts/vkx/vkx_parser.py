#!/usr/bin/env python3
"""Parser for Vakaros Atlas 2 `.vkx` log files.

Based on vkx_format_spec.md (reverse-engineered). A .vkx file is a flat
sequence of ~2 KB pages: a 0xFF page header, fixed-size keyed records,
and a 0xFE page footer. Everything is little-endian. Every data record
payload starts with a u64 millisecond UTC epoch timestamp.
"""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

# key -> payload size in bytes (excluding the key byte)
RECORD_SIZES = {
    0xFF: 7,   # page header
    0xFE: 2,   # page footer
    0x02: 44,  # position / velocity / orientation
    0x03: 20,  # periodic status A
    0x04: 13,  # race-timer event
    0x05: 17,  # start-line end position
    0x07: 12,  # periodic status B
    0x08: 13,  # one-shot status
    0x0E: 16,  # session marker
}

# Deciphered by correlating with Njord Analytics event exports (see
# vkx_format_spec.md). Code 1's value is seconds remaining to the gun
# (rounded up); code 3 fires at the exact gun time.
TIMER_EVENT_NAMES = {
    0: "timer reset",
    1: "timer start/sync",
    3: "gun",
    4: "timer set",
}


def ts_to_iso(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        + "Z"
    )


@dataclass
class Fix:
    """0x02 — primary track record, ~2 Hz."""
    timestamp_ms: int
    lat: float
    lon: float
    sog_ms: float      # m/s
    cog_rad: float     # radians 0..2pi
    unknown: float
    qw: float
    qx: float
    qy: float
    qz: float


@dataclass
class TimerEvent:
    """0x04 — race-timer event."""
    timestamp_ms: int
    code: int
    value: int

    @property
    def name(self) -> str:
        return TIMER_EVENT_NAMES.get(self.code, f"unknown({self.code})")


@dataclass
class LineEnd:
    """0x05 — start-line end broadcast position."""
    timestamp_ms: int
    end_id: int
    lat: float
    lon: float


@dataclass
class VkxFile:
    path: str
    fixes: list[Fix] = field(default_factory=list)
    timer_events: list[TimerEvent] = field(default_factory=list)
    line_ends: list[LineEnd] = field(default_factory=list)
    other: dict[int, list[bytes]] = field(default_factory=dict)  # key -> raw payloads
    counts: dict[int, int] = field(default_factory=dict)
    pages: int = 0


def parse_vkx(path: str) -> VkxFile:
    data = open(path, "rb").read()
    out = VkxFile(path=path)
    off = 0
    n = len(data)

    while off < n:
        key = data[off]
        size = RECORD_SIZES.get(key)
        if size is None:
            raise ValueError(
                f"{path}: unknown record key 0x{key:02X} at offset {off} "
                f"(context: {data[max(0, off - 8):off + 9].hex(' ')})"
            )
        payload = data[off + 1 : off + 1 + size]
        if len(payload) < size:
            raise ValueError(f"{path}: truncated record 0x{key:02X} at offset {off}")
        out.counts[key] = out.counts.get(key, 0) + 1

        if key == 0xFF:
            out.pages += 1
        elif key == 0xFE:
            pass
        elif key == 0x02:
            ts, lat, lon, sog, cog, unk, qw, qx, qy, qz = struct.unpack(
                "<Qii7f", payload
            )
            out.fixes.append(
                Fix(ts, lat / 1e7, lon / 1e7, sog, cog, unk, qw, qx, qy, qz)
            )
        elif key == 0x04:
            ts, code, value = struct.unpack("<QBI", payload)
            out.timer_events.append(TimerEvent(ts, code, value))
        elif key == 0x05:
            ts, end_id, lat, lon = struct.unpack("<QBff", payload)
            out.line_ends.append(LineEnd(ts, end_id, lat, lon))
        else:
            out.other.setdefault(key, []).append(payload)

        off += 1 + size

    return out


def summarize(vkx: VkxFile) -> None:
    print(f"\n=== {vkx.path} ===")
    print(f"pages: {vkx.pages}")
    print("record counts:")
    for key in sorted(vkx.counts):
        print(f"  0x{key:02X}: {vkx.counts[key]:7d}")

    if vkx.fixes:
        first, last = vkx.fixes[0], vkx.fixes[-1]
        dur_h = (last.timestamp_ms - first.timestamp_ms) / 3.6e6
        print(
            f"track: {len(vkx.fixes)} fixes, {ts_to_iso(first.timestamp_ms)} .. "
            f"{ts_to_iso(last.timestamp_ms)} ({dur_h:.2f} h)"
        )
        lats = [f.lat for f in vkx.fixes]
        lons = [f.lon for f in vkx.fixes]
        print(
            f"bbox: lat {min(lats):.6f}..{max(lats):.6f}, "
            f"lon {min(lons):.6f}..{max(lons):.6f}"
        )

    if vkx.timer_events:
        print("race-timer events (0x04):")
        for ev in vkx.timer_events:
            print(f"  {ts_to_iso(ev.timestamp_ms)}  code={ev.code} ({ev.name})  value={ev.value}")

    if vkx.line_ends:
        first, last = vkx.line_ends[0], vkx.line_ends[-1]
        print(
            f"start-line broadcasts (0x05): {len(vkx.line_ends)}, "
            f"{ts_to_iso(first.timestamp_ms)} .. {ts_to_iso(last.timestamp_ms)}"
        )

    for key, payloads in sorted(vkx.other.items()):
        print(f"0x{key:02X} payloads:")
        for p in payloads[:5]:
            ts = struct.unpack("<Q", p[:8])[0]
            print(f"  {ts_to_iso(ts)}  {p[8:].hex(' ')}")
        if len(payloads) > 5:
            print(f"  ... {len(payloads) - 5} more")


if __name__ == "__main__":
    for path in sys.argv[1:]:
        summarize(parse_vkx(path))
