#!/usr/bin/env python3
"""Render an analyze.py report folder as one self-contained HTML file.

Usage:
  python html_report.py <report_dir> [--debrief debrief.md] [--overview overview.md]
                        [--deep deep-dive.md] [--out report.html]

Reads <report_dir>/event.md, each <race>/report.md and its PNGs, and the coach's Markdown: the
debrief, and optionally a short overview and a deep-dive. The page has three depths, each with
its own pages: Quick look (the overview, or the coach's summary from the debrief), Debrief (the
debrief and the event numbers) and Deep dive (the race notes, then starts, maneuvers, upwind,
downwind, roundings and every race). Charts are interactive (hover for time, speed, VMG and heading;
drag to zoom) using the bundled Plotly library and each race's plotdata.json; --static uses the
PNGs instead. Everything is inlined, so the HTML file works offline, shared or uploaded on its
own. No third-party Python packages.

--cdn loads Plotly from jsDelivr instead of inlining it: the file is ~1 MB smaller, but the charts
need an internet connection when it is opened. Gmail's virus scan still flags interactive HTML
attachments either way, so share reports by a Google Drive link rather than as an attachment.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
from datetime import datetime
from pathlib import Path

import current as CU
from analyze import lengths, m_bl, mbl_cell  # metres and boat lengths
import maneuver_overlay as MO

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
.chart { width: 100%; min-height: 300px; margin: 8px 0 16px; }
.pair { display: grid; grid-template-columns: 1fr; gap: 8px; }
@media (min-width: 900px) { .pair { grid-template-columns: 1fr 1fr; } }
h3.leg { margin: 18px 0 2px; }
p.facts { color: var(--ink2); margin: 2px 0 6px; font-size: 0.92rem; }
.chart-hint { color: var(--muted); font-size: 0.85rem; margin: 0 0 4px; }
.next { color: var(--ink2); }
footer { color: var(--muted); font-size: 0.85rem; margin-top: 32px; }
nav.pages a[aria-current="page"] { background: var(--accent); color: #fff; }
nav.levels { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0; margin: 16px 0 4px;
  border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: var(--card); }
nav.levels a { display: block; padding: 8px 12px; text-decoration: none; color: var(--ink);
  border-left: 1px solid var(--line); border-radius: 0; background: none; font-size: 0.95rem; }
nav.levels a:first-child { border-left: 0; }
nav.levels a small { display: block; color: var(--muted); font-size: 0.78rem; }
nav.levels a[aria-current="true"] { background: var(--accent); color: #fff; }
nav.levels a[aria-current="true"] small { color: #fff; opacity: 0.85; }
nav.pages a[hidden] { display: none; }
nav.sub { margin: 0 0 8px; }
nav.sub a { background: none; border: 1px solid var(--line); color: var(--ink2); }
.page > h1 { font-size: 1.5rem; margin: 8px 0 4px; }
.page > p.lede { color: var(--ink2); margin: 0 0 8px; }
.exec h2 { font-size: 1.1rem; }
.exec li { margin: 0.5em 0; }
details { margin: 8px 0; } summary { cursor: pointer; color: var(--accent); }
details.dd { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 14px 20px; margin: 12px 0; }
details.dd > summary { list-style: none; display: grid; gap: 2px; color: var(--ink); }
details.dd > summary::-webkit-details-marker { display: none; }
details.dd > summary .dd-h { font-size: 1.15rem; font-weight: 700; }
details.dd > summary .dd-h::after { content: " ▸"; color: var(--ink2); font-weight: 400; }
details.dd[open] > summary .dd-h::after { content: " ▾"; }
details.dd > summary .dd-l { color: var(--ink2); font-size: 0.92rem; }
details.dd[open] > summary { margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
h2.dd-group { font-size: 1.05rem; color: var(--ink2); text-transform: uppercase; letter-spacing: 0.04em;
  margin: 24px 0 4px; }
.js .page { display: none; } .js .page.active { display: block; }
@media print {
  nav { display: none; } .js .page { display: block; }
  .page { break-before: page; } .page:first-of-type { break-before: auto; }
  section.card { break-inside: avoid-page; }
  details.dd > summary .dd-h::after { content: ""; }
}
"""
PAGE_JS = """
document.documentElement.classList.add('js');
function show() {
  const pages = [...document.querySelectorAll('.page')];
  const id = (location.hash || '').slice(1);
  let target = pages.find(p => p.id === id) || (id && document.getElementById(id)?.closest('.page')) || pages[0];
  pages.forEach(p => p.classList.toggle('active', p === target));
  const lv = target.dataset.level;  // depth levels (boat reports): show that level's pages only
  if (lv) {
    document.querySelectorAll('nav.levels a').forEach(a => a.setAttribute('aria-current', a.dataset.level === lv));
    document.querySelectorAll('nav.pages a[data-level]').forEach(a => { a.hidden = a.dataset.level !== lv; });
  }
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
// Dropdown sections: charts inside a closed one are drawn when it opens (Plotly can't size hidden charts)
document.addEventListener('toggle', e => {
  if (!e.target.matches || !e.target.matches('details.dd') || !e.target.open) return;
  if (window.renderCharts) window.renderCharts(e.target);
  window.dispatchEvent(new Event('sections:open'));
}, true);
window.addEventListener('beforeprint', () => document.querySelectorAll('details.dd').forEach(d => { d.open = true; }));
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
        elif LIST_ITEM.match(line):
            flush()
            block, i = _list(lines, i)
            out.append(block)
        else:
            para.append(line.strip())
            i += 1
    flush()
    return "\n".join(out)


LIST_ITEM = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.*)")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _list(lines: list[str], i: int) -> tuple[str, int]:
    """A list starting at lines[i]; deeper-indented items nest under the item above."""
    base = _indent(lines[i])
    tag = "ol" if re.match(r"^\s*\d+\.", lines[i]) else "ul"
    items: list[str] = []
    while i < len(lines) and (m := LIST_ITEM.match(lines[i])) and _indent(lines[i]) == base:
        text, sub = m.group(3).rstrip(), ""
        i += 1
        while i < len(lines) and lines[i].strip() and _indent(lines[i]) > base:
            if LIST_ITEM.match(lines[i]):
                block, i = _list(lines, i)
                sub += block
            else:
                text += " " + lines[i].strip()  # wrapped continuation line
                i += 1
        items.append(f"<li>{inline(text)}{sub}</li>")
    return f"<{tag}>{''.join(items)}</{tag}>", i


def debrief_points(debrief: str) -> dict[str, list[tuple[str, str]]]:
    """Headlines from the debrief's "What went well" and "Top 3" lists, with each Next time fix.

    A headline is the bold text that opens a top-level item; the fix is the text after
    "Next time:" anywhere in that item or its sub-bullets.
    """
    blocks: dict[str, list[str]] = {"well": [], "work": []}
    section = None
    for line in debrief.splitlines():
        if not line.strip() or line.startswith("[[chart:"):  # chart placeholders (fleet.py)
            continue
        if not line[0].isspace() and not LIST_ITEM.match(line):  # a header or paragraph
            title = line.lower()
            section = "well" if "went well" in title else "work" if "work on" in title else None
        elif section and not _indent(line):  # a new top-level item
            blocks[section].append(line)
        elif section and blocks[section]:  # its sub-bullets and wrapped lines
            blocks[section][-1] += " " + line.strip()
    points: dict[str, list[tuple[str, str]]] = {"well": [], "work": []}
    for key, items in blocks.items():
        for text in items:
            if lead := re.search(r"\*\*(.+?)\*\*", text):
                fix = re.search(r"\*\*Next time:\*\*\s*(.+?)(?=\s+-\s+\*\*|$)", text)
                points[key].append((lead.group(1).rstrip(" .:"), fix.group(1) if fix else ""))
    return points


def coach_card(debrief: str) -> str:
    pts = debrief_points(debrief)
    if not (pts["well"] or pts["work"]):
        return ""
    parts = ["<h2>Coach's summary</h2>"]
    if pts["well"]:
        parts.append("<h3>What went well</h3><ul>")
        parts += [f"<li>{inline(t)}</li>" for t, _ in pts["well"]]
        parts.append("</ul>")
    if pts["work"]:
        parts.append("<h3>Top priorities</h3><ol>")
        for t, fix in pts["work"]:
            fix = fix[:1].upper() + fix[1:]
            nxt = (
                f'<br><span class="next"><strong>Next time:</strong> {inline(fix)}</span>'
                if fix
                else ""
            )
            parts.append(f"<li><strong>{inline(t)}</strong>{nxt}</li>")
        parts.append("</ol>")
    parts.append(
        '<p class="chart-hint">From the debrief; the full reasoning and numbers are on the Debrief page.</p>'
    )
    return card("".join(parts), "debrief")


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
PLOTLY_CDN = "https://cdn.jsdelivr.net/npm/plotly.js-basic-dist-min@2.35.3/plotly-basic.min.js"

HINT = (
    '<p class="chart-hint">Hover for time, speed, VMG and heading. Drag to zoom, '
    "double-click to reset.</p>"
)


def interactive(race_dir: Path) -> bool:
    return INTERACTIVE and (race_dir / "plotdata.json").exists()


def chart_div(race_dir: Path, kind: str, **attrs) -> str:
    extra = "".join(f' data-{k}="{html.escape(str(v))}"' for k, v in attrs.items())
    return f'<div class="chart" data-chart="{kind}" data-race="{race_dir.name}"{extra}></div>'


def plot(race_dir: Path, kinds: list[str], png: str, caption: str) -> str:
    """Interactive chart(s) when the race has plotdata.json, else the static PNG."""
    if interactive(race_dir):
        return HINT + "".join(chart_div(race_dir, k) for k in kinds)
    return figures([(race_dir / png, caption)])


def pair(race_dir: Path, specs: list[tuple[str, dict]]) -> str:
    """Two interactive charts side by side on a wide screen, stacked on a phone."""
    return '<div class="pair">' + "".join(chart_div(race_dir, k, **a) for k, a in specs) + "</div>"


def figures(entries) -> str:
    figs = [img_tag(p, cap, narrow=False) for p, cap in entries if p.exists()]
    return f'<div class="plots">{"".join(figs)}</div>' if figs else ""


def page(pid: str, title: str, lede: str, body: str, level: int | None = None) -> str:
    lv = f' data-level="{level}"' if level else ""
    return (
        f'<section class="page" id="{pid}"{lv}><h1>{html.escape(title)}</h1>'
        f'<p class="lede">{html.escape(lede)}</p>{body}</section>'
    )


# The three depths of a boat report: (level, name, who it's for)
LEVELS = [
    (1, "Quick look", "The takeaways, 2 minutes"),
    (2, "Debrief", "The coaching and the numbers"),
    (3, "Deep dive", "Every race, leg and maneuver"),
]


def card(inner: str, cls: str = "") -> str:
    return f'<section class="card {cls}">{inner}</section>'


def dropdown(title: str, body: str, line: str = "", open_: bool = False, sid: str = "") -> str:
    """One section of a page that opens and closes, headed by its title and a one-line summary."""
    return (
        f'<details class="dd"{f" id={chr(34)}{sid}{chr(34)}" if sid else ""}{" open" if open_ else ""}>'
        f'<summary><span class="dd-h">{html.escape(title)}</span>'
        + (f'<span class="dd-l">{line}</span>' if line else "")
        + f"</summary>{body}</details>"
    )


def group(title: str) -> str:
    return f'<h2 class="dd-group">{html.escape(title)}</h2>'


def ocs_label(s: dict) -> str:
    """Same wording as analyze.ocs_label (kept separate: this script has no pandas)."""
    if s.get("ocs_returned_s") is not None and s.get("late_s") is not None:
        return f"over {s['ocs_at_gun_m']} m, restarted +{s['late_s']:.0f} s"
    return f"OCS {s['ocs_at_gun_m']} m"


def starts_page(runs) -> str:
    rows = []
    for d, s in runs:
        st = s.get("start") or {}
        late = (
            ocs_label(st)
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
                "b60": mbl_cell(st.get("below_line_-60s_m")),
                "b30": mbl_cell(st.get("below_line_-30s_m")),
                "b10": mbl_cell(st.get("below_line_-10s_m")),
                "b0": mbl_cell(st.get("below_line_+0s_m")),
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
            ("m back (lengths) −60 s", "b60"),
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


def tacks_gybes_page(runs, overlay_pages) -> str:
    """Maneuver costs, the tack calls, the tack and gybe overlays and each race's maneuvers, one dropdown each."""
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
            ("m lost avg (lengths)", lambda r: mbl_cell(r.get("distance_lost_avg_m"))),
            ("m lost total (lengths)", lambda r: mbl_cell(r.get("distance_lost_total_m"))),
            ("m lost onto port (lengths)", lambda r: mbl_cell(r.get("distance_lost_avg_onto_port_m"))),
            ("m lost onto stbd (lengths)", lambda r: mbl_cell(r.get("distance_lost_avg_onto_stbd_m"))),
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
        nt = sum(m["kind"] == "Tack" for m in mans)
        ng = sum(m["kind"] == "Gybe" for m in mans)
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
                ("m lost (lengths)", lambda r: mbl_cell(r.get("distance_lost_m"))),
                ("Call", "call"),
                ("Note", "note"),
            ],
        )
        lost = sum(m.get("distance_lost_m") or 0 for m in mans)
        per_race += dropdown(
            s["race"],
            plot(d, ["maneuvers"], "maneuvers.png", "Distance lost per maneuver (vs. VMG before it)")
            + "<h3>Every tack and gybe</h3>"
            + detail,
            f"{nt} tack{'s' * (nt != 1)}, {ng} gybe{'s' * (ng != 1)} · {html.escape(m_bl(lost))} lost in all",
        )
    tot_t = sum(r["count"] for r in summ if r["kind"] == "Tacks")
    tot_g = sum(r["count"] for r in summ if r["kind"] == "Gybes")
    calls_head = ""
    if calls:
        h = sum(c["h"] for c in calls)
        l_ = sum(c["l"] for c in calls)
        calls_head = f"{h} on a header, {l_} on a lift, {sum(c['over'] for c in calls)} laylines overstood"
    head = f"{tot_t} tacks and {tot_g} gybes over {len(runs)} race{'s' * (len(runs) != 1)}"
    overlays = "".join(
        dropdown(
            f"{name} lined up",
            f'<p class="facts">{lede}</p><div id="{pid}">{body}</div>',
            "Every comparable one overlaid: the best highlighted, and what they did differently",
        )
        for pid, name, lede, body in overlay_pages
    )
    return page(
        "tacks-gybes",
        "Tacks and Gybes",
        "How much each tack and gybe cost, whether the tacks were good wind calls, and every one side by side. Open a section to see it.",
        group("The regatta")
        + dropdown("What they cost", t1, head, open_=True)
        + dropdown("Tack calls", t2, calls_head)
        + overlays
        + group("Race by race")
        + per_race,
    )


