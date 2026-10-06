"""Reading aids for a boat report (html_report.py): the Quick look scorecard, tap-to-explain terms,
"Show me" links from the debrief to the chart behind each point, phone-friendly tables, and the
across-regattas trend page.

Everything here works from analyze.py's per-race summary.json files, for this boat and (in the fleet
layout, <reports>/<boat>/) the other boats analysed alongside it.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

# The 2026 Worlds standard on this water (references/playbook/starts.md): 1–2 m back at full speed
WORLDS_BACK_M = (1, 2)
LENGTH_M = 9.3  # one Etchells length

# Plain-language definitions, shown when a term is tapped or hovered. Keys are matched as whole words,
# case-insensitively; each term is explained the first time it appears on a page.
TERMS = {
    "VMC": "Velocity made good on course: speed toward the next mark. The number that decides who gets there first.",
    "VMG": "Velocity made good: speed straight upwind (or downwind), ignoring where the mark is.",
    "SOG": "Speed over the ground, from GPS. Includes the effect of current.",
    "COG": "Course over the ground, from GPS: where the boat actually went, leeway and current included.",
    "TWA": "True wind angle: the angle between the bow and the true wind.",
    "lengths": "Etchells boat lengths. One length is about 9.3 m (30.5 ft).",
    "lifted": "The wind has shifted so you can point closer to the mark on this tack. Stay on it.",
    "lift": "A wind shift that lets you point closer to the mark on your tack. Stay on it.",
    "headed": "The wind has shifted so you point further from the mark on this tack. Usually a cue to tack.",
    "header": "A wind shift that points you further from the mark on your tack. Usually a cue to tack.",
    "time on distance": "Judging the start by comparing time left to the gun with the time it takes to sail to the line at your speed.",
    "burn time": "Spare seconds before the gun after allowing for the run to the line. Burn them off, then go.",
    "layline": "The line from which you can just lay the mark on one tack. Going past it (overstanding) sails extra distance.",
    "overstood": "Sailed past the layline, so the boat reached the mark on a longer, slower angle than needed.",
    "footing": "Sailing a little lower than close-hauled for more speed.",
    "pinching": "Sailing too high: pointing close to the wind at the cost of speed.",
    "polar": "Speed plotted against angle to the wind: the shape shows the speed and angle the boat sailed.",
    "rhumb line": "The straight line from one mark to the next.",
    "favoured end": "The end of the start line closer to the windward mark, worth metres to whoever starts there at speed.",
    "oscillating": "Wind swinging back and forth around an average. Tack on the headers, staying near the middle.",
    "persistent": "A wind shift that keeps going one way. Get to the side it's going to before you tack back.",
    "heel": "How far the boat leans, in degrees.",
    "gate": "Two leeward marks; round either one.",
    "OCS": "On course side: over the line at the gun, and must return to start.",
    "accel": "Change in speed from 5 s before to 5 s after the gun: positive means building speed through the line.",
}


def _load(d: Path) -> dict:
    return json.loads((d / "summary.json").read_text())


def _boat_runs(boat_dir: Path) -> list[tuple[Path, dict]]:
    return [(p.parent, _load(p.parent)) for p in sorted(boat_dir.glob("race*/summary.json"))]


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def boat_metrics(runs: list[tuple[Path, dict]]) -> dict:
    """One boat's regatta averages for the scorecard."""
    st = [s.get("start") or {} for _, s in runs]
    tacks = [((s.get("maneuver_summary") or {}).get("Tack") or {}) for _, s in runs]
    rounds = [r for _, s in runs for r in (s.get("roundings") or [])]
    return {
        "late_s": _avg([x.get("late_s") for x in st if not x.get("ocs_at_gun_m")]),
        "back_m": _avg([x.get("below_line_+0s_m") for x in st]),
        "gun_kt": _avg([x.get("sog_+0s") for x in st]),
        "tack_m": _avg([t.get("distance_lost_avg_m") for t in tacks]),
        "tack_pct": _avg([t.get("speed_loss_avg_pct") for t in tacks]),
        "round_m": _avg([r.get("metres_lost") for r in rounds]),
    }


