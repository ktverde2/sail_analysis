#!/usr/bin/env python3
"""Compare boats that sailed the same races: results, gaps at every mark, leg gains and losses,
starts, upwind and downwind speed, sides of the course, and roundings.

Usage:
  python fleet.py --data <dir> --reports <dir> --out <dir> [--html] [--debrief fleet.md]
                  [--title "PCC 2026 · Day 1"] [--cdn]

--data holds one folder per boat with Njord exports (raceN.csv plus raceN-race.json with the
course); --reports holds the matching analyze.py output per boat (same folder names). Races are
matched across boats by file stem (race1, race2, ...). Mark passages come from the course
geometry for every boat (analyze.course_legs), so all boats are split the same way.

Writes <out>/fleet.md and <out>/fleet.json; with --html also <out>/fleet.html (interactive track
overlays and gap charts, the fleet debrief, and each boat's coach's summary from
<reports>/<boat>/debrief.md when present).
"""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path

import analyze as A
import html_report as H
import numpy as np
import pandas as pd

BOAT_COLORS = ["#2a78d6", "#eb6834", "#1f9d74", "#8a5cd1", "#c7a100", "#d6336c"]
SERIES_STEP_S = 2  # track overlay resolution


# ---------------------------------------------------------------- loading


def load_fleet(data_dir: Path, reports_dir: Path, tz: str | None = None) -> list[dict]:
    boats = []
    for d in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        races = {}
        for csv in sorted(d.glob("race*.csv")):
            summ = reports_dir / d.name / csv.stem / "summary.json"
            if not summ.exists():
                continue
            races[csv.stem] = {
                "race": A.load_race(csv, tz),
                "summary": json.loads(summ.read_text()),
            }
        if races:
            name = next(iter(races.values()))["summary"].get("boat") or d.name
            boats.append({"id": d.name, "name": name, "races": races})
    for i, b in enumerate(boats):
        b["color"] = BOAT_COLORS[i % len(BOAT_COLORS)]
    return boats


# ---------------------------------------------------------------- geometry


def _mid(el: dict) -> tuple[float, float]:
    pts = A._course_points(el)
    return float(np.mean([p[0] for p in pts])), float(np.mean([p[1] for p in pts]))


def frame(race: A.Race):
    """Local metres with the start-line middle at the origin and the upwind axis pointing up."""
    line = next(c for c in race.course if c["type"] == "StartLine")
    lat0, lon0 = _mid(line)
    axis = A.upwind_axis(race) or 0.0
    rot = math.radians(axis)

    def xy(lat, lon):
        x, y = A.local_xy(lat, lon, lat0, lon0)
        # rotate so bearing `axis` becomes north (up)
        return x * math.cos(rot) - y * math.sin(rot), x * math.sin(rot) + y * math.cos(rot)

    return xy


def leg_labels(legs: list[dict]) -> list[str]:
    out, nw, nl = [], 0, 0
    for i, lg in enumerate(legs):
        if i == len(legs) - 1:
            out.append("Finish")
        elif lg["type"] == "upwind":
            nw += 1
            out.append(f"Windward {nw}")
        else:
            nl += 1
            out.append(f"Leeward {nl}")
    return out


def side_of_rhumb(g: pd.DataFrame, a: tuple, b: tuple, xy) -> dict:
    """Signed distance from the rhumb line a->b (right of it, looking at b, is positive)."""
    ax, ay = xy(*a)
    bx, by = xy(*b)
    px, py = xy(g.Lat.to_numpy(), g.Lon.to_numpy())
    L = math.hypot(bx - ax, by - ay) or 1.0
    ux, uy = (bx - ax) / L, (by - ay) / L
    xte = (px - ax) * uy - (py - ay) * ux  # >0 = right of the rhumb looking at the mark
    return {
        "pct_right": round(float((xte > 0).mean() * 100)),
        "mean_xte_m": round(float(np.mean(xte))),
        "max_right_m": round(float(max(np.max(xte), 0))),
        "max_left_m": round(float(max(-np.min(xte), 0))),
    }


# ---------------------------------------------------------------- per race


