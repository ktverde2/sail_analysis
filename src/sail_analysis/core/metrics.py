"""Summary metrics computed from a track."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise

from .parsers import TrackPoint

_EARTH_RADIUS_NM = 3440.065
_MANEUVER_WINDOW_S = 15  # heading change measured over this window
_MANEUVER_MIN_DEG = 70  # change that counts as a tack/gybe
_MANEUVER_GAP_S = 30  # ignore further detections this soon after one


@dataclass
class Summary:
    start: datetime
    end: datetime
    duration_s: float
    distance_nm: float
    avg_sog: float
    max_sog: float
    maneuvers: list[datetime] = field(default_factory=list)
    # Series for charts: (seconds from start, value)
    sog_series: list[tuple[float, float]] = field(default_factory=list)
    track: list[tuple[float, float]] = field(default_factory=list)


def haversine_nm(a: TrackPoint, b: TrackPoint) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_NM * math.asin(math.sqrt(h))


def bearing_deg(a: TrackPoint, b: TrackPoint) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlon = math.radians(b.lon - a.lon)
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y)) % 360


def angle_diff(a: float, b: float) -> float:
    """Smallest signed difference b - a in degrees, in (-180, 180]."""
    d = (b - a) % 360
    return d - 360 if d > 180 else d


def summarize(points: list[TrackPoint]) -> Summary:
    t0 = points[0].time
    distance = 0.0
    sogs: list[float] = []
    cogs: list[tuple[float, float]] = []
    sog_series = []
    for prev, cur in pairwise(points):
        dt = (cur.time - prev.time).total_seconds()
        if dt <= 0:
            continue
        leg = haversine_nm(prev, cur)
        distance += leg
        sog = cur.sog if cur.sog is not None else leg / (dt / 3600)
        cog = cur.cog if cur.cog is not None else bearing_deg(prev, cur)
        t = (cur.time - t0).total_seconds()
        sogs.append(sog)
        sog_series.append((t, round(sog, 2)))
        if sog > 1.0:  # heading is noise when nearly stopped
            cogs.append((t, cog))

    duration = (points[-1].time - t0).total_seconds()
    return Summary(
        start=t0,
        end=points[-1].time,
        duration_s=duration,
        distance_nm=round(distance, 3),
        avg_sog=round(distance / (duration / 3600), 2) if duration > 0 else 0.0,
        max_sog=round(max(sogs), 2) if sogs else 0.0,
        maneuvers=[t0 + timedelta(seconds=t) for t in detect_maneuvers(cogs)],
        sog_series=sog_series,
        track=[(p.lat, p.lon) for p in points],
    )


def detect_maneuvers(cogs: list[tuple[float, float]]) -> list[float]:
    """Return times (s from start) where heading swings past the threshold."""
    found: list[float] = []
    j = 0
    for i, (t, cog) in enumerate(cogs):
        while cogs[j][0] < t - _MANEUVER_WINDOW_S:
            j += 1
        if j == i:
            continue
        swung = abs(angle_diff(cogs[j][1], cog)) >= _MANEUVER_MIN_DEG
        if swung and (not found or t - found[-1] >= _MANEUVER_GAP_S):
            found.append(t)
    return found
