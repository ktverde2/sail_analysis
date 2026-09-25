"""analyze.py and fleet.py against PCC 2026 (three boats, five races) in samples/."""

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


def test_restart_when_over_by_less_than_a_metre(pcc):
    _, res = pcc
    r = res["1044"]["race5"]  # 0.8 m over at the gun, went back, restarted
    s = r["start"]
    assert s["ocs_at_gun_m"] == 0.8
    assert s["ocs_returned_s"] == pytest.approx(36, abs=3)
    assert s["late_s"] == pytest.approx(56, abs=3)
    # the first beat's shift analysis starts at the restart, not during the return
    beat1 = r["shifts"]["beats"][0]
    assert beat1["missed_headers"] == []
    # a boat on the line that never went back is still on time
    assert res["chomp"]["race2"]["start"]["late_s"] == 0.0
    assert "ocs_at_gun_m" not in res["chomp"]["race2"]["start"]


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
    assert day["1044"]["places"] == [1, 1, 1, 1, 1]
    assert day["Chomp"]["places"] == [2, 2, 2, 2, 3]  # race 4: 2 s ahead of Mojo by GPS
    assert day["Mojo"]["places"] == [3, 3, 3, 3, 2]
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
        and "| 1044 | 1 | 1 | 1 | 1 | 1 | 5 |" in md
        and "## Order among the tracked boats" in md
    )
    debrief = "**What went well**\n- **1044: fast**\n\n**Top 3 to work on**\n1. **Mojo: sides**\n   - **Next time:** stay central.\n"
    page = fleet.write_html(fa, md, tmp_path, "PCC", debrief, reports, boats, cdn=True).read_text()
    assert '<div class="replay" id="replay-race1" data-race="race1"></div>' in page
    assert 'data-fleet="gaps"' in page and 'data-fleet="tracks"' not in page
    assert 'id="pairs"' in page and 'data-fleet="pair"' in page
    assert 'data-watch="race1"' in page and "## Side by side" in md
    assert page.count('class="pairmap"') == 1 + len(fa["pairs"])  # overall map + one per pair
    assert "**Where the stretches were**" in md and "| R1-1 | Race 1 |" in md
    n = sum(len(r["pairs"]) for r in fa["races"])
    assert page.count('<details class="stretch"') == n == page.count('data-fleet="stretch"')
    assert page.count('href="#stretch-R') == n and 'id="stretch-R1-1"' in page
    assert "### Why, stretch by stretch" in md and "- **R1-1** (Race 1," in md
    assert "Next time:</strong> Stay central." in page
    data = json.loads(page.split('id="fleet-data">')[1].split("</script>")[0].replace("<\\/", "</"))
    assert len(data["races"]) == 5