def upwind_parts(runs):
    """Upwind sections: (regatta-wide [(title, line, body)], {race: per-race html})."""
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
                    # shown magnetic, like the compass
                    "twd_est": round((lg["twd_est"] - s["mag_var"]) % 360, 1)
                    if lg.get("twd_est") is not None and s.get("mag_var") is not None
                    else lg.get("twd_est"),
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
            ("Wind est (mag)", "twd_est"),
            ("Pattern", "pattern"),
            ("Trend", "trend"),
            ("% right", "right"),
            ("Tacks", "tacks"),
            ("SOG stbd/port", "split"),
            ("Headed > 5° s", "missed"),
        ],
    )
    secs = [("Every beat", _legs_line(beats), t1)]
    dist = distance_card(runs, "upwind", bare=True)
    if dist:
        secs.append(("Distance sailed upwind", "Sailed against the straight line and the ideal at our angle", dist))
    if tgts:
        secs.append(
            (
                "Vs. the Etchells card",
                "Speed, heel and wind angle against the target card",
                html_table(
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
            ),
            )
        )
    per_race = {}
    for d, s in runs:
        beats_ = [lg for lg in s["legs"] if lg["type"] == "upwind"]
        if interactive(d):
            inner = (
                HINT
                + "".join(
                    f'<h3 class="leg">Leg {lg["leg"]}</h3>'
                    f'<p class="facts">{lg["sog_steady"]} kt steady, VMG {lg["vmg_steady"]} kt, '
                    f"heel {lg['heel_abs_avg']}°, {lg['tacks']} tacks, tacking angle {lg['tacking_angle']}°</p>"
                    + pair(d, [("legTrack", {"leg": lg["leg"]}), ("polar", {"leg": lg["leg"]})])
                    for lg in beats_
                )
                + '<h3 class="leg">Wind shifts, all beats</h3>'
                + chart_div(d, "shifts")
            )
        else:
            inner = figures(
                [
                    (d / "polar.png", "Upwind polars"),
                    (d / "shifts.png", "Wind shifts upwind and the call on each tack"),
                ]
            )
        per_race[s["race"]] = inner
    return secs, per_race