def leg_losses(fleet: dict[str, list[tuple[Path, dict]]], boat: str, kind: str) -> tuple[float | None, int]:
    """Seconds this boat lost to the fastest tracked boat on each leg of this kind (legs at least one
    other tracked boat sailed too), and how many legs that covers."""
    by_race = {b: {s["stem"]: s for _, s in runs} for b, runs in fleet.items()}
    lost, n = 0.0, 0
    for stem, s in by_race.get(boat, {}).items():
        for lg in s["legs"]:
            if lg["type"] != kind or not lg.get("duration_s"):
                continue
            times = []
            for b in fleet:
                o = by_race[b].get(stem)
                m = next((x for x in (o or {}).get("legs", []) if x["leg"] == lg["leg"]), None)
                if m and m.get("duration_s"):
                    times.append(m["duration_s"])
            if len(times) >= 2:  # at least one other boat sailed this leg
                lost += lg["duration_s"] - min(times)
                n += 1
    return (lost if n else None), n


def _fleet(report_dir: Path) -> dict[str, list[tuple[Path, dict]]]:
    import current as CU  # same rule for which boats sailed alongside this one

    out = {}
    for d in CU.fleet_dirs(report_dir):
        runs = _boat_runs(d)
        if runs:
            out[runs[0][1]["boat"]] = runs
    return out


def _mmss(sec: float) -> str:
    sec = round(sec)
    return f"{sec // 60}:{sec % 60:02d}" if sec >= 60 else f"{sec} s"


def _m(v: float) -> str:
    return f"{v:.0f} m ({v / LENGTH_M:.1f} lengths)"


def scorecard(report_dir: Path) -> str:
    """Five tiles: starts, upwind, downwind, tacks and gybes, roundings. This boat against the best
    tracked boat in each, and the Worlds standard for starts."""
    fleet = _fleet(report_dir)
    runs = _boat_runs(report_dir)
    if not runs:
        return ""
    me = runs[0][1]["boat"]
    fleet.setdefault(me, runs)
    others = [b for b in fleet if b != me]
    mets = {b: boat_metrics(r) for b, r in fleet.items()}

    def best(key, low=True):
        vals = [(mets[b][key], b) for b in fleet if mets[b][key] is not None]
        if not vals:
            return None, None
        v, b = (min if low else max)(vals)
        return v, b

    def tile(area, big, sub, cmp_, link):
        return (
            f'<a class="tile" href="#{link}"><span class="t-area">{area}</span>'
            f'<span class="t-big">{big}</span><span class="t-sub">{sub}</span>'
            f'<span class="t-cmp">{cmp_}</span></a>'
        )

    tiles = []
    m = mets[me]
    # Starts
    if m["back_m"] is not None:
        bv, bb = best("back_m")
        cmp_ = f"Worlds standard: {WORLDS_BACK_M[0]}–{WORLDS_BACK_M[1]} m back at full speed"
        if others and bb and bb != me:
            cmp_ += f". Best here: {html.escape(bb)}, {bv:.1f} m"
        tiles.append(
            tile(
                "Starts",
                f"{m['back_m']:.1f} m back",
                f"at the gun on average, {m['late_s']:.0f} s late, {m['gun_kt']:.1f} kt"
                if m["late_s"] is not None and m["gun_kt"] is not None
                else "at the gun on average",
                cmp_,
                "starts",
            )
        )
    # Upwind and downwind: time lost to the fastest tracked boat, leg by leg
    if others:
        for kind, area, legs in (("upwind", "Upwind", "beat"), ("downwind", "Downwind", "run")):
            lost, n = leg_losses(fleet, me, kind)
            if lost is None:
                continue
            all_lost = {b: leg_losses(fleet, b, kind)[0] for b in fleet}
            bb = min((b for b in all_lost if all_lost[b] is not None), key=lambda b: all_lost[b])
            cmp_ = (
                "Least time lost of the tracked boats"
                if bb == me
                else f"Least lost: {html.escape(bb)}, {_mmss(all_lost[bb])}"
            )
            tiles.append(
                tile(
                    area,
                    f"{_mmss(lost)} lost",
                    f"to the fastest tracked boat, summed over {n} {legs}{'s' * (n != 1)}",
                    cmp_,
                    "upwind-downwind",
                )
            )
    # Tacks
    if m["tack_m"] is not None:
        bv, bb = best("tack_m")
        cmp_ = (
            "Best of the tracked boats"
            if bb == me or not others
            else f"Best: {html.escape(bb)}, {bv:.1f} m"
        )
        tiles.append(
            tile(
                "Tacks",
                f"{m['tack_m']:.1f} m lost",
                f"per tack on average ({m['tack_pct']:.0f}% speed loss)"
                if m["tack_pct"] is not None
                else "per tack on average",
                cmp_,
                "tacks-gybes",
            )
        )
    # Roundings
    if m["round_m"] is not None:
        bv, bb = best("round_m")
        cmp_ = (
            "Best of the tracked boats"
            if bb == me or not others
            else f"Best: {html.escape(bb)}, {bv:.0f} m"
        )
        tiles.append(
            tile("Roundings", f"{m['round_m']:.0f} m lost", "toward the marks per rounding", cmp_, "roundings")
        )
    if not tiles:
        return ""
    note = (
        f"Compared with the other tracked boats ({', '.join(html.escape(o) for o in others)}), not the whole fleet. "
        if others
        else ""
    )
    return (
        '<section class="card"><h2>Scorecard</h2><div class="tiles">'
        + "".join(tiles)
        + f'</div><p class="facts">{note}Tap a tile for the detail.</p></section>'
    )