def race_fleet(stem: str, entries: list[tuple[dict, dict]]) -> dict:
    """entries: (boat, {"race", "summary"}) for every boat that sailed this race."""
    boats = {}
    ref_legs = course_xy = None
    race_name, gun_local = stem, None
    for boat, e in entries:
        race, summ = e["race"], e["summary"]
        legs = A.course_legs(race)
        if not legs:
            continue
        ref_legs = ref_legs or legs
        df = race.df
        xy = frame(race)
        marks = [c for c in race.course if c["type"] in ("Mark", "Gate")]
        start_line = next(c for c in race.course if c["type"] == "StartLine")
        finish = next((c for c in race.course if c["type"] == "FinishLine"), None)
        targets = [_mid(m) for m in marks] + ([_mid(finish)] if finish else [])
        origin = [_mid(start_line)] + [_mid(m) for m in marks]
        mans = summ.get("maneuvers") or []
        leg_rows = []
        for i, lg in enumerate(legs):
            g = df[(df.t >= lg["start"]) & (df.t <= lg["end"])]
            s0 = (lg["start"] - race.gun).total_seconds()
            s1 = (lg["end"] - race.gun).total_seconds()
            lat, lon = g.Lat.to_numpy(), g.Lon.to_numpy()
            seg = A.dist_m(lat[:-1], lon[:-1], lat[1:], lon[1:])
            sailed = float(np.nansum(seg)) / A.NM
            straight = float(A.dist_m(*origin[i], *targets[i])) / A.NM if i < len(targets) else None
            row = {
                "leg": i + 1,
                "type": lg["type"],
                "start_s": round(s0),
                "end_s": round(s1),
                "duration_s": round(s1 - s0),
                "sog_avg": round(float(g.SOG.mean()), 2),
                "heel_abs_avg": round(float(g.Heel.abs().mean()), 1) if "Heel" in g else None,
                "sailed_nm": round(sailed, 2),
                "straight_nm": round(straight, 2) if straight else None,
                "extra_pct": round(100 * (sailed / straight - 1), 1) if straight else None,
                "tacks": sum(m["kind"] == "Tack" and s0 <= m["time_s"] <= s1 for m in mans),
                "gybes": sum(m["kind"] == "Gybe" and s0 <= m["time_s"] <= s1 for m in mans),
            }
            if i < len(targets):
                row.update(side_of_rhumb(g, origin[i], targets[i], xy))
            leg_rows.append(row)
        # summary legs (steady speed, tacking angle, heel) in beat/run order
        by_type = {"upwind": [], "downwind": []}
        for lg in summ.get("legs") or []:
            by_type.setdefault(lg["type"], []).append(lg)
        seen = {"upwind": 0, "downwind": 0}
        for row in leg_rows:
            k = seen[row["type"]]
            seen[row["type"]] += 1
            src = by_type.get(row["type"], [])
            if k < len(src):
                for key in ("sog_steady", "vmg_steady", "tacking_angle", "heel_abs_std"):
                    row[key] = src[k].get(key)
        # series for the track overlay
        w = df[
            (df.t >= race.gun - pd.Timedelta(seconds=60))
            & (df.t <= legs[-1]["end"] + pd.Timedelta(seconds=30))
            & df.Lat.notna()
        ].iloc[::SERIES_STEP_S]
        x, y = xy(w.Lat.to_numpy(), w.Lon.to_numpy())
        boats[boat["id"]] = {
            "name": boat["name"],
            "color": boat["color"],
            "passes_s": [r["end_s"] for r in leg_rows],
            "legs": leg_rows,
            "start": summ.get("start") or {},
            "roundings": summ.get("roundings") or [],
            "maneuver_summary": summ.get("maneuver_summary") or {},
            "series": {
                "t": [round(v) for v in (w.t - race.gun).dt.total_seconds()],
                "x": [round(float(v), 1) for v in x],
                "y": [round(float(v), 1) for v in y],
                "sog": [None if pd.isna(v) else round(float(v), 2) for v in w.SOG],
            },
        }
        if course_xy is None:
            course_xy = [
                {"type": c["type"], "pts": [list(map(float, xy(*p))) for p in A._course_points(c)]}
                for c in race.course
            ]
            race_name, gun_local = summ.get("race", stem), summ.get("gun_local")
    if not boats:
        return {}
    labels = leg_labels(ref_legs)
    n = min(len(b["passes_s"]) for b in boats.values())
    labels = labels[:n]
    # ranks and gaps at each mark
    for j in range(n):
        order = sorted(boats, key=lambda k: boats[k]["passes_s"][j])
        lead = boats[order[0]]["passes_s"][j]
        for rank, k in enumerate(order, 1):
            boats[k].setdefault("ranks", []).append(rank)
            boats[k].setdefault("gaps_s", []).append(boats[k]["passes_s"][j] - lead)
    # leg deltas vs the fastest boat on that leg
    for j in range(n):
        best = min(b["legs"][j]["duration_s"] for b in boats.values())
        for b in boats.values():
            b["legs"][j]["vs_best_s"] = b["legs"][j]["duration_s"] - best
    for b in boats.values():
        b["place"] = b["ranks"][-1]
        b["finish_s"] = b["passes_s"][n - 1]
        b["gap_s"] = b["gaps_s"][-1]
    # where the time went, against the race winner so the parts add up to the finishing gap:
    # start = line crossing later than the winner's; leg 1 runs from each boat's own crossing
    win = min(boats.values(), key=lambda b: b["finish_s"])
    for b in boats.values():
        cross = b["start"].get("late_s") or 0.0
        wcross = win["start"].get("late_s") or 0.0
        split = {"start": cross - wcross, "upwind": 0.0, "downwind": 0.0}
        for j in range(n):
            mine = b["passes_s"][j] - (b["passes_s"][j - 1] if j else cross)
            theirs = win["passes_s"][j] - (win["passes_s"][j - 1] if j else wcross)
            split[b["legs"][j]["type"]] += mine - theirs
        b["time_split_s"] = {k: round(v) for k, v in split.items()}
    return {
        "stem": stem,
        "race": race_name,
        "gun_local": gun_local,
        "marks": labels,
        "leg_types": [lg["type"] for lg in ref_legs[:n]],
        "course": course_xy,
        "boats": boats,
    }