def downwind_parts(runs):
    """Downwind sections, like upwind_parts. Gybe details are on the Tacks and Gybes page."""
    rows = []
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
    t1 = html_table(
        rows,
        [
            ("Race", "race"),
            ("Leg", "leg"),
            ("Duration", "time"),
            ("SOG steady", "sog_steady"),
            ("VMG", "vmg_steady"),
            ("Angle to wind", "twa_steady"),
            ("Heel", "heel_abs_avg"),
            ("Gybes", "gybes"),
            ("% stbd", "pct_time_stbd"),
            ("SOG stbd/port", "split"),
        ],
    )
    secs = [("Every run", _legs_line(rows), t1)]
    dist = distance_card(runs, "downwind", bare=True)
    if dist:
        secs.append(("Distance sailed downwind", "Sailed against the straight line and the ideal at our angle", dist))
    per_race = {}
    for d, s in runs:
        runs_ = [lg for lg in s["legs"] if lg["type"] == "downwind"]
        if not runs_:
            continue
        if interactive(d):
            inner = (
                HINT
                + "".join(
                    f'<h3 class="leg">Leg {lg["leg"]}</h3>'
                    f'<p class="facts">{lg["sog_steady"]} kt steady, VMG {lg["vmg_steady"]} kt, '
                    f"{lg['twa_steady']}° to the wind, {lg['gybes']} gybes, {lg['pct_time_stbd']}% on starboard</p>"
                    + chart_div(d, "legTrack", leg=lg["leg"])
                    for lg in runs_
                )
                + '<h3 class="leg">Speed on every run</h3>'
                + chart_div(d, "downwind")
            )
        else:
            inner = figures([(d / "downwind.png", "Speed down each run, by gybe")])
        per_race[s["race"]] = inner
    return secs, per_race