def race_ids(runs) -> dict:
    """{"Race 3": "race3", ...} for the Show me links."""
    return {s["race"]: d.name for d, s in runs}


# ---- across regattas ----


def event_metrics(boat_dir: Path) -> dict | None:
    """This boat's numbers for one regatta, for the trend page."""
    runs = _boat_runs(boat_dir)
    if not runs:
        return None
    s0 = runs[0][1]
    fleet = _fleet(boat_dir)
    me = s0["boat"]
    fleet.setdefault(me, runs)
    m = boat_metrics(runs)
    up, nu = leg_losses(fleet, me, "upwind") if len(fleet) > 1 else (None, 0)
    dn, nd = leg_losses(fleet, me, "downwind") if len(fleet) > 1 else (None, 0)
    return {
        "event": s0.get("event"),
        "date": (s0.get("gun_local") or "")[:10],
        "races": len(runs),
        "boats": sorted(b for b in fleet if b != me),
        **m,
        "up_lost_per_beat_s": up / nu if up is not None and nu else None,
        "dn_lost_per_run_s": dn / nd if dn is not None and nd else None,
    }


def trend_page(history: list[dict]) -> str:
    """A table and small charts of this boat's numbers regatta by regatta, oldest first."""
    if len(history) < 2:
        return ""
    history = sorted(history, key=lambda h: h["date"])

    def f(v, fmt):
        return "–" if v is None else fmt.format(v)

    rows = "".join(
        f"<tr><td>{html.escape(h['event'] or '')}</td><td>{h['date']}</td><td>{h['races']}</td>"
        f"<td>{f(h['late_s'], '{:.0f}')}</td><td>{f(h['back_m'], '{:.1f}')}</td><td>{f(h['gun_kt'], '{:.1f}')}</td>"
        f"<td>{f(h['up_lost_per_beat_s'], '{:.0f}')}</td><td>{f(h['dn_lost_per_run_s'], '{:.0f}')}</td>"
        f"<td>{f(h['tack_m'], '{:.0f}')}</td><td>{f(h['tack_pct'], '{:.0f}')}</td><td>{f(h['round_m'], '{:.0f}')}</td>"
        f"<td>{html.escape(', '.join(h['boats']) or '–')}</td></tr>"
        for h in history
    )
    table = (
        '<div class="scroll"><table data-keep="0,3,4,6,8"><tr><th>Regatta</th><th>Date</th><th>Races</th>'
        "<th>Late (s)</th><th>m back at gun</th><th>kt at gun</th><th>s lost per beat</th><th>s lost per run</th>"
        "<th>m lost per tack</th><th>Tack speed loss %</th><th>m lost per rounding</th><th>Compared with</th></tr>"
        + rows
        + "</table></div>"
    )
    data = json.dumps(history).replace("</", "<\\/")
    charts = (
        '<div class="trend-charts">'
        + "".join(
            f'<div class="trend" data-key="{k}" data-label="{html.escape(lbl)}" data-better="{b}"></div>'
            for k, lbl, b in (
                ("back_m", "Metres back at the gun (lower is better)", "low"),
                ("late_s", "Seconds late at the start (lower is better)", "low"),
                ("up_lost_per_beat_s", "Seconds lost per beat to the fastest tracked boat", "low"),
                ("dn_lost_per_run_s", "Seconds lost per run to the fastest tracked boat", "low"),
                ("tack_m", "Metres lost per tack", "low"),
                ("round_m", "Metres lost per rounding", "low"),
            )
        )
        + f'</div><script type="application/json" id="trend-data">{data}</script>'
    )
    return (
        '<section class="card"><h2>Regatta by regatta</h2>'
        '<p class="facts">Seconds lost per beat and run are against the fastest tracked boat on each leg, so '
        "they depend on who else was tracked (last column). Starts, tacks and roundings are this boat's own "
        "numbers. Wind and sea differ between regattas, so read the direction more than the exact values. A windward "
        "rounding with an offset mark counts the short reach to the offset, so courses with offsets read higher "
        "there.</p>"
        + table
        + "</section>"
        + '<section class="card"><h2>Trends</h2>'
        + charts
        + "</section>"
    )