def test_debrief_chart_placeholders(pcc, tmp_path):
    reports, _ = pcc
    boats = fleet.load_fleet(PCC, reports)
    fa = fleet.fleet_analysis(boats)
    md = fleet.write_md(fa, tmp_path, "PCC")
    debrief = (
        "**What went well**\n- **1044: fast**\n\n[[chart:split]]\n\n"
        "**Top 3 to work on**\n1. **Mojo: sides**\n   - **Next time:** stay central.\n\n"
        "[[chart:angles]]\n[[chart:nonsense]]\n[[chart:replay]]\n[[chart:pairmap]]\n"
    )
    page = fleet.write_html(fa, md, tmp_path, "PCC", debrief, reports, boats, cdn=True).read_text()
    debrief_page = page[page.index('id="debrief"') :]
    assert '<div class="chart" data-fleet="split"></div>' in debrief_page
    assert '<div class="chart" data-fleet="angles"></div>' in debrief_page
    assert '<div class="replay"></div>' in debrief_page  # replay with a button per race
    assert '<div class="pairmap"></div>' in debrief_page  # where the boats sailed side by side
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
    assert off["fleet_size"] == 42 and off["race_numbers"] == [1, 2, 3, 4, 5]
    assert off["race_winners"] == [
        "Lifted",
        "DanEgerous",
        "Bayou Hustler",
        "Stark Raving Mad",
        "Lifted",
    ]
    assert off["boats"]["mojo"]["places"] == [31, 29, 38, 30, 29]
    assert off["boats"]["chomp"]["places"] == [29, 22, 29, 31, 31]
    assert off["boats"]["chomp"]["corinthian"]
    assert off["boats"]["1044"]["days"]["2026-02-21"] == {"total": 45, "rank": 14}
    # the official order matches the GPS finish order, except near-ties (under 5 s)
    for r, n in zip(fa["races"], off["race_numbers"], strict=True):
        bs = r["boats"]
        for i in bs:
            for j in bs:
                pi, pj = off["boats"][i]["all_places"][n - 1], off["boats"][j]["all_places"][n - 1]
                if pi < pj:
                    assert bs[i]["finish_s"] < bs[j]["finish_s"] + 5
    assert 5 <= off["s_per_place"][len(off["s_per_place"]) // 2] <= 15
    md = fleet.write_md(fa, Path(reports), "PCC")
    assert (
        "## Official results (42 boats, YachtScoring)" in md
        and "| 12 | 16 | 17 | 18 | 26 | 45 (14) | 44 (" in md
    )


def test_side_by_side(pcc):
    reports, res = pcc
    fa = fleet.fleet_analysis(fleet.load_fleet(PCC, reports))
    pairs = [(r, p) for r in fa["races"] for p in r["pairs"]]
    assert len(pairs) > 30
    for r, p in pairs:
        assert p["duration_s"] >= fleet.PAIR_MIN_S
        assert max(p["apart_m"]) <= fleet.PAIR_RADIUS_M
        assert p["leg_type"] == r["leg_types"][p["leg"]]
        assert p["gain_speed_m"] + p["gain_angle_m"] == p["gain_m"]
        assert p["side"] in ("starboard tack", "port tack", "starboard gybe", "port gybe")
        assert p["leg_type"] == "upwind" or p["side"].endswith("gybe")
        for bid in (p["a"], p["b"]):  # only once both boats are racing (after any restart)
            assert p["t0"] >= (res[bid][r["stem"]]["start"].get("late_s") or 0)
        assert p["id"].startswith("R" + r["stem"][4:] + "-")
        w = p["where"]
        assert w["along"] in ("first third", "middle third", "last third")
        assert w["side"] in ("left", "middle", "right")
        assert (w["side"] == "middle") == (abs(w["xte_m"]) <= fleet.PAIR_MIDDLE_M)
        # second by second: the gain adds up to the stretch's total, split the same way
        d = p["detail"]
        assert len(d["t"]) == p["duration_s"] + 1 == len(d["gain"]) == len(d["shadow"])
        for bid in (p["a"], p["b"]):
            assert all(len(v) == len(d["t"]) for v in d["boats"][bid].values())
            mean_angle = p["why"]["stats"][bid]["angle"][0]
            assert abs(mean_angle - p["boats"][bid]["angle"]) <= 2
        assert abs(d["gain"][-1] - p["gain_m"]) <= 2
        assert abs(d["gain_speed"][-1] + d["gain_angle"][-1] - d["gain"][-1]) <= 0.2
        assert abs(d["gain_speed"][-1] - p["gain_speed_m"]) <= 4
        if min(p["apart_m"]) > fleet.SHADOW_M + 60:
            assert not any(d["shadow"])
        why = fleet.why_sentence(fa, p)
        g = abs(p["gain_m"])
        winner = fa["day"][p["a"] if p["gain_m"] >= 0 else p["b"]]["name"]
        assert why.startswith("About level" if g < 8 else f"{winner} gained {g} m")
        if g >= 8:
            big = (
                "speed"
                if abs(p["gain_speed_m"]) >= abs(p["gain_angle_m"])
                else ("height" if p["leg_type"] == "upwind" else "depth")
            )
            assert why.split(": ", 1)[1].split("(")[0].strip().endswith(big)
    zones = fleet.where_summary(fa)
    assert sum(z["n"] for z in zones) == len(pairs)
    tot = {(x["a"], x["b"]): x for x in fa["pairs"]}
    up = tot[("1044", "chomp")]["upwind"]
    assert up["gain_m"] > 0 and up["gain_angle_m"] > 0 > up["gain_speed_m"]  # higher, slower
    up = tot[("chomp", "mojo")]["upwind"]
    assert up["gain_m"] < 0 and up["gain_angle_m"] < 0  # Mojo points higher than Chomp
    assert all(
        x[k]["duration_s"]
        == sum(p["duration_s"] for _, p in pairs if (p["a"], p["b"]) == key and p["leg_type"] == k)
        for key, x in tot.items()
        for k in ("upwind", "downwind")
        if x[k]
    )