def _legs_line(rows) -> str:
    if not rows:
        return ""
    sog = [r["sog_steady"] for r in rows if r.get("sog_steady") is not None]
    avg = f", {sum(sog) / len(sog):.2f} kt steady SOG on average" if sog else ""
    return f"{len(rows)} legs{avg}"


def up_down_page(runs) -> str:
    """Beats and runs on one page: the regatta-wide tables, then each race's legs, one dropdown each."""
    up, up_r = upwind_parts(runs)
    dn, dn_r = downwind_parts(runs)
    per_race = ""
    for _, s in runs:
        r = s["race"]
        nb = sum(lg["type"] == "upwind" for lg in s["legs"])
        nr = sum(lg["type"] == "downwind" for lg in s["legs"])
        body = (
            (f"<h3>Upwind</h3>{up_r[r]}" if up_r.get(r) else "")
            + (f"<h3>Downwind</h3>{dn_r[r]}" if dn_r.get(r) else "")
        )
        if body:
            per_race += dropdown(r, body, f"{nb} beat{'s' * (nb != 1)}, {nr} run{'s' * (nr != 1)}: tracks, polars, shifts and speed")
    return page(
        "upwind-downwind",
        "Upwind and Downwind Analysis",
        "Every beat and run: speed, VMG, heel and angles, the wind pattern and which side we sailed, and the distance sailed. Open a section to see it.",
        group("Upwind")
        + "".join(dropdown(t, b, l, open_=(i == 0)) for i, (t, l, b) in enumerate(up))
        + group("Downwind")
        + "".join(dropdown(t, b, l) for t, l, b in dn)
        + group("Race by race")
        + per_race,
    )