CSS = """
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(145px, 1fr)); gap: 10px; margin: 8px 0; }
a.tile { display: grid; align-content: start; gap: 2px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 10px;
  background: var(--bg); color: var(--ink); text-decoration: none; }
a.tile:hover { border-color: var(--accent); }
.t-area { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ink2); font-weight: 600; }
.t-big { font-size: 1.35rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.t-sub { font-size: 0.85rem; color: var(--ink2); }
.t-cmp { font-size: 0.82rem; color: var(--muted); margin-top: 4px; }
.term { border-bottom: 1px dotted var(--ink2); cursor: help; }
.term:focus { outline: 2px solid var(--accent); outline-offset: 1px; border-radius: 2px; }
#defbox { position: fixed; left: 50%; bottom: 16px; transform: translateX(-50%); z-index: 50;
  max-width: min(560px, calc(100vw - 32px)); background: var(--ink); color: var(--bg); padding: 10px 14px;
  border-radius: 10px; font-size: 0.9rem; line-height: 1.4; box-shadow: 0 4px 16px rgba(0,0,0,.25); }
#defbox[hidden] { display: none; }
#defbox b { color: inherit; }
.show-me { display: inline; font-size: 0.85rem; white-space: normal; }
.show-me .sm-l { white-space: nowrap; color: var(--ink2); }
.show-me a { color: var(--accent); text-decoration: none; background: var(--accent-soft); padding: 1px 8px;
  border-radius: 999px; margin-left: 4px; white-space: nowrap; }
.scroll th:first-child, .scroll td:first-child { position: sticky; left: 0; z-index: 1; background: var(--card); }
.scroll th:first-child { z-index: 2; background: var(--bg); }
button.cols { display: none; font: inherit; font-size: 0.82rem; color: var(--accent); background: none;
  border: 1px solid var(--line); border-radius: 999px; padding: 2px 10px; margin: 4px 0 0; cursor: pointer; }
@media (max-width: 700px) {
  button.cols { display: inline-block; }
  .scroll.few .more { display: none; }
}
.trend-charts { display: grid; grid-template-columns: 1fr; gap: 8px; }
@media (min-width: 900px) { .trend-charts { grid-template-columns: 1fr 1fr; } }
.trend { min-height: 240px; }
@media print { #defbox, button.cols, .show-me { display: none; } .scroll.few .more { display: table-cell; } }
"""


