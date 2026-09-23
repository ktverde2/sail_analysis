"""analyze.py against the real July ODW races in samples/."""

import re
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
            "downwind.png",
            "polar.png",
            "roundings.png",
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
    page = write_html(out, debrief, tmp_path / "r.html", interactive=False).read_text()
    assert "<title>Mojo · July ODW · Sun 19 Jul 2026</title>" in page
    assert page.count("data:image/png;base64,") == 16  # 8 plots x 2 races, each shown once
    assert '<section class="page" id="debrief">' in page
    assert '<section class="card debrief"><h2>Debrief</h2>' in page
    assert "<ol><li><strong>Starts</strong> — late.</li></ol>" in page
    # Every table row has as many cells as its header (a stray "|" would break this)
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


def test_executive_summary(results):
    out, _ = results
    ex = (out / "executive.md").read_text()
    assert ex.startswith("# Executive summary")
    assert "## Sun 19 Jul" in ex and "**Day:** 2 races" in ex
    assert "**Overall:**" not in ex  # one day only
    race2 = next(line for line in ex.splitlines() if line.startswith("- **Race 2**"))
    assert "13 s late, boat end" in race2 and "**Flags:** late start (13 s)" in race2
    race1 = next(line for line in ex.splitlines() if line.startswith("- **Race 1**"))
    assert "on the line at the gun" in race1 and "late start" not in race1


def test_executive_summary_spans_days(tmp_path):
    res = analyze.run(
        [SATURDAY / "race3.csv", SAMPLES / "race2.csv"], tmp_path, tws="8-10", tz=None, plots=False
    )
    ex = (tmp_path / "executive.md").read_text()
    assert "**Overall:** 2 races" in ex and "## Sat 18 Jul" in ex and "## Sun 19 Jul" in ex
    assert len(res) == 2


def test_leg_and_start_extras(results):
    _, r = results
    run = next(lg for lg in r["race2"]["legs"] if lg["type"] == "downwind")
    assert 150 < run["twa_steady"] < 180 and run["gybes"] == 2 and 5 < run["vmg_steady"] < 6.5
    beat = r["race2"]["legs"][0]
    assert 30 < beat["twa_steady"] < 45 and beat["tacks"] >= 8
    assert r["race2"]["start"]["last_maneuver_before_gun_s"] == -203
    assert r["race2"]["start"]["prestart_maneuvers_5min"] == 2


def test_html_pages(results, tmp_path):
    from html_report import write_html

    out, _ = results
    page = write_html(out, None, tmp_path / "p.html").read_text()
    ids = re.findall(r'<section class="page" id="([^"]+)"', page)
    assert ids == ["summary", "starts", "maneuvers", "upwind", "downwind", "roundings", "races"]
    assert "Executive summary" in page.split('id="starts"')[0]
    assert page.count("downwind.png") == 0  # embedded, not linked
    assert '<nav class="pages">' in page


def test_interactive_charts(results, tmp_path):
    from html_report import write_html

    out, _ = results
    page = write_html(out, None, tmp_path / "i.html").read_text()
    assert "data:image/png" not in page  # charts replace the PNGs
    kinds = re.findall(r'<div class="chart" data-chart="(\w+)" data-race="(\w+)"', page)
    assert ("startMap", "race2") in kinds and ("startTime", "race2") in kinds
    assert {k for k, _ in kinds} == {
        "startMap",
        "startTime",
        "maneuvers",
        "shifts",
        "downwind",
        "track",
        "timeline",
        "legTrack",
        "polar",
        "roundTrack",
        "roundSpeed",
    }
    assert (
        page.count('<script type="application/json" id="race-race') == 2
    )  # data embedded once per race
    assert "plotly.js (basic - minified)" in page and "window.renderCharts" in page
    import json

    d = json.loads((out / "race2" / "plotdata.json").read_text())
    s = d["series"]
    i = s["t"].index(-30)
    assert (s["sog"][i], s["below"][i], s["hdg"][i]) == (2.13, 42.0, 246)
    assert len({len(v) for v in s.values() if v is not None}) == 1  # all series aligned


def test_roundings(results):
    _, r = results
    r1 = r["race1"]["roundings"]
    assert [x["type"] for x in r1] == ["windward", "leeward", "windward"]
    assert [x["type"] for x in r["race2"]["roundings"]] == ["windward", "leeward"]
    lw = r["race2"]["roundings"][1]
    # Sunday race 2's leeward gate: slow, costly, left-hand mark looking downwind
    assert lw["metres_lost"] > 60 and lw["sog_min"] < 3.6 and lw["settle_s"] > 40
    assert lw["gate_side"].startswith("left-hand") and lw["mark_dist_m"] < 10
    ww = r1[0]
    assert ww["last_tack_before_s"] > 0 and ww["mark_dist_m"] < 10 and ww["gate_side"] is None
    for x in r1 + r["race2"]["roundings"]:
        assert x["settle_s"] is None or x["settle_s"] >= 10  # measured after the mark only