def roundings_page(runs) -> str:
    rows = []
    for _, s in runs:
        for r in s.get("roundings") or []:
            rows.append({"race": s["race"], **r, "approach": _approach_text(r)})
    table = html_table(
        rows,
        [
            ("Race", "race"),
            ("#", "n"),
            ("Type", "type"),
            ("From gun", lambda r: _mmss(r["time_s"])),
            ("m lost (lengths)", lambda r: mbl_cell(r.get("metres_lost"))),
            ("vs your best", lambda r: _plus(r.get("vs_best_m")) + (f" ({lengths(r['vs_best_m'])})" if r.get("vs_best_m") else "") if r.get("vs_best_m") is not None else None),
            ("Approach", "approach"),
            ("SOG in", "sog_entry"),
            ("SOG min", "sog_min"),
            ("SOG out", "sog_exit"),
            ("Settled s", "settle_s"),
            ("Closest to mark m (lengths)", lambda r: mbl_cell(r.get("mark_dist_m"))),
            ("Gate", "gate_side"),
        ],
    )
    summary = []
    for kind in ("windward", "leeward"):
        k = [r for r in rows if r["type"] == kind and r.get("metres_lost") is not None]
        if k:
            worst = max(k, key=lambda r: r["metres_lost"])
            best = min(k, key=lambda r: r["metres_lost"])
            summary.append(
                f"<li><strong>{kind.capitalize()}:</strong> {len(k)} roundings, "
                f"{m_bl(sum(r['metres_lost'] for r in k) / len(k))} lost on average; best "
                f"{html.escape(best['race'])} #{best['n']} ({m_bl(best['metres_lost'])}), worst "
                f"{html.escape(worst['race'])} #{worst['n']} ({m_bl(worst['metres_lost'])}).</li>"
            )
    goal = _rounding_goal(rows)
    body = card(
        "<h2>Every rounding</h2>"
        + goal
        + (f"<ul>{''.join(summary)}</ul>" if summary else "")
        + '<p class="facts">Metres lost toward the marks: VMC (speed toward this mark, then the next) from 30 s '
        "before to 60 s after the rounding, against the steady VMC of the leg before and after. Settled: 10 s "
        "VMC back to 90% of the next leg's.</p>"
        '<p class="facts">Each track map opens zoomed on the mark: the dashed circle is the zone (three Etchells '
        "lengths, 28 m, where mark-room applies) and the open circle is where the boat entered it. "
        "<em>Wider view</em> shows the minute either side. The fleet report shows the roundings where "
        "boats arrived together.</p>"
        + table
    ) + card(TIGHT_ROUNDINGS)
    for d, s in runs:
        rs = s.get("roundings") or []
        if not rs:
            continue
        if interactive(d):
            inner = HINT + "".join(
                f'<h3 class="leg">{r["type"].capitalize()} rounding #{r["n"]} · +{_mmss(r["time_s"])} from gun · '
                f"{m_bl(r['metres_lost'])} lost"
                + (
                    " · your best"
                    if r.get("is_best")
                    else f" · +{m_bl(r['vs_best_m'])} vs your best"
                    if r.get("vs_best_m") is not None
                    else ""
                )
                + f'</h3><p class="facts">{html.escape(_approach_text(r) or "")}; '
                f"{r['sog_entry']} → {r['sog_min']} → {r['sog_exit']} kt; settled in {r['settle_s']} s"
                + (
                    f"; closest {m_bl(r['mark_dist_m'])} from the mark"
                    if r.get("mark_dist_m") is not None
                    else ""
                )
                + (f"; {r['gate_side']}" if r.get("gate_side") else "")
                + "</p>"
                + _tips_html(r)
                + pair(d, [("roundTrack", {"n": r["n"]}), ("roundSpeed", {"n": r["n"]})])
                for r in rs
            )
        else:
            inner = "".join(
                f'<h3 class="leg">{r["type"].capitalize()} rounding #{r["n"]}: {m_bl(r["metres_lost"])} lost</h3>'
                + _tips_html(r)
                for r in rs
            ) + figures([(d / "roundings.png", "Speed through the roundings")])
        body += card(f"<h2>{html.escape(s['race'])}</h2>" + inner)
    return page(
        "roundings",
        "Roundings",
        "Windward and leeward marks: approach, speed through the turn, and what each one cost.",
        body,
    )


