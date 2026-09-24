"""analyze.py and fleet.py against PCC 2026 day 1 (three boats) in samples/."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "skill/sailing-coach/scripts"))
import analyze
import fleet

PCC = ROOT / "samples/njord/2026-02-21_pcc"
BOATS = ("1044", "chomp", "mojo")


@pytest.fixture(scope="module")
def pcc(tmp_path_factory):
    reports = tmp_path_factory.mktemp("pcc")
    res = {
        b: {
            r["stem"]: r
            for r in analyze.run(
                sorted((PCC / b).glob("race*.csv")), reports / b, tws=None, tz=None, plots=False
            )
        }
        for b in BOATS
    }
    return reports, res


def test_legs_from_course_when_njord_split_is_incomplete(pcc):
    _, res = pcc
    r = res["chomp"]["race2"]  # Njord's Leg column stops at leg 2 for Chomp here
    assert r["legs_source"].startswith("from the course marks")
    assert [lg["type"] for lg in r["legs"]] == ["upwind", "downwind", "upwind", "downwind"]
    assert 51 <= r["duration_min"] <= 52  # finish-line crossing, not the end of the data
    assert res["mojo"]["race2"]["legs_source"] == "Njord course"  # complete split: Njord's


def test_restart_after_being_over_at_the_gun(pcc):
    _, res = pcc
    s = res["mojo"]["race2"]["start"]
    assert s["ocs_at_gun_m"] == 2.5
    assert s["ocs_returned_s"] is not None and 0 < s["ocs_returned_s"] < s["late_s"]
    assert s["late_s"] == pytest.approx(10, abs=1)
    assert analyze.ocs_label(s).startswith("over 2.5 m, restarted +")


def test_model_feed_wind_is_not_trusted(pcc):
    _, res = pcc
    w = res["mojo"]["race1"]["wind"]
    assert w["trusted"] is False and "model feed" in w["reason"]
    assert res["mojo"]["race1"]["targets"]["available"] is False


def test_tacks_detected_from_heading_and_heel(pcc):
    _, res = pcc
    mans = res["chomp"]["race1"]["maneuvers"]  # Chomp's files have no Njord tack events
    tacks = [m for m in mans if m["kind"] == "Tack"]
    assert len(tacks) >= 6
    assert {m["onto"] for m in tacks} == {"Port", "Stbd"}


def test_offset_rounding_measured_from_the_offset(pcc):
    _, res = pcc
    ww = [r for r in res["1044"]["race1"]["roundings"] if r["type"] == "windward"]
    assert all(15 <= r["offset_s"] <= 40 for r in ww)
    assert all(r["exit_twa"] > 140 for r in ww)  # measured on the run, not the offset reach


def test_fleet_results_and_time_split(pcc):
    reports, _ = pcc
    fa = fleet.fleet_analysis(fleet.load_fleet(PCC, reports))
    day = {d["name"]: d for d in fa["day"].values()}
    assert day["1044"]["places"] == [1, 1, 1]
    assert day["Chomp"]["places"] == [2, 2, 2]
    assert day["Mojo"]["places"] == [3, 3, 3]
    for r in fa["races"]:
        assert r["marks"][-1] == "Finish"
        for b in r["boats"].values():
            # start + upwind + downwind adds up to the finishing gap (rounding: 1 s per part)
            assert abs(sum(b["time_split_s"].values()) - b["gap_s"]) <= 3
            assert len(b["gaps_s"]) == len(r["marks"])
    assert fa["races"][2]["marks"] == [
        "Windward 1",
        "Leeward 1",
        "Windward 2",
        "Leeward 2",
        "Finish",
    ]


def test_fleet_outputs(pcc, tmp_path):
    reports, _ = pcc
    boats = fleet.load_fleet(PCC, reports)
    fa = fleet.fleet_analysis(boats)
    md = fleet.write_md(fa, tmp_path, "PCC")
    assert (
        "## Where the time went" in md
        and "| 1044 | 1 | 1 | 1 | 3 |" in md
        and "## Order among the tracked boats" in md
    )
    debrief = "**What went well**\n- **1044: fast**\n\n**Top 3 to work on**\n1. **Mojo: sides**\n   - **Next time:** stay central.\n"
    page = fleet.write_html(fa, md, tmp_path, "PCC", debrief, reports, boats, cdn=True).read_text()
    assert 'data-fleet="tracks"' in page and 'data-fleet="gaps"' in page
    assert "Next time:</strong> Stay central." in page
    data = json.loads(page.split('id="fleet-data">')[1].split("</script>")[0].replace("<\\/", "</"))
    assert len(data["races"]) == 3


def test_debrief_chart_placeholders(pcc, tmp_path):
    reports, _ = pcc
    boats = fleet.load_fleet(PCC, reports)
    fa = fleet.fleet_analysis(boats)
    md = fleet.write_md(fa, tmp_path, "PCC")
    debrief = (
        "**What went well**\n- **1044: fast**\n\n[[chart:split]]\n\n"
        "**Top 3 to work on**\n1. **Mojo: sides**\n   - **Next time:** stay central.\n\n"
        "[[chart:angles]]\n[[chart:nonsense]]\n"
    )
    page = fleet.write_html(fa, md, tmp_path, "PCC", debrief, reports, boats, cdn=True).read_text()
    debrief_page = page[page.index('id="debrief"') :]
    assert '<div class="chart" data-fleet="split"></div>' in debrief_page
    assert '<div class="chart" data-fleet="angles"></div>' in debrief_page
    assert "nonsense" not in debrief_page and "[[chart:" not in page
    # the coach's summary still finds both sections around the chart line
    assert "Next time:</strong> Stay central." in page
    for r in fa["races"]:
        for b in r["boats"].values():
            beats = [lg for lg in b["legs"] if lg["type"] == "upwind"]
            assert all(lg["ta_cog"] >= lg["ta_heading"] - 1 for lg in beats if "ta_cog" in lg)


def test_official_results(pcc):
    reports, _ = pcc
    fa = fleet.fleet_analysis(fleet.load_fleet(PCC, reports))
    official = json.loads((PCC / "official.json").read_text())
    fleet.add_official(fa, official, {"1044": "1044", "chomp": "905", "mojo": "1315"})
    off = fa["official"]
    assert off["fleet_size"] == 42 and off["race_numbers"] == [1, 2, 3]
    assert off["race_winners"] == ["Lifted", "DanEgerous", "Bayou Hustler"]
    assert off["boats"]["mojo"]["places"] == [31, 29, 38]
    assert off["boats"]["chomp"]["places"] == [29, 22, 29] and off["boats"]["chomp"]["corinthian"]
    assert off["boats"]["1044"]["day_total"] == 45
    # the official order matches the order in the tracks in every race
    for r, n in zip(fa["races"], off["race_numbers"], strict=True):
        by_gps = sorted(r["boats"], key=lambda k: r["boats"][k]["finish_s"])
        by_official = sorted(r["boats"], key=lambda k: off["boats"][k]["all_places"][n - 1])
        assert by_gps == by_official
    assert 5 <= off["s_per_place"][len(off["s_per_place"]) // 2] <= 15
    md = fleet.write_md(fa, Path(reports), "PCC")
    assert (
        "## Official results (42 boats, YachtScoring)" in md and "| 12 | 16 | 17 | 45 | 14 |" in md
    )
