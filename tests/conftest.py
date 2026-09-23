from datetime import UTC, datetime, timedelta

import pytest


def make_csv(legs: list[tuple[float, float, int]]) -> str:
    """Build a CSV track. Each leg is (dlat_per_s, dlon_per_s, seconds)."""
    t0 = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
    lat, lon, t = 41.5, -71.3, 0
    rows = ["timestamp,latitude,longitude"]
    rows.append(f"{t0.isoformat()},{lat},{lon}")
    for dlat, dlon, secs in legs:
        for _ in range(secs):
            t += 1
            lat += dlat
            lon += dlon
            rows.append(f"{(t0 + timedelta(seconds=t)).isoformat()},{lat:.7f},{lon:.7f}")
    return "\n".join(rows) + "\n"


@pytest.fixture
def zigzag_csv() -> str:
    # ~6 kt: 1/60 nm per 10 s -> lat deg per second ~= 6/3600/60
    d = 6 / 3600 / 60
    # Upwind on starboard (NE), tack to port (NW), tack back: 2 maneuvers
    return make_csv([(d, d, 120), (d, -d, 120), (d, d, 120)])