TIGHT_ROUNDINGS = """<h2>Keeping roundings tight</h2>
<p class="facts">The goal is zero metres lost. These are the habits behind a tight rounding; the
suggestions under each rounding below say which ones that rounding missed.</p>
<ul>
<li><strong>Windward, on the layline:</strong> tack onto it with room to sail the last 100 m at full
speed, not pinching up to the mark and not overstood. Pole and halyard ready before the mark.</li>
<li><strong>Windward, the bear-away:</strong> ease main and vang and turn steadily all the way to the
run angle, then hoist. Reaching off high first and setting there is where our slowest exits went.</li>
<li><strong>Leeward, the approach:</strong> drop early enough that the turn starts before the mark.
Enter wide, a couple of boat lengths out, so the turn can finish right at the mark.</li>
<li><strong>Leeward, the exit:</strong> tight out, about a boat length from the mark, with main and jib
trimmed on through the turn. Foot at your normal upwind angle until the speed is back, then point.</li>
<li><strong>Both:</strong> one smooth radius, helm and trim together. The bigger the speed dip, the
longer it takes to get it back.</li>
</ul>"""


def _rounding_goal(rows: list[dict]) -> str:
    """Goal line: zero, with the best of each type as the next milestone and what closing the gap
    on every rounding would have saved."""
    parts, saved = [], 0.0
    for kind in ("windward", "leeward"):
        k = [r for r in rows if r["type"] == kind and r.get("metres_lost") is not None]
        if k:
            best = min(r["metres_lost"] for r in k)
            parts.append(f"every {kind} rounding at or under {m_bl(best)}")
            saved += sum(r.get("vs_best_m") or 0 for r in k)
    if not parts:
        return ""
    return (
        '<p class="note"><strong>Goal: zero metres lost at every mark.</strong> Next milestone: '
        + " and ".join(parts)
        + f" (your best so far). Matching your best everywhere would have saved about {m_bl(saved)}.</p>"
    )