JS = r"""
(function () {
  const TERMS = __TERMS__;
  const RACES = __RACES__;
  const IDS = __IDS__;  // section id prefix for each topic on this report ('' = not on this report)
  // ---- tables: race column stays put; on a phone, the key columns first ----
  function tables() {
    document.querySelectorAll('.scroll > table').forEach(t => {
      if (t.dataset.done) return; t.dataset.done = 1;
      const n = t.rows[0] ? t.rows[0].cells.length : 0;
      if (n <= 5) return;
      const keep = (t.dataset.keep || '0,1,2,3').split(',').map(Number);
      [...t.rows].forEach(r => [...r.cells].forEach((c, i) => { if (!keep.includes(i)) c.classList.add('more'); }));
      const w = t.parentElement; w.classList.add('few');
      const b = document.createElement('button'); b.type = 'button'; b.className = 'cols';
      const label = () => { b.textContent = w.classList.contains('few') ? `Show all ${n} columns` : 'Show fewer columns'; };
      b.onclick = () => { w.classList.toggle('few'); label(); }; label();
      w.after(b);
    });
  }
  // ---- terms: explained on tap or hover, the first time each appears on a page ----
  const keys = Object.keys(TERMS).sort((a, b) => b.length - a.length);
  const re = new RegExp('\\b(' + keys.map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')\\b', 'i');
  function lookup(w) { const k = keys.find(k => k.toLowerCase() === w.toLowerCase()); return k ? [k, TERMS[k]] : null; }
  function terms() {
    document.querySelectorAll('.page').forEach(pg => {
      const seen = new Set();
      const walker = document.createTreeWalker(pg, NodeFilter.SHOW_TEXT, {
        acceptNode: n => {
          const p = n.parentElement;
          if (!p || p.closest('table, .chart, .tk, .trend, nav, script, style, a, button, h1, .term, .t-big, summary .dd-h')) return NodeFilter.FILTER_REJECT;
          return re.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
        }
      });
      const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
      nodes.forEach(node => {
        let text = node.nodeValue, m, frag = null, last = 0;
        const g = new RegExp(re.source, 'gi');
        while ((m = g.exec(text))) {
          const hit = lookup(m[1]); if (!hit) continue;
          const key = hit[0].toLowerCase();
          if (seen.has(key)) continue; seen.add(key);
          frag = frag || document.createDocumentFragment();
          frag.append(text.slice(last, m.index));
          const s = document.createElement('span');
          s.className = 'term'; s.tabIndex = 0; s.textContent = m[1]; s.dataset.term = hit[0];
          s.setAttribute('role', 'button'); s.setAttribute('aria-label', m[1] + ': ' + hit[1]);
          frag.append(s); last = m.index + m[1].length;
        }
        if (frag) { frag.append(text.slice(last)); node.replaceWith(frag); }
      });
    });
    const box = document.createElement('div'); box.id = 'defbox'; box.hidden = true; box.setAttribute('role', 'status');
    document.body.append(box);
    let pinned = null;
    const showDef = el => { const k = el.dataset.term; box.innerHTML = '<b>' + k + ':</b> ' + TERMS[k]; box.hidden = false; };
    document.addEventListener('mouseover', e => { const t = e.target.closest && e.target.closest('.term'); if (t) showDef(t); });
    document.addEventListener('mouseout', e => { const t = e.target.closest && e.target.closest('.term'); if (t && t !== pinned) box.hidden = true; });
    document.addEventListener('click', e => {
      const t = e.target.closest && e.target.closest('.term');
      if (t) { pinned = t; showDef(t); e.stopPropagation(); } else { pinned = null; box.hidden = true; }
    });
    document.addEventListener('keydown', e => {
      const t = document.activeElement;
      if (t && t.classList && t.classList.contains('term') && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); pinned = t; showDef(t); }
      if (e.key === 'Escape') { pinned = null; box.hidden = true; }
    });
  }
  // ---- Show me: each debrief point that names a race links to the chart behind it ----
  const TOPICS = [
    [/\b(start|starts|gun|late|burn)\b/i, 'starts', 'start'],
    [/\b(rounding|mark room|windward mark|leeward mark)\b/i, 'roundings', 'rounding'],
    [/\b(tack|tacks|tacked|gybe|gybes|gybed)\b/i, 'tg', 'tacks and gybes'],
    [/\b(beat|beats|upwind|shift|shifts|wind went|header|lift)\b/i, 'ud', 'beats'],
    [/\b(run|runs|downwind)\b/i, 'ud', 'runs'],
  ];
  function showMe() {
    document.querySelectorAll('.debrief li').forEach(li => {
      if (li.querySelector(':scope > .show-me')) return;
      // not the opening context bullets under the debrief's title
      const list = li.parentElement, prev = list && list.previousElementSibling;
      if (prev && /^H[12]$/.test(prev.tagName) && !li.parentElement.closest('li')) return;
      const own = [...li.childNodes].filter(n => !(n.nodeType === 1 && /^(UL|OL)$/.test(n.tagName))).map(n => n.textContent).join(' ');
      const races = [...new Set((own.match(/\bRaces? \d+(?:\s*(?:–|-|and|,)\s*\d+)*/g) || []).flatMap(r => {
        const nums = r.match(/\d+/g).map(Number);
        if (/–|-/.test(r) && nums.length === 2) { const a = []; for (let i = nums[0]; i <= nums[1]; i++) a.push(i); return a; }
        return nums;
      }))].filter(n => RACES['Race ' + n]);
      if (!races.length) return;
      const found = new Map();  // id -> label; beats and runs share a section
      TOPICS.forEach(([rx, pre, what]) => {
        if (!rx.test(own)) return;
        races.forEach(n => {
          if (!IDS[pre]) return;
          const id = IDS[pre] + RACES['Race ' + n];
          if (!document.getElementById(id)) return;
          found.set(id, found.has(id) ? found.get(id) + ' and ' + what : `Race ${n} ${what}`);
        });
      });
      const links = [...found].slice(0, 3).map(([id, label]) => `<a href="#${id}">${label}</a>`);
      if (!links.length) races.slice(0, 2).forEach(n => links.push(`<a href="#${IDS.race}${RACES['Race ' + n]}">Race ${n}</a>`));
      const span = document.createElement('span'); span.className = 'show-me';
      span.innerHTML = ' <span class="sm-l">Show me:</span>' + links.join('');
      const sub = li.querySelector(':scope > ul, :scope > ol');
      sub ? li.insertBefore(span, sub) : li.append(span);
    });
  }
  // ---- trend charts ----
  function trends() {
    const el = document.getElementById('trend-data'); if (!el || !window.Plotly) return;
    const H = JSON.parse(el.textContent);
    const css = getComputedStyle(document.documentElement);
    const ink = css.getPropertyValue('--ink2').trim(), line = css.getPropertyValue('--line').trim(), acc = css.getPropertyValue('--accent').trim();
    document.querySelectorAll('.trend').forEach(div => {
      if (!div.offsetParent) return;
      const k = div.dataset.key;
      const pts = H.filter(h => h[k] !== null && h[k] !== undefined);
      if (pts.length < 2) { div.textContent = ''; div.style.minHeight = '0'; return; }
      Plotly.react(div, [{
        x: pts.map(h => h.event.replace(/^Etchells\s+/, '').replace(/\s+20\d\d$/, '') + '<br>' +
          new Date(h.date + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', year: 'numeric' })), y: pts.map(h => h[k]), mode: 'lines+markers+text',
        text: pts.map(h => (Math.round(h[k] * 10) / 10).toString()), textposition: 'top center',
        line: { color: acc, width: 2 }, marker: { size: 9, color: acc }, hovertemplate: '%{y:.1f}<extra></extra>', cliponaxis: false,
      }], {
        title: { text: div.dataset.label, font: { size: 13, color: ink }, x: 0, xanchor: 'left' },
        margin: { l: 40, r: 36, t: 40, b: 50 }, height: 240, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: ink, size: 11 }, xaxis: { gridcolor: line, type: 'category' }, yaxis: { gridcolor: line, rangemode: 'tozero', zerolinecolor: line },
        showlegend: false,
      }, { displaylogo: false, responsive: true, displayModeBar: false });
    });
  }
  document.addEventListener('DOMContentLoaded', () => { tables(); showMe(); terms(); setTimeout(trends, 0); });
  window.addEventListener('hashchange', () => setTimeout(trends, 0));
})();
"""


