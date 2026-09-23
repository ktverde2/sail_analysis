"""Readers that turn GPX and CSV logs into a list of TrackPoints."""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class TrackPoint:
    time: datetime
    lat: float
    lon: float
    sog: float | None = None  # knots
    cog: float | None = None  # degrees true
    heel: float | None = None  # degrees, + = starboard


class ParseError(ValueError):
    pass


# Column aliases seen in common exports (Vakaros, RaceSense, Njord CSV, generic GPS).
_ALIASES = {
    "time": ("time", "timestamp", "datetime", "utc", "date_time", "time_utc"),
    "lat": ("lat", "latitude"),
    "lon": ("lon", "lng", "long", "longitude"),
    "sog": ("sog", "sog_kts", "sog_kn", "speed_kts", "bsp", "speed"),
    "cog": ("cog", "cog_deg", "course", "heading", "hdg"),
    "heel": ("heel", "roll", "heel_deg", "roll_deg"),
}


def parse_file(filename: str, data: bytes) -> list[TrackPoint]:
    name = filename.lower()
    if name.endswith(".gpx"):
        points = parse_gpx(data)
    elif name.endswith(".csv"):
        points = parse_csv(data.decode("utf-8-sig"))
    else:
        raise ParseError("Unsupported file type (use .gpx or .csv)")
    if len(points) < 2:
        raise ParseError("File contains fewer than two usable track points")
    return sorted(points, key=lambda p: p.time)


def parse_gpx(data: bytes) -> list[TrackPoint]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ParseError(f"Invalid GPX: {e}") from e
    points = []
    for el in root.iter():
        if not el.tag.endswith("trkpt"):
            continue
        time_el = next((c for c in el if c.tag.endswith("time")), None)
        if time_el is None or not time_el.text:
            continue
        points.append(
            TrackPoint(
                time=_parse_time(time_el.text),
                lat=float(el.attrib["lat"]),
                lon=float(el.attrib["lon"]),
            )
        )
    return points


def parse_csv(text: str) -> list[TrackPoint]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ParseError("CSV has no header row")
    cols = _match_columns(reader.fieldnames)
    missing = [k for k in ("time", "lat", "lon") if k not in cols]
    if missing:
        raise ParseError(f"CSV is missing required columns: {', '.join(missing)}")
    points = []
    for row in reader:
        try:
            points.append(
                TrackPoint(
                    time=_parse_time(row[cols["time"]]),
                    lat=float(row[cols["lat"]]),
                    lon=float(row[cols["lon"]]),
                    sog=_opt_float(row, cols.get("sog")),
                    cog=_opt_float(row, cols.get("cog")),
                    heel=_opt_float(row, cols.get("heel")),
                )
            )
        except (ValueError, TypeError):
            continue  # skip blank or malformed rows
    return points


def _match_columns(fieldnames: list[str]) -> dict[str, str]:
    normalized = {f.strip().lower().replace(" ", "_"): f for f in fieldnames}
    cols = {}
    for key, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                cols[key] = normalized[alias]
                break
    return cols


def _opt_float(row: dict, col: str | None) -> float | None:
    if col is None or row.get(col) in (None, ""):
        return None
    return float(row[col])


def _parse_time(value: str) -> datetime:
    value = value.strip()
    try:
        # Unix epoch seconds or milliseconds
        num = float(value)
        if num > 1e11:
            num /= 1000
        return datetime.fromtimestamp(num, tz=UTC)
    except ValueError:
        pass
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