def _tips_html(r: dict) -> str:
    tips = r.get("tips") or []
    return f"<ul>{''.join(f'<li>{html.escape(t)}</li>' for t in tips)}</ul>" if tips else ""


def distance_card(runs, kind: str, bare: bool = False) -> str:
    rows = [
        {"race": s["race"], **lg}
        for _, s in runs
        for lg in s["legs"]
        if lg["type"] == kind and lg.get("straight_nm")
    ]
    if not rows:
        return ""
    table = html_table(
        rows,
        [
            ("Race", "race"),
            ("Leg", "leg"),
            ("Sailed nm", "distance_sailed_nm"),
            ("Straight line nm", "straight_nm"),
            ("+% vs straight", "extra_pct"),
            ("Ideal at our angle nm", "ideal_nm"),
            ("Extra vs ideal m (lengths)", lambda r: mbl_cell(r.get("extra_vs_ideal_m"))),
            ("Track angle to wind", "track_angle"),
        ],
    )
    note = (
        '<p class="facts">Straight line: mark to mark. Ideal: the shortest '
        "path at our average angle to the wind over the ground (leeway included) in steady wind. "
        "Extra vs ideal is distance sailed beyond what our angles needed: sailing below our angle, "
        "overstanding, extra zig-zags. Shifts can make it negative.</p>"
    )
    return note + table if bare else card("<h2>Distance sailed</h2>" + note + table)


def _plus(v):
    return None if v is None else ("best" if v == 0 else f"+{v:.0f}")


def _approach_text(r: dict) -> str | None:
    if r["type"] == "windward":
        sec = r.get("last_tack_before_s")
        if sec is None:
            return None
        over = r.get("overstand_deg")
        return f"layline tack {sec} s out" + (
            f", overstood ~{over:.0f}°" if over is not None and over > 5 else ""
        )
    sec = r.get("last_gybe_before_s")
    return f"last gybe {sec} s out" if sec is not None else "no gybe on the run"