BOAT_IDS = {"starts": "starts-", "roundings": "roundings-", "tg": "tg-", "ud": "ud-", "race": ""}
FLEET_IDS = {"starts": "starts-", "roundings": "race-", "tg": "", "ud": "wind-", "race": "race-"}


def _js(races: dict, ids: dict) -> str:
    return (
        JS.replace("__TERMS__", json.dumps(TERMS))
        .replace("__RACES__", json.dumps(races))
        .replace("__IDS__", json.dumps(ids))
    )


def assets(runs) -> tuple[str, str]:
    return CSS, _js(race_ids(runs), BOAT_IDS)


def fleet_assets(fa: dict) -> str:
    """The same reading aids for the fleet report (fleet.py)."""
    return _js({r["race"]: r["stem"] for r in fa["races"]}, FLEET_IDS)


def fleet_scorecard(reports_dir: Path) -> str:
    """The fleet version of the scorecard: each area's best tracked boat, and every boat's number."""
    fleet = {}
    for d in sorted(Path(reports_dir).iterdir()):
        if d.is_dir():
            runs = _boat_runs(d)
            if runs:
                fleet[runs[0][1]["boat"]] = runs
    if len(fleet) < 2:
        return ""
    mets = {b: boat_metrics(r) for b, r in fleet.items()}
    up = {b: leg_losses(fleet, b, "upwind")[0] for b in fleet}
    dn = {b: leg_losses(fleet, b, "downwind")[0] for b in fleet}
    areas = [
        ("Starts", "starts", {b: mets[b]["back_m"] for b in fleet}, lambda v: f"{v:.1f} m", "back at the gun on average",
         f"Worlds standard: {WORLDS_BACK_M[0]}–{WORLDS_BACK_M[1]} m back at full speed"),
        ("Upwind", "pairs", up, _mmss, "lost to the fastest boat, summed over the beats", ""),
        ("Downwind", "pairs", dn, _mmss, "lost to the fastest boat, summed over the runs", ""),
        ("Tacks", "races", {b: mets[b]["tack_m"] for b in fleet}, lambda v: f"{v:.1f} m", "lost per tack on average", ""),
        ("Roundings", "races", {b: mets[b]["round_m"] for b in fleet}, lambda v: f"{v:.0f} m", "lost toward the marks per rounding", ""),
    ]
    tiles = []
    for area, link, vals, fmt, what, extra in areas:
        vals = {b: v for b, v in vals.items() if v is not None}
        if not vals:
            continue
        order = sorted(vals, key=lambda b: vals[b])
        best = order[0]
        rest = ", ".join(f"{html.escape(b)} {fmt(vals[b])}" for b in order[1:])
        tiles.append(
            f'<a class="tile" href="#{link}"><span class="t-area">{area}</span>'
            f'<span class="t-big">{html.escape(best)}</span>'
            f'<span class="t-sub">{fmt(vals[best])} {what}</span>'
            f'<span class="t-cmp">{rest}{(". " + extra) if extra else ""}</span></a>'
        )
    return (
        '<section class="card"><h2>Scorecard</h2><div class="tiles">'
        + "".join(tiles)
        + '</div><p class="facts">The best tracked boat in each area, then the others. Tap a tile for the detail.</p></section>'
    )
