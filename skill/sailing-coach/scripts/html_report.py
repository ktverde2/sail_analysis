#!/usr/bin/env python3
"""Render an analyze.py report folder as one self-contained HTML file.

Usage:
  python html_report.py <report_dir> [--debrief debrief.md] [--out report.html]

Reads <report_dir>/event.md, each <race>/report.md and its PNGs, and an optional debrief
(Markdown written by the coach). Charts are interactive (hover for time, speed, VMG and heading;
drag to zoom) using the bundled Plotly library and each race's plotdata.json; --static uses the
PNGs instead. Everything is inlined, so the HTML file works offline, emailed or uploaded on its
own. No third-party Python packages.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
from datetime import datetime
from pathlib import Path

PLOTS = [
    ("start.png", "Start: approach from 5 minutes, then the last 2 minutes"),
    ("track.png", "Track"),
    ("shifts.png", "Wind shifts upwind and the call on each tack"),
    ("timeline.png", "Speed and heel"),
    ("maneuvers.png", "Distance lost per maneuver"),
]

CSS = """
:root {
  color-scheme: light;
  --bg: #f7f7f5; --card: #fcfcfb; --ink: #0b0b0b; --ink2: #52514e; --muted: #7a7974;
  --line: #e4e3df; --accent: #2a78d6; --accent-soft: #e8f1fc; --warn-bg: #fdf3e2; --warn: #8a5a00;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #121211; --card: #1a1a19; --ink: #ffffff; --ink2: #c3c2b7; --muted: #99988f;
    --line: #2e2e2b; --accent: #3987e5; --accent-soft: #1d2b3d; --warn-bg: #33280f; --warn: #f0c46b;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #121211; --card: #1a1a19; --ink: #ffffff; --ink2: #c3c2b7; --muted: #99988f;
  --line: #2e2e2b; --accent: #3987e5; --accent-soft: #1d2b3d; --warn-bg: #33280f; --warn: #f0c46b;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 24px 16px 64px; }
header.top { margin-bottom: 20px; }
header.top h1 { font-size: 1.7rem; margin: 0 0 4px; letter-spacing: -0.01em; }
header.top p { margin: 0; color: var(--ink2); }
nav { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 8px; }
nav a { color: var(--accent); background: var(--accent-soft); text-decoration: none;
  padding: 4px 12px; border-radius: 999px; font-size: 0.9rem; }
section.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 20px; margin: 16px 0; }
section.card > h2:first-child, section.card > h1:first-child { margin-top: 0; }
h1 { font-size: 1.45rem; } h2 { font-size: 1.2rem; margin: 1.4em 0 0.5em; }
h3 { font-size: 1.05rem; margin: 1.2em 0 0.4em; }
p, li { color: var(--ink); } ul, ol { padding-left: 1.3em; } li { margin: 0.25em 0; }
em { color: var(--ink2); }
.scroll { overflow-x: auto; margin: 8px 0 12px; }
table { border-collapse: collapse; font-size: 0.88rem; font-variant-numeric: tabular-nums; }
th, td { padding: 6px 10px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }
th { color: var(--ink2); font-weight: 600; background: var(--bg); position: sticky; top: 0; }
th:first-child, td:first-child { text-align: left; }
.debrief { border-left: 4px solid var(--accent); }
li.note { list-style: none; margin-left: -1.3em; }
.note { background: var(--warn-bg); color: var(--warn); border-radius: 8px; padding: 10px 14px; }
figure { margin: 12px 0; }
figure img { width: 100%; height: auto; border-radius: 8px; border: 1px solid var(--line);
  background: #fcfcfb; }
figcaption { color: var(--muted); font-size: 0.85rem; margin-top: 4px; }
.plots { display: grid; grid-template-columns: 1fr; gap: 8px; }
figure.narrow img { max-width: 560px; display: block; margin: 0 auto; }
figure.narrow figcaption { text-align: center; }
.chart { width: 100%; min-height: 320px; margin: 8px 0 16px; }
.chart-hint { color: var(--muted); font-size: 0.85rem; margin: 0 0 4px; }
footer { color: var(--muted); font-size: 0.85rem; margin-top: 32px; }
nav.pages a[aria-current="page"] { background: var(--accent); color: #fff; }
nav.sub { margin: 0 0 8px; }
nav.sub a { background: none; border: 1px solid var(--line); color: var(--ink2); }
.page > h1 { font-size: 1.5rem; margin: 8px 0 4px; }
.page > p.lede { color: var(--ink2); margin: 0 0 8px; }
.exec h2 { font-size: 1.1rem; }
.exec li { margin: 0.5em 0; }
details { margin: 8px 0; } summary { cursor: pointer; color: var(--accent); }
.js .page { display: none; } .js .page.active { display: block; }
@media print {
  nav { display: none; } .js .page { display: block; }
  .page { break-before: page; } .page:first-of-type { break-before: auto; }
  section.card { break-inside: avoid-page; }
}
"""
PAGE_JS = """
document.documentElement.classList.add('js');
function show() {
  const pages = [...document.querySelectorAll('.page')];
  const id = (location.hash || '').slice(1);
  let target = pages.find(p => p.id === id) || (id && document.getElementById(id)?.closest('.page')) || pages[0];
  pages.forEach(p => p.classList.toggle('active', p === target));
  if (window.renderCharts) window.renderCharts(target);
  document.querySelectorAll('nav.pages a').forEach(a => {
    if (a.getAttribute('href') === '#' + target.id) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  const el = id && document.getElementById(id);
  if (el && el !== target) el.scrollIntoView(); else window.scrollTo(0, 0);
}
window.addEventListener('hashchange', show);
document.addEventListener('DOMContentLoaded', show);
"""


def md_to_html(md: str) -> str:
    """Small Markdown subset: headings, lists, tables, bold/italic/code, paragraphs."""
    out, para, lines, i = [], [], md.splitlines(), 0

    def flush():
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            flush()
            i += 1
        elif m := re.match(r"^(#{1,4})\s+(.*)", line):
            flush()
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
            i += 1
        elif line.lstrip().startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(lines[i].strip().strip("|").split("|"))
                i += 1
            head, body = rows[0], [r for r in rows[1:] if not re.match(r"^\s*:?-{2,}", r[0])]
            t = "<tr>" + "".join(f"<th>{inline(c.strip())}</th>" for c in head) + "</tr>"
            for r in body:
                t += "<tr>" + "".join(f"<td>{inline(c.strip())}</td>" for c in r) + "</tr>"
            out.append(f'<div class="scroll"><table>{t}</table></div>')
        elif re.match(r"^\s*([-*]|\d+\.)\s+", line):
            flush()
            ordered = bool(re.match(r"^\s*\d+\.", line))
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                item = re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i].rstrip())
                i += 1
                while i < len(lines) and lines[i].startswith("   ") and lines[i].strip():
                    item += " " + lines[i].strip()  # wrapped continuation line
                    i += 1
                items.append(f"<li>{inline(item)}</li>")
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
        else:
            para.append(line.strip())
            i += 1
    flush()
    return "\n".join(out)


def inline(text: str) -> str:
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
    return t


def img_tag(path: Path, caption: str, narrow: bool) -> str:
    data = base64.b64encode(path.read_bytes()).decode()
    cls = ' class="narrow"' if narrow else ""  # tall track maps shouldn't fill a desktop
    return (
        f'<figure{cls}><img src="data:image/png;base64,{data}" alt="{html.escape(caption)}">'
        f"<figcaption>{html.escape(caption)}</figcaption></figure>"
    )


RACE_PLOTS = [("track.png", "Track"), ("timeline.png", "Speed and heel")]


def race_section(race_dir: Path) -> tuple[str, str, str]:
    md = (race_dir / "report.md").read_text()
    md = md.split("\n## Plots")[0]  # the HTML shows the plots themselves
    title = md.splitlines()[0].lstrip("# ").strip()
    body = md_to_html("\n".join(md.splitlines()[1:]))
    body = body.replace("<li>Wind: NOT trusted", '<li class="note">Wind: NOT trusted')
    anchor = race_dir.name
    if INTERACTIVE and (race_dir / "plotdata.json").exists():
        plots = "<h2>Plots</h2>" + plot(race_dir, ["track", "timeline"], "", "")
    else:
        figs = [
            img_tag(race_dir / f, cap, narrow=f == "track.png")
            for f, cap in RACE_PLOTS
            if (race_dir / f).exists()
        ]
        plots = f'<h2>Plots</h2><div class="plots">{"".join(figs)}</div>' if figs else ""
    return (
        anchor,
        title,
        f'<section class="card" id="{anchor}"><h1>{html.escape(title)}</h1>{body}{plots}</section>',
    )


def nav_label(race_dir: Path, title: str) -> str:
    """The race's own name ('Race 2', 'Sun Race 2'), not the boat and event prefix."""
    summary = race_dir / "summary.json"
    if summary.exists():
        name = json.loads(summary.read_text()).get("race")
        if name:
            return name
    return " ".join(title.split()[-2:])


def default_title(races: list[Path]) -> str:
    """'Mojo · July ODW · Sun 19 Jul 2026' from the first race's summary.json."""
    if not races or not (races[0] / "summary.json").exists():
        return "Race report"
    s = json.loads((races[0] / "summary.json").read_text())
    parts = [s.get("boat"), s.get("event")]
    if s.get("gun_local"):
        parts.append(datetime.fromisoformat(s["gun_local"]).strftime("%a %-d %b %Y"))
    return " · ".join(p for p in parts if p) or "Race report"


def html_table(rows: list[dict], cols: list[tuple]) -> str:
    """cols: (header, key or callable). None renders as an en dash."""
    head = "".join(f"<th>{html.escape(h)}</th>" for h, _ in cols)
    body = ""
    for r in rows:
        cells = []
        for _, k in cols:
            v = k(r) if callable(k) else r.get(k)
            cells.append(f"<td>{inline('–' if v is None else str(v))}</td>")
        body += "<tr>" + "".join(cells) + "</tr>"
    return f'<div class="scroll"><table><tr>{head}</tr>{body}</table></div>'


def _mmss(sec):
    if sec is None:
        return None
    sec = round(sec)
    return f"{'-' if sec < 0 else ''}{abs(sec) // 60}:{abs(sec) % 60:02d}"


def _sgn(v, unit=""):
    return None if v is None else f"{v:+.1f}{unit}"


VENDOR = Path(__file__).parent / "vendor" / "plotly-basic.min.js"
CHARTS_JS = Path(__file__).parent / "charts.js"
INTERACTIVE = True  # build() turns this off for --static or when the library is missing

HINT = (
    '<p class="chart-hint">Hover for time, speed, VMG and heading. Drag to zoom, '
    "double-click to reset.</p>"
)


def plot(race_dir: Path, kinds: list[str], png: str, caption: str) -> str:
    """Interactive chart(s) when the race has plotdata.json, else the static PNG."""
    if INTERACTIVE and (race_dir / "plotdata.json").exists():
        return HINT + "".join(
            f'<div class="chart" data-chart="{k}" data-race="{race_dir.name}"></div>' for k in kinds
        )
    return figures([(race_dir / png, caption)])


def figures(entries) -> str:
    figs = [img_tag(p, cap, narrow=False) for p, cap in entries if p.exists()]
    return f'<div class="plots">{"".join(figs)}</div>' if figs else ""


def page(pid: str, title: str, lede: str, body: str) -> str:
    return (
        f'<section class="page" id="{pid}"><h1>{html.escape(title)}</h1>'
        f'<p class="lede">{html.escape(lede)}</p>{body}</section>'
    )


def card(inner: str, cls: str = "") -> str:
    return f'<section class="card {cls}">{inner}</section>'


def starts_page(runs) -> str:
    rows = []
    for d, s in runs:
        st = s.get("start") or {}
        late = (
            f"OCS {st['ocs_at_gun_m']} m"
            if st.get("ocs_at_gun_m")
            else "on the line"
            if st.get("late_s") == 0
            else st.get("late_s")
        )
        rows.append(
            {
                "race": s["race"],
                "late": late,
                "pos": st.get("line_pos_pct_from_pin"),
                "where": st.get("line_pos_label"),
                "b60": st.get("below_line_-60s_m"),
                "b30": st.get("below_line_-30s_m"),
                "b10": st.get("below_line_-10s_m"),
                "b0": st.get("below_line_+0s_m"),
                "s30": st.get("sog_-30s"),
                "s10": st.get("sog_-10s"),
                "s0": st.get("sog_+0s"),
                "s10p": st.get("sog_+10s"),
                "acc": _sgn(st.get("accel_pm5s_kt"), " kt"),
                "stbd": st.get("pct_last_60s_on_stbd"),
                "last": _mmss(st.get("last_maneuver_before_gun_s")),
                "n": st.get("prestart_maneuvers_5min"),
            }
        )
    table = html_table(
        rows,
        [
            ("Race", "race"),
            ("Late (s)", "late"),
            ("Line pos % from pin", "pos"),
            ("End", "where"),
            ("m back −60 s", "b60"),
            ("−30 s", "b30"),
            ("−10 s", "b10"),
            ("Gun", "b0"),
            ("SOG −30 s", "s30"),
            ("−10 s", "s10"),
            ("Gun", "s0"),
            ("+10 s", "s10p"),
            ("Accel ±5 s", "acc"),
            ("% stbd last min", "stbd"),
            ("Last tack/gybe", "last"),
            ("Tacks/gybes last 5 min", "n"),
        ],
    )
    plots = "".join(
        card(
            f"<h2>{html.escape(s['race'])}</h2>"
            + plot(
                d,
                ["startMap", "startTime"],
                "start.png",
                "Approach from 5 minutes, then the last 2 minutes",
            )
        )
        for d, s in runs
    )
    return page(
        "starts",
        "Starts",
        "Every start side by side: timing, position on the line, and speed through the gun.",
        card("<h2>Start comparison</h2>" + table) + plots,
    )


def maneuvers_page(runs) -> str:
    summ, calls = [], []
    for d, s in runs:
        for kind in ("Tack", "Gybe"):
            m = (s.get("maneuver_summary") or {}).get(kind)
            if m:
                summ.append({"race": s["race"], "kind": kind + "s", **m})
        sh = s.get("shifts") or {}
        n = {}
        for c in sh.get("tacks", []):
            n[c["verdict_kind"]] = n.get(c["verdict_kind"], 0) + 1
        over = [c for c in sh.get("tacks", []) if (c.get("overstand_deg") or 0) > 5]
        calls.append(
            {
                "race": s["race"],
                "h": n.get("header", 0),
                "l": n.get("lift", 0),
                "x": n.get("no_shift", 0),
                "lay": n.get("layline", 0),
                "over": len(over),
                "other": n.get("start", 0) + n.get("mark", 0) + n.get("double", 0),
            }
        )
    t1 = html_table(
        summ,
        [
            ("Race", "race"),
            ("Kind", "kind"),
            ("Count", "count"),
            ("Doubles left out", "doubles_excluded"),
            ("Entry kt", "entry_sog_avg"),
            ("Loss kt", "speed_loss_avg_kt"),
            ("Loss %", "speed_loss_avg_pct"),
            ("Recover s", "recovery_avg_s"),
            ("Not recovered in 60 s", "not_recovered_in_60s"),
            ("m lost avg", "distance_lost_avg_m"),
            ("m lost total", "distance_lost_total_m"),
            ("m lost onto port", "distance_lost_avg_onto_port_m"),
            ("m lost onto stbd", "distance_lost_avg_onto_stbd_m"),
        ],
    )
    t2 = html_table(
        calls,
        [
            ("Race", "race"),
            ("On a header", "h"),
            ("On a lift", "l"),
            ("No clear shift", "x"),
            ("Layline tacks", "lay"),
            ("Laylines overstood", "over"),
            ("Start / mark / double", "other"),
        ],
    )
    per_race = ""
    for d, s in runs:
        mans = s.get("maneuvers") or []
        calls_by_t = {c["time_s"]: c for c in (s.get("shifts") or {}).get("tacks", [])}
        rows = [dict(m, call=(calls_by_t.get(m["time_s"]) or {}).get("verdict")) for m in mans]
        detail = html_table(
            rows,
            [
                ("From gun", lambda r: _mmss(r["time_s"])),
                ("Kind", "kind"),
                ("Onto", "onto"),
                ("Leg", "leg"),
                ("Entry kt", "entry_sog"),
                ("Min kt", "min_sog"),
                ("Loss %", "speed_loss_pct"),
                ("Recover s", "recovery_s"),
                ("m lost", "distance_lost_m"),
                ("Call", "call"),
                ("Note", "note"),
            ],
        )
        per_race += card(
            f"<h2>{html.escape(s['race'])}</h2>"
            + plot(
                d, ["maneuvers"], "maneuvers.png", "Distance lost per maneuver (vs. VMG before it)"
            )
            + f"<details><summary>Every tack and gybe ({len(mans)})</summary>{detail}</details>"
        )
    return page(
        "maneuvers",
        "Maneuvers",
        "Tacks and gybes: how much each one cost, and whether the tacks were good wind calls.",
        card("<h2>Tacks and gybes</h2>" + t1) + card("<h2>Tack calls</h2>" + t2) + per_race,
    )


def upwind_page(runs) -> str:
    beats, tgts = [], []
    for d, s in runs:
        shifts = {b["leg"]: b for b in (s.get("shifts") or {}).get("beats", [])}
        for lg in s["legs"]:
            if lg["type"] != "upwind":
                continue
            b = shifts.get(lg["leg"], {})
            beats.append(
                {
                    "race": s["race"],
                    **lg,
                    "time": _mmss(lg["duration_s"]),
                    "pattern": b.get("pattern"),
                    "trend": _sgn(b.get("trend_deg"), "°"),
                    "right": b.get("pct_time_right"),
                    "missed": b.get("missed_header_s"),
                    "split": f"{lg['sog_stbd']}/{lg['sog_port']}",
                }
            )
        tg = s.get("targets") or {}
        for band in tg.get("bands", []) if tg.get("available") else []:
            tgts.append({"race": s["race"], **band})
    t1 = html_table(
        beats,
        [
            ("Race", "race"),
            ("Leg", "leg"),
            ("Duration", "time"),
            ("SOG steady", "sog_steady"),
            ("VMG", "vmg_steady"),
            ("Heel", "heel_abs_avg"),
            ("Heel sd", "heel_abs_std"),
            ("Tacking ∠", "tacking_angle"),
            ("Wind est", "twd_est"),
            ("Pattern", "pattern"),
            ("Trend", "trend"),
            ("% right", "right"),
            ("Tacks", "tacks"),
            ("SOG stbd/port", "split"),
            ("Headed > 5° s", "missed"),
        ],
    )
    body = card("<h2>Every beat</h2>" + t1)
    if tgts:
        body += card(
            "<h2>Vs. the Etchells card</h2>"
            + html_table(
                tgts,
                [
                    ("Race", "race"),
                    ("Wind", "wind"),
                    ("Speed", "speed_avg"),
                    ("Target", "speed_tgt"),
                    ("% target", "speed_pct"),
                    ("Heel", "heel_avg"),
                    ("Target heel", "heel_tgt"),
                    ("Δ heel", "heel_delta"),
                    ("Heel sd", "heel_std"),
                    ("TWA", "twa_avg"),
                    ("Target TWA", "twa_tgt"),
                    ("Δ TWA", "twa_delta"),
                ],
            )
        )
    body += "".join(
        card(
            f"<h2>{html.escape(s['race'])}</h2>"
            + plot(d, ["shifts"], "shifts.png", "Wind shifts upwind and the call on each tack")
        )
        for d, s in runs
    )
    return page(
        "upwind",
        "Upwind",
        "Beats only: speed and heel, the card, the wind pattern and which side we sailed.",
        body,
    )


def downwind_page(runs) -> str:
    rows, gybes = [], []
    for d, s in runs:
        for lg in s["legs"]:
            if lg["type"] == "downwind":
                rows.append(
                    {
                        "race": s["race"],
                        **lg,
                        "time": _mmss(lg["duration_s"]),
                        "split": f"{lg['sog_stbd']}/{lg['sog_port']}",
                    }
                )
        for m in s.get("maneuvers") or []:
            if m["kind"] == "Gybe":
                gybes.append({"race": s["race"], **m})
    t1 = html_table(
        rows,
        [
            ("Race", "race"),
            ("Leg", "leg"),
            ("Duration", "time"),
            ("Dist nm", "distance_sailed_nm"),
            ("SOG steady", "sog_steady"),
            ("VMG", "vmg_steady"),
            ("Angle to wind", "twa_steady"),
            ("Heel", "heel_abs_avg"),
            ("Gybes", "gybes"),
            ("% stbd", "pct_time_stbd"),
            ("SOG stbd/port", "split"),
        ],
    )
    t2 = (
        html_table(
            gybes,
            [
                ("Race", "race"),
                ("From gun", lambda r: _mmss(r["time_s"])),
                ("Onto", "onto"),
                ("Leg", "leg"),
                ("Entry kt", "entry_sog"),
                ("Min kt", "min_sog"),
                ("Loss %", "speed_loss_pct"),
                ("Recover s", "recovery_s"),
                ("m lost", "distance_lost_m"),
            ],
        )
        if gybes
        else "<p>No gybes.</p>"
    )
    plots = "".join(
        card(
            f"<h2>{html.escape(s['race'])}</h2>"
            + plot(d, ["downwind"], "downwind.png", "Speed down each run, by gybe")
        )
        for d, s in runs
        if any(lg["type"] == "downwind" for lg in s["legs"])
    )
    return page(
        "downwind",
        "Downwind",
        "Runs only: speed, VMG away from the wind, angle, and gybes.",
        card("<h2>Every run</h2>" + t1) + card("<h2>Gybes</h2>" + t2) + plots,
    )


def build(
    report_dir: Path, debrief: str | None, title: str | None, interactive: bool = True
) -> str:
    global INTERACTIVE
    INTERACTIVE = interactive and VENDOR.exists() and CHARTS_JS.exists()
    races = sorted(p.parent for p in report_dir.glob("*/report.md"))
    sections = [race_section(d) for d in races]
    runs = [
        (d, json.loads((d / "summary.json").read_text()))
        for d in races
        if (d / "summary.json").exists()
    ]
    runs.sort(key=lambda x: x[1].get("gun_local") or "")
    page_title = title or default_title(races)

    pages = []
    exec_md = report_dir / "executive.md"
    event = (report_dir / "event.md").read_text().split("\nPer-race detail")[0]
    event = event.replace("# Event summary", "## All races")
    summary_body = card(md_to_html(exec_md.read_text()), "exec") if exec_md.exists() else ""
    summary_body += card(md_to_html(event))
    pages.append(
        page(
            "summary",
            "Summary",
            "The whole event at a glance, then each day and each race.",
            summary_body,
        )
    )
    if debrief:
        pages.append(
            page(
                "debrief", "Debrief", "The coaching debrief.", card(md_to_html(debrief), "debrief")
            )
        )
    if runs:
        pages += [starts_page(runs), maneuvers_page(runs), upwind_page(runs), downwind_page(runs)]
    sub_nav = (
        '<nav class="sub">'
        + "".join(
            f'<a href="#{a}">{html.escape(nav_label(report_dir / a, t))}</a>'
            for a, t, _ in sections
        )
        + "</nav>"
    )
    pages.append(
        page(
            "races",
            "Race by race",
            "Everything for one race in one place.",
            sub_nav + "".join(s for _, _, s in sections),
        )
    )

    names = {
        "summary": "Summary",
        "debrief": "Debrief",
        "starts": "Starts",
        "maneuvers": "Maneuvers",
        "upwind": "Upwind",
        "downwind": "Downwind",
        "races": "Race by race",
    }
    ids = re.findall(r'<section class="page" id="([^"]+)"', "".join(pages))
    nav = (
        '<nav class="pages">'
        + "".join(f'<a href="#{i}">{names.get(i, i)}</a>' for i in ids)
        + "</nav>"
    )
    parts = [
        f'<header class="top"><h1>{html.escape(page_title)}</h1>',
        "<p>Race analysis from Njord data</p></header>",
        nav,
        *pages,
        (
            "<footer>Numbers from analyze.py. Speeds are over ground unless noted. "
            "Times are local to the event.</footer>"
        ),
    ]
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(page_title)}</title><style>{CSS}</style>"
        f"<script>{PAGE_JS}</script></head>"
        f"<body><main>{''.join(parts)}</main>{chart_scripts(races)}</body></html>"
    )


def chart_scripts(races: list[Path]) -> str:
    """Race data (once each) plus the chart library and renderer, all inline for offline use."""
    if not INTERACTIVE:
        return ""
    data = "".join(
        f'<script type="application/json" id="race-{d.name}">'
        + (d / "plotdata.json").read_text().replace("</", "<\\/")
        + "</script>"
        for d in races
        if (d / "plotdata.json").exists()
    )
    return data + f"<script>{VENDOR.read_text()}</script><script>{CHARTS_JS.read_text()}</script>"


def write_html(
    report_dir: Path,
    debrief_path: Path | None = None,
    out: Path | None = None,
    title: str | None = None,
    interactive: bool = True,
) -> Path:
    debrief = debrief_path.read_text() if debrief_path else None
    out = out or report_dir / "report.html"
    out.write_text(build(report_dir, debrief, title, interactive))
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("report_dir", type=Path)
    ap.add_argument("--debrief", type=Path, help="Markdown debrief to put at the top")
    ap.add_argument("--out", type=Path, help="default: <report_dir>/report.html")
    ap.add_argument("--title")
    ap.add_argument("--static", action="store_true", help="PNG plots instead of interactive charts")
    a = ap.parse_args()
    print(write_html(a.report_dir, a.debrief, a.out, a.title, interactive=not a.static))


if __name__ == "__main__":
    main()