def build(
    report_dir: Path,
    debrief: str | None,
    title: str | None,
    interactive: bool = True,
    cdn: bool = False,
    overview: str | None = None,
    deep: str | None = None,
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
    # level 1: the overview, or the coach's summary pulled from the debrief
    quick = (
        card(md_to_html(overview), "debrief")
        if overview
        else (coach_card(debrief) if debrief else "")
    )
    if quick:
        pages.append(
            page(
                "quick",
                "Quick look",
                "The main takeaways. Switch to Debrief or Deep dive above for more.",
                quick,
                1,
            )
        )
    # level 2: the debrief and the event numbers
    if debrief:
        pages.append(
            page(
                "debrief",
                "Debrief",
                "The coaching debrief.",
                card(md_to_html(debrief), "debrief"),
                2,
            )
        )
    summary_body = coach_card(debrief) if debrief and overview else ""
    summary_body += card(md_to_html(exec_md.read_text()), "exec") if exec_md.exists() else ""
    summary_body += card(md_to_html(event))
    pages.append(
        page(
            "summary",
            "Summary",
            "The whole event at a glance, then each day and each race.",
            summary_body,
            2,
        )
    )
    # level 3: the coach's race notes, then every number
    if deep:
        pages.append(
            page(
                "notes",
                "Race notes",
                "The nuances, race by race and leg by leg.",
                card(md_to_html(deep), "debrief"),
                3,
            )
        )
    # Tacks and Gybes: every comparable maneuver overlaid (needs overlay.json from analyze.py)
    overlays = [
        json.loads((d / "overlay.json").read_text())
        for d, _ in runs
        if INTERACTIVE and (d / "overlay.json").exists()
    ]
    overlay_pages, overlay_js = MO.report_pages(overlays, MO.boat_order(o["boat"] for o in overlays))
    if runs:
        pages += [
            starts_page(runs),
            tacks_gybes_page(runs, overlay_pages),
            up_down_page(runs),
            roundings_page(runs),
        ]
    # Current: COG vs heading, from this boat and any others analysed alongside it
    cur = CU.page_parts(CU.fleet_dirs(report_dir), focus=runs[0][1].get("boat") if runs else None) if INTERACTIVE and runs else None
    if cur:
        pages.append(page("current", "Current", CU.INTRO, cur[0], 3))
        overlay_js += cur[1]
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
            3,
        )
    )

    names = {
        "quick": "Quick look",
        "summary": "Summary",
        "debrief": "Debrief",
        "notes": "Race notes",
        "starts": "Starts",
        "tacks-gybes": "Tacks and Gybes",
        "upwind-downwind": "Upwind and Downwind",
        "roundings": "Roundings",
        "current": "Current",
        "races": "Race by race",
    }
    joined = "".join(pages)
    ids = re.findall(r'<section class="page" id="([^"]+)"', joined)
    lvl = dict(re.findall(r'<section class="page" id="([^"]+)" data-level="(\d)"', joined))
    for i in ids:  # the data pages (starts, upwind, ...) are deep-dive pages
        lvl.setdefault(i, "3")
    pages = [
        re.sub(
            r'^<section class="page" id="([^"]+)">',
            lambda m: f'<section class="page" id="{m.group(1)}" data-level="{lvl[m.group(1)]}">',
            p,
        )
        for p in pages
    ]
    first = {}
    for i in ids:
        first.setdefault(lvl[i], i)
    levels = (
        '<nav class="levels" aria-label="Depth">'
        + "".join(
            f'<a href="#{first[str(n)]}" data-level="{n}">{name}<small>{hint}</small></a>'
            for n, name, hint in LEVELS
            if str(n) in first
        )
        + "</nav>"
    )
    nav = (
        '<nav class="pages">'
        + "".join(
            f'<a href="#{i}" data-level="{lvl[i]}">{names.get(i, i)}</a>'
            for i in ids
            if sum(v == lvl[i] for v in lvl.values()) > 1
        )
        + "</nav>"
    )
    parts = [
        f'<header class="top"><h1>{html.escape(page_title)}</h1>',
        "<p>Race analysis from Njord data</p></header>",
        levels,
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
        f"<body><main>{''.join(parts)}</main>{chart_scripts(races, cdn)}{overlay_js}</body></html>"
    )


def chart_scripts(races: list[Path], cdn: bool = False) -> str:
    """Race data (once each) plus the chart library and renderer, inline unless cdn."""
    if not INTERACTIVE:
        return ""
    data = "".join(
        f'<script type="application/json" id="race-{d.name}">'
        + (d / "plotdata.json").read_text().replace("</", "<\\/")
        + "</script>"
        for d in races
        if (d / "plotdata.json").exists()
    )
    lib = (
        f'<script src="{PLOTLY_CDN}"></script>' if cdn else f"<script>{VENDOR.read_text()}</script>"
    )
    ladder_js = (Path(__file__).parent / "ladder.js").read_text()
    return data + lib + f"<script>{ladder_js}</script><script>{CHARTS_JS.read_text()}</script>"


def write_html(
    report_dir: Path,
    debrief_path: Path | None = None,
    out: Path | None = None,
    title: str | None = None,
    interactive: bool = True,
    cdn: bool = False,
    overview_path: Path | None = None,
    deep_path: Path | None = None,
) -> Path:
    def text(p):
        return p.read_text() if p else None

    out = out or report_dir / "report.html"
    out.write_text(
        build(
            report_dir,
            text(debrief_path),
            title,
            interactive,
            cdn,
            text(overview_path),
            text(deep_path),
        )
    )
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("report_dir", type=Path)
    ap.add_argument("--debrief", type=Path, help="the coaching debrief (Markdown)")
    ap.add_argument("--overview", type=Path, help="short Quick look overview (Markdown)")
    ap.add_argument("--deep", type=Path, help="Deep dive race notes (Markdown)")
    ap.add_argument("--out", type=Path, help="default: <report_dir>/report.html")
    ap.add_argument("--title")
    ap.add_argument("--static", action="store_true", help="PNG plots instead of interactive charts")
    ap.add_argument(
        "--cdn",
        action="store_true",
        help="load Plotly from jsDelivr instead of inlining it (smaller; needs internet)",
    )
    a = ap.parse_args()
    print(
        write_html(a.report_dir, a.debrief, a.out, a.title, not a.static, a.cdn, a.overview, a.deep)
    )


if __name__ == "__main__":
    main()
