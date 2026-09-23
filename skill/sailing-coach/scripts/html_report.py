#!/usr/bin/env python3
"""Render an analyze.py report folder as one self-contained HTML file.

Usage:
  python html_report.py <report_dir> [--debrief debrief.md] [--out report.html]

Reads <report_dir>/event.md, each <race>/report.md and its PNGs, and an optional debrief
(Markdown written by the coach). Images are embedded, so the HTML file can be opened, emailed
or uploaded on its own. No third-party packages.
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
footer { color: var(--muted); font-size: 0.85rem; margin-top: 32px; }
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


def race_section(race_dir: Path) -> tuple[str, str, str]:
    md = (race_dir / "report.md").read_text()
    md = md.split("\n## Plots")[0]  # the HTML shows the plots themselves
    title = md.splitlines()[0].lstrip("# ").strip()
    body = md_to_html("\n".join(md.splitlines()[1:]))
    body = body.replace("<li>Wind: NOT trusted", '<li class="note">Wind: NOT trusted')
    figs = [
        img_tag(race_dir / f, cap, narrow=f == "track.png")
        for f, cap in PLOTS
        if (race_dir / f).exists()
    ]
    anchor = race_dir.name
    plots = f'<h2>Plots</h2><div class="plots">{"".join(figs)}</div>' if figs else ""
    return (
        anchor,
        title,
        f'<section class="card" id="{anchor}"><h1>{html.escape(title)}</h1>{body}{plots}</section>',
    )


def default_title(races: list[Path]) -> str:
    """'Mojo · July ODW · Sun 19 Jul 2026' from the first race's summary.json."""
    if not races or not (races[0] / "summary.json").exists():
        return "Race report"
    s = json.loads((races[0] / "summary.json").read_text())
    parts = [s.get("boat"), s.get("event")]
    if s.get("gun_local"):
        parts.append(datetime.fromisoformat(s["gun_local"]).strftime("%a %-d %b %Y"))
    return " · ".join(p for p in parts if p) or "Race report"


def build(report_dir: Path, debrief: str | None, title: str | None) -> str:
    races = sorted(p.parent for p in report_dir.glob("*/report.md"))
    sections = [race_section(d) for d in races]
    page_title = title or default_title(races)

    parts = [f'<header class="top"><h1>{html.escape(page_title)}</h1>']
    parts.append("<p>Race analysis from Njord data</p></header>")
    nav = [("debrief", "Debrief")] if debrief else []
    nav += [("summary", "Summary")] + [
        (a, t.split()[-2] + " " + t.split()[-1]) for a, t, _ in sections
    ]
    parts.append(
        "<nav>" + "".join(f'<a href="#{a}">{html.escape(t)}</a>' for a, t in nav) + "</nav>"
    )
    if debrief:
        parts.append(f'<section class="card debrief" id="debrief">{md_to_html(debrief)}</section>')
    event = (report_dir / "event.md").read_text().split("\nPer-race detail")[0]
    event = event.replace("# Event summary", "## Summary")
    parts.append(f'<section class="card" id="summary">{md_to_html(event)}</section>')
    parts += [s for _, _, s in sections]
    parts.append(
        "<footer>Numbers from analyze.py. Speeds are over ground unless noted. "
        "Times are local to the event.</footer>"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(page_title)}</title><style>{CSS}</style></head>"
        f"<body><main>{''.join(parts)}</main></body></html>"
    )


def write_html(
    report_dir: Path,
    debrief_path: Path | None = None,
    out: Path | None = None,
    title: str | None = None,
) -> Path:
    debrief = debrief_path.read_text() if debrief_path else None
    out = out or report_dir / "report.html"
    out.write_text(build(report_dir, debrief, title))
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("report_dir", type=Path)
    ap.add_argument("--debrief", type=Path, help="Markdown debrief to put at the top")
    ap.add_argument("--out", type=Path, help="default: <report_dir>/report.html")
    ap.add_argument("--title")
    a = ap.parse_args()
    print(write_html(a.report_dir, a.debrief, a.out, a.title))


if __name__ == "__main__":
    main()