def test_polar_data(results):
    import json

    out, _ = results
    d = json.loads((out / "race1" / "plotdata.json").read_text())
    s = d["series"]
    assert len(s["steady"]) == len(s["t"]) and {0, 1} == set(s["steady"])
    assert [t["wind"] for t in d["targets"]] == []  # results fixture has no --tws
    up = [
        i
        for i in range(len(s["t"]))
        if s["leg"][i] == 1 and s["steady"][i] and s["twa"][i] is not None
    ]
    assert 30 < sum(s["twa"][i] for i in up) / len(up) < 40


def test_rounding_vs_best_and_tips(results):
    _, r = results
    rs = r["race1"]["roundings"] + r["race2"]["roundings"]
    for kind in ("windward", "leeward"):
        k = [x for x in rs if x["type"] == kind]
        best = min(k, key=lambda x: x["metres_lost"])
        assert best["is_best"] and best["vs_best_m"] == 0
        assert all(x["vs_best_m"] == round(x["metres_lost"] - best["metres_lost"], 1) for x in k)
    for x in rs:
        assert x["lost_before_m"] + x["lost_after_m"] == pytest.approx(x["metres_lost"], abs=0.2)
        assert 1 <= len(x["tips"]) <= 4
    lw2 = r["race2"]["roundings"][1]  # the 84 m leeward gate
    assert any("low and wide" in t for t in lw2["tips"]) and any("80 s" in t for t in lw2["tips"])
    assert not any("pinching" in t for t in r["race1"]["roundings"][1]["tips"])  # same exit as best


def test_leg_distance(results):
    _, r = results
    for lg in r["race1"]["legs"] + r["race2"]["legs"]:
        assert lg["straight_nm"] < lg["distance_sailed_nm"]
        if lg["type"] == "upwind":
            assert 20 < lg["extra_pct"] < 45 and 35 < lg["track_angle"] < 45
            assert lg["straight_nm"] < lg["ideal_nm"] < lg["distance_sailed_nm"] + 0.05
        else:
            assert 0 < lg["extra_pct"] < 10 and lg["track_angle"] < 25


def test_cdn_html_loads_plotly_online(results, tmp_path):
    from html_report import PLOTLY_CDN, write_html

    out, _ = results
    inline = write_html(out, None, tmp_path / "inline.html").read_text()
    page = write_html(out, None, tmp_path / "cdn.html", cdn=True).read_text()
    assert f'<script src="{PLOTLY_CDN}"></script>' in page
    assert 'data-chart="roundTrack"' in page
    assert len(page) < len(inline) - 1_000_000  # library not inlined
    # the library's download helpers stay out of the page
    for pattern in ("createObjectURL", "msSaveBlob", "atob(", "new Function", "fromCharCode"):
        assert pattern in inline
        assert pattern not in page


def test_nested_lists_and_coach_summary(results, tmp_path):
    from html_report import debrief_points, md_to_html, write_html

    out, _ = results
    md = """**What went well**
- **Fast upwind**
  - 101% of target

**Top 3 to work on**
1. **Roundings**
   - lost 44 m
   - **Next time:** bear away first.
   - **Drill:** six in a row.
2. **Starts** late by 5 s.

*Also worth a look:*
- **Tacks** deep
"""
    html = md_to_html(md)
    assert "<ol><li><strong>Roundings</strong><ul><li>lost 44 m</li>" in html
    assert "<li><strong>Fast upwind</strong><ul><li>101% of target</li></ul></li>" in html
    pts = debrief_points(md)
    assert pts["well"] == [("Fast upwind", "")]
    assert pts["work"] == [("Roundings", "bear away first."), ("Starts", "")]
    debrief = tmp_path / "debrief.md"
    debrief.write_text(md)
    page = write_html(out, debrief, tmp_path / "c.html").read_text()
    summary = page[page.index('id="summary"') : page.index('id="debrief"')]
    assert "Coach&#x27;s summary" in summary or "Coach's summary" in summary
    assert "<strong>Next time:</strong> Bear away first." in summary
    assert "<strong>Tacks</strong>" not in summary  # "also worth a look" stays on Debrief
