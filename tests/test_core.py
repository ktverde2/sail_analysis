import math

import pytest

from sail_analysis.core import parse_file, summarize
from sail_analysis.core.metrics import angle_diff
from sail_analysis.core.parsers import ParseError

GPX = b"""<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lat="41.5" lon="-71.3"><time>2026-09-01T14:00:00Z</time></trkpt>
<trkpt lat="41.501" lon="-71.3"><time>2026-09-01T14:00:10Z</time></trkpt>
<trkpt lat="41.502" lon="-71.3"><time>2026-09-01T14:00:20Z</time></trkpt>
</trkseg></trk></gpx>"""


def test_parse_gpx_and_summarize():
    s = summarize(parse_file("race.gpx", GPX))
    # 0.002 deg latitude = 0.12 nm in 20 s = 21.6 kt
    assert s.distance_nm == pytest.approx(0.12, abs=0.001)
    assert s.avg_sog == pytest.approx(21.6, abs=0.1)
    assert s.duration_s == 20


def test_csv_column_aliases_and_optional_fields():
    csv = (
        "Time,Lat,Lng,SOG_kts,Roll\n1788271200,41.5,-71.3,5.5,12\n1788271201,41.50002,-71.3,5.6,\n"
    )
    points = parse_file("log.csv", csv.encode())
    assert points[0].sog == 5.5
    assert points[0].heel == 12
    assert points[1].heel is None


def test_zigzag_detects_two_tacks(zigzag_csv):
    s = summarize(parse_file("zz.csv", zigzag_csv.encode()))
    assert len(s.maneuvers) == 2
    # Equal lat/lon degrees per second; a lon degree is shorter by cos(lat)
    expected = 6 * (1 + math.cos(math.radians(41.5)) ** 2) ** 0.5
    assert s.avg_sog == pytest.approx(expected, rel=0.02)


@pytest.mark.parametrize(
    "name,data,msg",
    [
        ("x.txt", b"", "Unsupported"),
        ("x.csv", b"foo,bar\n1,2\n", "missing required"),
        ("x.gpx", b"<gpx>", "Invalid GPX"),
        ("x.csv", b"time,lat,lon\n2026-09-01T14:00:00Z,41,-71\n", "fewer than two"),
    ],
)
def test_parse_errors(name, data, msg):
    with pytest.raises(ParseError, match=msg):
        parse_file(name, data)


def test_angle_diff_wraps():
    assert angle_diff(350, 10) == 20
    assert angle_diff(10, 350) == -20
