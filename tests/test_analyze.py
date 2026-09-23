"""analyze.py against the real July ODW races in samples/."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "skill/sailing-coach/scripts"))
import analyze

SAMPLES = ROOT / "samples/njord/2026-07-19_july-odw"


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    out = tmp_path_factory.mktemp("report")
    res = analyze.run([SAMPLES / "race1.csv", SAMPLES / "race2.csv"], out, tws=None, tz=None)
    return out, {r["stem"]: r for r in res}


def test_outputs_written(results):
    out, _ = results
    assert (out / "event.md").exists()
    for race in ("race1", "race2"):
        for f in (
            "report.md",
            "summary.json",
            "legs.csv",
            "maneuvers.csv",
            "track.png",
            "timeline.png",
            "start.png",
            "maneuvers.png",
        ):
            assert (out / race / f).stat().st_size > 0, f"{race}/{f}"


def test_times_are_local(results):
    _, r = results
    assert r["race1"]["gun_local"] == "2026-07-19 11:40:00"
    assert r["race1"]["timezone"] == "America/Los_Angeles"


def test_bad_logged_wind_is_flagged_and_direction_estimated(results):
    _, r = results
    for race in r.values():
        assert race["wind"]["trusted"] is False
        assert 260 < race["wind"]["twd_estimated"] < 295  # San Diego westerly
        assert race["targets"]["available"] is False


def test_starts(results):
    _, r = results
    s1, s2 = r["race1"]["start"], r["race2"]["start"]
    # Race 1: on the line at the gun (0.08 m over is GPS noise, not OCS)
    assert s1["late_s"] == 0.0 and "ocs_at_gun_m" not in s1
    # Race 2: 21 m back, crossed 13 s late, boat end
    assert s2["below_line_+0s_m"] == pytest.approx(21, abs=1)
    assert s2["late_s"] == pytest.approx(13, abs=1)
    assert s2["line_pos_label"] == "boat end"


def test_legs_alternate_upwind_downwind(results):
    _, r = results
    assert [lg["type"] for lg in r["race1"]["legs"]] == ["upwind", "downwind"] * 2
    assert [lg["type"] for lg in r["race2"]["legs"]] == ["upwind", "downwind", "upwind"]


def test_maneuvers(results):
    _, r = results
    m1 = r["race1"]["maneuvers"]
    assert len(m1) == 9
    doubles = [m for m in m1 if m["note"]]
    assert len(doubles) == 2
    assert r["race1"]["maneuver_summary"]["Tack"]["count"] == 7
    for race in r.values():
        for m in race["maneuvers"]:
            assert m["recovery_s"] is None or m["recovery_s"] >= 0
            assert m["speed_loss_kt"] is None or m["speed_loss_kt"] >= 0


def test_targets_with_user_wind(tmp_path):
    res = analyze.run([SAMPLES / "race2.csv"], tmp_path, tws=8, tz=None, plots=False)
    t = res[0]["targets"]
    assert t["available"] and t["bands"][0]["band"] == "7-9"
    assert 80 < t["bands"][0]["speed_pct"] < 130
