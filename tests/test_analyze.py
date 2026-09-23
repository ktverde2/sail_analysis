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
            "shifts.png",
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
    assert t["available"] and [b["wind"] for b in t["bands"]] == ["8 kt"]
    assert t["bands"][0]["speed_tgt"] == 5.5 and t["bands"][0]["heel_tgt"] == 14
    assert 80 < t["bands"][0]["speed_pct"] < 130


def test_targets_with_wind_range(tmp_path):
    res = analyze.run([SAMPLES / "race1.csv"], tmp_path, tws="8-10", tz=None, plots=False)
    t = res[0]["targets"]
    assert [b["wind"] for b in t["bands"]] == ["8 kt", "9 kt", "10 kt"]
    # Same sailing, stricter card as the wind rises: % of target falls, heel excess shrinks
    pct = [b["speed_pct"] for b in t["bands"]]
    heel = [b["heel_delta"] for b in t["bands"]]
    assert pct == sorted(pct, reverse=True) and heel == sorted(heel, reverse=True)
    assert t["by_beat_heel_target"] == 16.0
    assert {(b["leg"], b["tack"]) for b in t["by_beat"]} == {
        (1, "stbd"),
        (1, "port"),
        (3, "stbd"),
        (3, "port"),
    }
    assert "Speed % target" in (tmp_path / "event.md").read_text()
    with pytest.raises(ValueError):
        analyze.parse_tws("10-8")


SATURDAY = ROOT / "samples/njord/2026-07-18_july-odw"


def test_markless_course_legs_detected_from_heel(tmp_path):
    """Saturday's courses have only a start/finish line, so Njord gives one leg per race."""
    res = analyze.run(
        [SATURDAY / "race1.csv", SATURDAY / "race3.csv"], tmp_path, tws=None, tz=None, plots=False
    )
    r1, r3 = res
    assert r1["legs_source"].startswith("detected")
    assert [lg["type"] for lg in r1["legs"]] == ["upwind", "downwind"] * 2
    assert [lg["type"] for lg in r3["legs"]] == ["upwind", "downwind"]
    # Njord's end time runs 10 min past the finish; the line crossing at 12:34 local wins
    assert r1["finish_local"].startswith("2026-07-18 12:34")
    assert all(lg["vmc_avg"] is None for lg in r1["legs"])  # VMC points at the finish here
    assert r1["start"]["line_pos_pct_from_pin"] is not None


def test_html_report(results, tmp_path):
    sys.path.insert(0, str(ROOT / "skill/sailing-coach/scripts"))
    from html_report import md_to_html, write_html

    out, _ = results
    debrief = tmp_path / "debrief.md"
    debrief.write_text(
        "## Debrief\n\n**Top 3**\n1. **Starts** — late.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    )
    page = write_html(out, debrief, tmp_path / "r.html").read_text()
    assert "<title>Mojo · July ODW · Sun 19 Jul 2026</title>" in page
    assert page.count("data:image/png;base64,") == 10  # 5 plots x 2 races
    assert '<section class="card debrief" id="debrief"><h2>Debrief</h2>' in page
    assert "<ol><li><strong>Starts</strong> — late.</li></ol>" in page
    # Every table row has as many cells as its header (a stray "|" would break this)
    import re

    for table in re.findall(r"<table>(.*?)</table>", page):
        rows = re.findall(r"<tr>(.*?)</tr>", table)
        widths = {len(re.findall(r"<t[hd]>", r)) for r in rows}
        assert len(widths) == 1, rows[0]
    assert "&lt;script&gt;" in md_to_html("<script>x</script>")


def test_tack_calls_from_heading_wind(results):
    _, r = results
    sh = r["race2"]["shifts"]
    beats = {b["leg"]: b for b in sh["beats"]}
    assert set(beats) == {1, 3}
    assert 275 < beats[1]["twd_median"] < 290
    assert beats[3]["pattern"] == "persistent left shift" and beats[3]["trend_deg"] < -3
    assert 4 < beats[1]["leeway_deg"] < 8
    calls = {analyze._fmt_mmss(c["time_s"]): c for c in sh["tacks"]}
    # 9:43: left a starboard tack that was lifted ~8 deg; 12:28: left a headed port tack
    assert calls["9:43"]["verdict_kind"] == "lift" and calls["9:43"]["headed_before_deg"] < -5
    assert calls["12:28"]["verdict_kind"] == "header"
    assert calls["0:34"]["verdict_kind"] == "start"
    # Final tack of each beat is judged as a layline, with leeway included
    assert calls["16:35"]["verdict_kind"] == "layline" and 4 < calls["16:35"]["overstand_deg"] < 10
    assert sum(sh["summary"].values()) == 14
    assert (results[0] / "race2" / "shifts.png").exists()
    # Race 1's double tack stays a double, not a shift call
    assert any(c["verdict_kind"] == "double" for c in r["race1"]["shifts"]["tacks"])


def test_tack_right_after_rounding_is_not_a_shift_call(tmp_path):
    res = analyze.run([SATURDAY / "race2.csv"], tmp_path, tws=None, tz=None, plots=False)
    calls = {analyze._fmt_mmss(c["time_s"]): c for c in res[0]["shifts"]["tacks"]}
    assert calls["27:52"]["verdict"] == "tack right after the leeward mark"