def fleet_analysis(boats: list[dict]) -> dict:
    stems = sorted({s for b in boats for s in b["races"]}, key=lambda s: (len(s), s))
    races = []
    for stem in stems:
        entries = [(b, b["races"][stem]) for b in boats if stem in b["races"]]
        r = race_fleet(stem, entries)
        if r:
            races.append(r)
    day = {}
    for b in boats:
        rs = [r["boats"][b["id"]] for r in races if b["id"] in r["boats"]]
        day[b["id"]] = {
            "name": b["name"],
            "color": b["color"],
            "places": [x["place"] for x in rs],
            "points": sum(x["place"] for x in rs),
            "total_gap_s": sum(x["gap_s"] for x in rs),
            "split_s": {
                k: sum(x["time_split_s"][k] for x in rs) for k in ("start", "upwind", "downwind")
            },
        }
    return {"races": races, "day": day}


# ---------------------------------------------------------------- markdown


def _mmss(s):
    if s is None:
        return "–"
    s = round(s)
    return f"{'-' if s < 0 else ''}{abs(s) // 60}:{abs(s) % 60:02d}"


def _plus(s):
    return "–" if s is None else "0" if s == 0 else f"+{_mmss(s)}"


def write_md(fa: dict, out: Path, title: str) -> str:
    day = fa["day"]
    order = sorted(day, key=lambda k: (day[k]["points"], day[k]["total_gap_s"]))
    L = [f"# {title}: fleet comparison", ""]
    L += [
        "## Results",
        "",
        "| Boat | "
        + " | ".join(r["race"] for r in fa["races"])
        + " | Points | Time behind the winner |",
        "|---|" + "---|" * len(fa["races"]) + "---|---|",
    ]
    for k in order:
        d = day[k]
        L.append(
            f"| {d['name']} | "
            + " | ".join(str(p) for p in d["places"])
            + f" | {d['points']} | {_plus(d['total_gap_s'])} |"
        )
    L += [
        "",
        (
            "Places and gaps from each boat's finish-line crossing (mark passages from the course "
            "geometry, the same way for every boat)."
        ),
        "",
        "## Where the time went (day total, seconds behind each race's winner)",
        "",
        (
            "Against each race's winner, so the three parts add up to the finishing gap. Start: "
            "crossing the line later than the winner. Upwind and downwind: time lost (or gained, "
            "negative) on those legs, counting leg 1 from each boat's own line crossing; "
            "roundings sit inside the legs."
        ),
        "",
        "| Boat | Start | Upwind legs | Downwind legs | Total |",
        "|---|---|---|---|---|",
    ]
    for k in order:
        s = day[k]["split_s"]
        L.append(
            f"| {day[k]['name']} | {s['start']} | {s['upwind']} | {s['downwind']} | "
            f"{s['start'] + s['upwind'] + s['downwind']} |"
        )
    for r in fa["races"]:
        bs = r["boats"]
        ids = sorted(bs, key=lambda k: bs[k]["place"])
        L += ["", f"## {r['race']} ({(r['gun_local'] or '')[11:16]})", ""]
        L += [
            "**Gap to the leader at each mark**",
            "",
            "| Boat | " + " | ".join(r["marks"]) + " |",
            "|---|" + "---|" * len(r["marks"]) + "",
        ]
        for k in ids:
            L.append(
                f"| {bs[k]['name']} | "
                + " | ".join(
                    f"{_plus(g)} ({rk})"
                    for g, rk in zip(bs[k]["gaps_s"], bs[k]["ranks"], strict=False)
                )
                + " |"
            )
        L += ["", "Gap in min:s, place at that mark in brackets.", ""]
        L += [
            "**Start**",
            "",
            "| Boat | Late (s) | Line pos (from pin) | SOG at gun | Accel ±5 s | Back at −60 s |",
            "|---|---|---|---|---|---|",
        ]
        for k in ids:
            s = bs[k]["start"]
            late = (
                A.ocs_label(s)
                if s.get("ocs_at_gun_m")
                else "on the line"
                if s.get("late_s") == 0
                else s.get("late_s")
            )
            L.append(
                f"| {bs[k]['name']} | {late} | {s.get('line_pos_pct_from_pin')}% | "
                f"{s.get('sog_+0s')} kt | {s.get('accel_pm5s_kt')} kt | "
                f"{s.get('below_line_-60s_m')} m |"
            )
        L += [
            "",
            "**Legs**",
            "",
            (
                "| Leg | Boat | Time | vs fastest | SOG | Sailed / straight nm | Tacks | Gybes | "
                "% right of rhumb | Heel |"
            ),
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for j, lab in enumerate(r["marks"]):
            for k in ids:
                lg = bs[k]["legs"][j]
                L.append(
                    f"| {j + 1} {lg['type']} to {lab} | {bs[k]['name']} | {_mmss(lg['duration_s'])} | "
                    f"{_plus(lg['vs_best_s'])} | {lg['sog_avg']} | "
                    f"{lg['sailed_nm']} / {lg['straight_nm']} | {lg['tacks']} | {lg['gybes']} | "
                    f"{lg.get('pct_right', '–')} | {lg['heel_abs_avg']}° |"
                )
        L += [
            "",
            "**Roundings (metres lost, each boat against its own steady VMG)**",
            "",
            "| Boat | " + " | ".join(r["marks"][: len(bs[ids[0]]["roundings"])]) + " |",
            "|---|" + "---|" * len(bs[ids[0]]["roundings"]),
        ]
        for k in ids:
            L.append(
                f"| {bs[k]['name']} | "
                + " | ".join(
                    "–" if x.get("metres_lost") is None else f"{x['metres_lost']:.0f}"
                    for x in bs[k]["roundings"]
                )
                + " |"
            )
    L += [
        "",
        (
            "*Speeds are over ground. % right of rhumb: share of the leg spent right of the line "
            "from the previous mark to the next, looking at the mark.*"
        ),
    ]
    text = "\n".join(L) + "\n"
    (out / "fleet.md").write_text(text)
    return text


# ---------------------------------------------------------------- html


FLEET_JS = """
(function () {
  function css(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function clock(t) {
    const a = Math.abs(Math.round(t)), s = Math.floor(a / 60) + ':' + String(a % 60).padStart(2, '0');
    return t < 0 ? '−' + s + ' to gun' : '+' + s;
  }
  const FLEET = JSON.parse(document.getElementById('fleet-data').textContent);
  const CONFIG = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ['toImage', 'lasso2d', 'select2d'] };
  function base(title) {
    const ink = css('--ink'), line = css('--line');
    return {
      title: { text: title, font: { size: 14, color: ink } },
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      font: { color: ink, size: 12 }, margin: { l: 50, r: 10, t: 36, b: 40 },
      legend: { orientation: 'h', y: -0.15 },
      xaxis: { gridcolor: line, zerolinecolor: line }, yaxis: { gridcolor: line, zerolinecolor: line },
    };
  }
  const CHARTS = {
    tracks(el, r) {
      const traces = [];
      for (const c of r.course) {
        const xs = c.pts.map(p => p[0]), ys = c.pts.map(p => p[1]);
        traces.push({ x: xs, y: ys, mode: c.pts.length > 1 ? 'lines+markers' : 'markers',
          marker: { size: 9, color: css('--ink2'), symbol: c.type === 'Offset' ? 'circle-open' : 'diamond' },
          line: { color: css('--ink2'), dash: 'dot' }, name: c.type, showlegend: false, hoverinfo: 'name' });
      }
      for (const id of Object.keys(r.boats)) {
        const b = r.boats[id], s = b.series;
        traces.push({ x: s.x, y: s.y, mode: 'lines', name: b.name, line: { color: b.color, width: 2 },
          customdata: s.t.map((t, i) => [clock(t), s.sog[i]]),
          hovertemplate: '<b>' + b.name + '</b> %{customdata[0]}<br>SOG %{customdata[1]} kt<extra></extra>' });
      }
      const lay = base(r.race + ': tracks (upwind is up)');
      lay.xaxis.scaleanchor = 'y'; lay.xaxis.title = 'm'; lay.yaxis.title = 'm'; lay.height = 560;
      Plotly.newPlot(el, traces, lay, CONFIG);
    },
    gaps(el, r) {
      const traces = Object.keys(r.boats).map(id => {
        const b = r.boats[id];
        return { x: r.marks, y: b.gaps_s, mode: 'lines+markers', name: b.name,
          line: { color: b.color, width: 2 }, marker: { size: 8 },
          customdata: b.gaps_s.map((g, i) => [clock(g).replace('+', ''), b.ranks[i]]),
          hovertemplate: '<b>' + b.name + '</b> %{x}<br>%{customdata[0]} behind, place %{customdata[1]}<extra></extra>' };
      });
      const lay = base(r.race + ': seconds behind the leader at each mark');
      lay.yaxis.autorange = 'reversed'; lay.yaxis.title = 's behind'; lay.height = 340;
      Plotly.newPlot(el, traces, lay, CONFIG);
    },
  };
  function render(root) {
    if (!window.Plotly) {
      root.querySelectorAll('.chart:not([data-done])').forEach(el => {
        el.textContent = 'Interactive chart needs an internet connection (the chart library loads online).';
      });
      return;
    }
    root.querySelectorAll('.chart[data-fleet]:not([data-done])').forEach(el => {
      el.dataset.done = '1';
      const r = FLEET.races.find(x => x.stem === el.dataset.race);
      try { CHARTS[el.dataset.fleet](el, r); } catch (e) { el.textContent = 'Chart failed: ' + e.message; }
    });
  }
  window.renderCharts = render;
})();
"""


def _chart(kind: str, stem: str) -> str:
    return f'<div class="chart" data-fleet="{kind}" data-race="{stem}"></div>'


def boat_cards(reports_dir: Path, boats: list[dict]) -> str:
    out = []
    for b in boats:
        f = reports_dir / b["id"] / "debrief.md"
        if not f.exists():
            continue
        c = H.coach_card(f.read_text())
        if c:
            out.append(
                c.replace("<h2>Coach's summary</h2>", f"<h2>{html.escape(b['name'])}</h2>", 1)
            )
    return "".join(out)


def write_html(
    fa: dict,
    md: str,
    out: Path,
    title: str,
    debrief: str | None,
    reports_dir: Path,
    boats: list[dict],
    cdn: bool,
) -> Path:
    sections = md.split("\n## ")
    rest = ["## " + s for s in sections[1:]]
    results = [s for s in rest if s.startswith(("## Results", "## Where the time"))]
    per_race = [s for s in rest if s not in results]
    summary = (H.coach_card(debrief) if debrief else "") + H.card(H.md_to_html("\n".join(results)))
    pages = [H.page("summary", "Summary", "Results, and where each boat gained and lost.", summary)]
    if debrief:
        pages.append(
            H.page(
                "debrief",
                "Fleet debrief",
                "How the boats compared, and what each can improve.",
                H.card(H.md_to_html(debrief), "debrief"),
            )
        )
    race_body = ""
    for r, sec in zip(fa["races"], per_race, strict=False):
        race_body += H.card(
            f"<h2>{html.escape(r['race'])}</h2>"
            + '<p class="chart-hint">Hover for boat, time from the gun and speed. Drag to zoom, '
            "double-click to reset.</p>"
            + _chart("gaps", r["stem"])
            + _chart("tracks", r["stem"])
            + H.md_to_html(sec.split("\n", 1)[1])
        )
    pages.append(
        H.page(
            "races",
            "Race by race",
            "Gaps at each mark, tracks, starts, legs and roundings.",
            race_body,
        )
    )
    cards = boat_cards(reports_dir, boats)
    if cards:
        pages.append(
            H.page(
                "boats",
                "Boat by boat",
                "Each boat's coach's summary; full reports are in each boat's own file.",
                cards,
            )
        )
    names = {
        "summary": "Summary",
        "debrief": "Fleet debrief",
        "races": "Race by race",
        "boats": "Boat by boat",
    }
    nav = (
        '<nav class="pages">'
        + "".join(
            f'<a href="#{p}">{n}</a>' for p, n in names.items() if f'id="{p}"' in "".join(pages)
        )
        + "</nav>"
    )
    data = json.dumps(fa).replace("</", "<\\/")
    lib = (
        f'<script src="{H.PLOTLY_CDN}"></script>'
        if cdn
        else f"<script>{H.VENDOR.read_text()}</script>"
    )
    doc = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{H.CSS}</style><script>{H.PAGE_JS}</script></head>"
        f'<body><main><header class="top"><h1>{html.escape(title)}</h1>'
        "<p>Fleet comparison from Njord data</p></header>"
        + nav
        + "".join(pages)
        + "<footer>Numbers from analyze.py and fleet.py. Speeds are over ground. "
        "Times are local to the event.</footer></main>"
        f'<script type="application/json" id="fleet-data">{data}</script>{lib}'
        f"<script>{FLEET_JS}</script></body></html>"
    )
    path = out / "fleet.html"
    path.write_text(doc)
    return path


# ---------------------------------------------------------------- cli


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--reports", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--title", default="Fleet")
    ap.add_argument("--tz")
    ap.add_argument("--html", action="store_true")
    ap.add_argument("--debrief", type=Path, help="fleet debrief (Markdown) for the HTML")
    ap.add_argument("--cdn", action="store_true", help="load Plotly online instead of inlining it")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    boats = load_fleet(a.data, a.reports, a.tz)
    fa = fleet_analysis(boats)
    (a.out / "fleet.json").write_text(json.dumps(fa, indent=1))
    md = write_md(fa, a.out, a.title)
    print(md)
    if a.html or a.debrief:
        debrief = a.debrief.read_text() if a.debrief else None
        print(write_html(fa, md, a.out, a.title, debrief, a.reports, boats, a.cdn))


if __name__ == "__main__":
    main()
