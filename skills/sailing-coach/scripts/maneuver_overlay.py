#!/usr/bin/env python3
"""Tack and gybe overlays: SOG, heading and turn angle through every maneuver, best ones highlighted.

analyze.py calls race_overlay() for each race and writes <race>/overlay.json. html_report.py turns
those into Tacks and Gybes pages in a boat report's Deep dive. This script's CLI builds the same
view as one standalone page across several boats (the fleet):

  python maneuver_overlay.py --reports <dir>/report --kind tack --out <dir>/report/tacks.html \
      --title "<Event> · Tacks"

Every maneuver is anchored the same way for every boat: t = 0 is the moment the heading passes
halfway between the settled heading before and after the turn (head to wind in a tack, dead
downwind in a gybe). The ranking (by time lost) and the "what the best ones did" panel are
computed in the page, so they follow its filters.
"""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PRE, POST = 15, 40  # seconds shown either side of the middle of the turn
ENTRY = (-10, -4)  # entry speed window, same as analyze.py
RECOVERY_PCT = 0.95
KT = 0.514444
MIN_ENTRY_KT = 4.0  # below this it's a restart from a stall (start, traffic), not a maneuver to compare
UPWIND_HEEL = 10.0  # heel (abs) that separates a beat from a run
PLOTLY_CDN = "https://cdn.jsdelivr.net/npm/plotly.js-basic-dist-min@2.35.3/plotly-basic.min.js"

# Which maneuvers are comparable. A tack: heeled on both sides and under 110° (more is a tack
# into a bear-away at the windward mark). A gybe: flat on both sides (not a gybe-set off the
# windward mark or a gybe into the leeward rounding) and 20-120°.
KINDS = {
    "tack": {"event": "Tack", "heeled": True, "angle": (0, 110)},
    "gybe": {"event": "Gybe", "heeled": False, "angle": (20, 120)},
}


def adiff(a, b):
    """Signed smallest angle from a to b, degrees."""
    return (b - a + 180.0) % 360.0 - 180.0


def circ_mean(deg):
    r = np.radians(np.asarray(deg, dtype=float))
    r = r[~np.isnan(r)]
    if not len(r):
        return np.nan
    return math.degrees(math.atan2(np.sin(r).mean(), np.cos(r).mean())) % 360


def unwrap_from(ref, series):
    """Heading relative to ref, unwrapped so a turn reads as a continuous line."""
    out = adiff(ref, np.asarray(series, dtype=float))
    for i in range(1, len(out)):
        if not (np.isnan(out[i]) or np.isnan(out[i - 1])):
            out[i] = out[i - 1] + adiff(out[i - 1], out[i])
    return out


def record(s: pd.DataFrame, t_rep: float, kind: str) -> dict | None:
    """One maneuver from a race frame indexed by seconds from the gun (tg)."""
    win = lambda a, b, c: s.loc[(s.index >= t_rep + a) & (s.index <= t_rep + b), c]
    h_pre = circ_mean(win(-14, -8, "Heading"))
    h_post = circ_mean(win(15, 25, "Heading"))
    if np.isnan(h_pre) or np.isnan(h_post):
        return None
    turn = adiff(h_pre, h_post)
    sign = 1 if turn > 0 else -1

    # Middle of the turn: first second the heading has turned half-way, near the reported time
    near = s.loc[(s.index >= t_rep - 12) & (s.index <= t_rep + 12)]
    rel = sign * unwrap_from(h_pre, near.Heading.values)
    past = np.flatnonzero(rel >= abs(turn) / 2)
    if not len(past):
        return None
    t0 = float(near.index[past[0]])

    seg = s.loc[(s.index >= t0 - PRE) & (s.index <= t0 + POST)]
    x = np.round(seg.index.values - t0).astype(int)
    sog = seg.SOG.values.astype(float)
    hdg = sign * unwrap_from(h_pre, seg.Heading.values)
    heel = seg.Heel.abs().values.astype(float) if "Heel" in seg else np.full(len(x), np.nan)
    at = lambda a, b: (x >= a) & (x <= b)

    entry = float(np.nanmean(sog[at(*ENTRY)]))
    look = np.flatnonzero(at(-3, 15))
    if not len(look) or np.isnan(entry):
        return None
    i_min = look[np.nanargmin(sog[look])]
    min_sog, t_min = float(sog[i_min]), int(x[i_min])
    rec = np.flatnonzero((x > t_min) & (sog >= RECOVERY_PCT * entry))
    t_rec = int(x[rec[0]]) if len(rec) else None

    # Time lost: seconds at entry speed given away until recovered (or the end of the window)
    end = t_rec if t_rec is not None else POST
    secs_lost = float(np.nansum(np.clip(1 - sog[at(-5, end)] / entry, 0, None)))

    a = abs(turn)
    reached = lambda f: x[np.flatnonzero(hdg >= f * a)[0]] if (hdg >= f * a).any() else None
    t10, t90 = reached(0.1), reached(0.9)
    after = at(0, 15)
    has_cog = "COG" in s
    cog_pre = circ_mean(win(-14, -8, "COG")) if has_cog else np.nan
    cog_post = circ_mean(win(15, 25, "COG")) if has_cog else np.nan
    cog_angle = None if np.isnan(cog_pre + cog_post) else abs(adiff(cog_pre, cog_post))
    r1 = lambda v: None if v is None or np.isnan(v) else round(float(v), 1)
    return {
        "kind": kind,
        # Heading swings clockwise onto port tack and anticlockwise onto port gybe
        "onto": ("Port" if turn > 0 else "Stbd") if kind == "tack" else ("Port" if turn < 0 else "Stbd"),
        "t0": round(t0),
        "clock": f"{int(t0 // 60)}:{int(t0 % 60):02d}",
        "entry": round(entry, 2),
        "min": round(min_sog, 2),
        "loss_pct": round(100 * (entry - min_sog) / entry, 1),
        "t_min": t_min,
        "t_rec": t_rec,
        "secs_lost": round(secs_lost, 1),
        "m_lost": round(secs_lost * entry * KT, 1),
        "hdg_angle": round(a, 1),
        "cog_angle": r1(cog_angle),
        "turn_s": int(t90 - t10) if t10 is not None and t90 is not None else None,
        "overshoot": r1(np.nanmax(hdg[after]) - a) if after.any() else None,
        "heel_pre": r1(np.nanmean(heel[at(-10, -4)])),
        "heel_at10": r1(np.nanmean(heel[at(8, 12)])),
        "heel_post": r1(np.nanmean(heel[at(15, 25)])),
        "x": x.tolist(),
        "sog": [None if np.isnan(v) else round(float(v), 2) for v in sog],
        "hdg": [None if np.isnan(v) else round(float(v), 1) for v in hdg],
    }


def comparable(r: dict, kind: str) -> str | None:
    """Why a maneuver is left out of the comparison, or None to keep it."""
    k = KINDS[kind]
    if r["entry"] < MIN_ENTRY_KT:
        return "stall"
    heeled = lambda h: h is not None and h >= UPWIND_HEEL
    if heeled(r["heel_pre"]) != k["heeled"] or heeled(r["heel_post"]) != k["heeled"]:
        return "mark"
    lo, hi = k["angle"]
    if not lo <= r["hdg_angle"] <= hi:
        return "mark"
    return None


def race_overlay(df: pd.DataFrame, maneuvers: list[dict], boat: str, race: str) -> dict:
    """overlay.json for one race: every comparable tack and gybe, plus counts of what was left out.

    df needs tg (seconds from the gun), SOG, Heading and ideally COG and Heel; maneuvers are
    analyze.py's rows (time_s, kind, note)."""
    s = df.dropna(subset=["tg"]).drop_duplicates("tg").set_index("tg")
    if "Heading" not in s or s.Heading.isna().all():
        if "COG" not in s:
            return {"boat": boat, "race": race}
        s = s.assign(Heading=s.COG)  # no compass: GPS course is the next best heading
    out = {"boat": boat, "race": race}
    for kind, k in KINDS.items():
        keep, skipped = [], {"double": 0, "mark": 0, "stall": 0}
        for m in maneuvers:
            if m.get("kind") != k["event"] or m.get("time_s") is None:
                continue
            if m.get("note"):
                skipped["double"] += 1  # doubles: the two maneuvers' losses overlap
                continue
            r = record(s, float(m["time_s"]), kind)
            why = "mark" if r is None else comparable(r, kind)
            if why:
                skipped[why] += 1
                continue
            r.update(boat=boat, race=race, id=f"{boat} · {race} · {r['clock']}")
            keep.append(r)
        out[kind + "s"] = keep
        out[kind + "s_skipped"] = skipped
    return out


# --- page building -----------------------------------------------------------------------------

WORDS = {
    "tack": {"one": "tack", "many": "tacks", "Many": "Tacks", "mid": "head to wind",
             "angle": "tacking angle", "Angle": "Tacking angle"},
    "gybe": {"one": "gybe", "many": "gybes", "Many": "Gybes", "mid": "dead downwind",
             "angle": "gybing angle", "Angle": "Gybing angle"},
}


def collect(overlays: list[dict], kind: str, boats: list[str] | None = None) -> tuple[list[dict], dict]:
    items, skipped = [], {"double": 0, "mark": 0, "stall": 0}
    for o in overlays:
        if boats and o["boat"] not in boats:
            continue
        items += o.get(kind + "s", [])
        for k, v in o.get(kind + "s_skipped", {}).items():
            skipped[k] = skipped.get(k, 0) + v
    return items, skipped


def skipped_note(skipped: dict, kind: str) -> str:
    w = WORDS[kind]
    where = (
        "a mark rounding (a turn over 110°, or a tack into or out of a run)"
        if kind == "tack"
        else "a mark rounding (heeled like a beat on either side, e.g. a gybe-set or a gybe into the leeward mark)"
    )
    return (
        f"Left out: {skipped['double']} {w['many']} in doubles (under 30 s apart, so their losses overlap), "
        f"{skipped['mark']} at {where}, and {skipped['stall']} from a stall (entry under {MIN_ENTRY_KT:.0f} kt)."
    )


def intro(kind: str) -> str:
    w = WORDS[kind]
    return (
        f"Every comparable {w['one']}, lined up at {w['mid']} (0 s): the moment the heading is halfway "
        f"through the turn. The best 10% by <b>time lost</b> are drawn in the boat's colour and the rest in "
        f"grey. Time lost is the seconds at entry speed the {w['one']} gave away until speed was back to "
        f"95% of entry. Click a line, dot or table row to follow one {w['one']}."
    )


def fragment(kind: str, extra_class: str = "") -> str:
    """The component's markup; maneuver_overlay.js fills it in."""
    w = WORDS[kind]
    angle_note = (
        "Over the ground is from GPS course and doesn't depend on the compass. The gap between it and the "
        "compass angle is leeway through the tack (both tacks' leeway added together)."
        if kind == "tack"
        else "Over the ground is from GPS course and doesn't depend on the compass. Downwind, a wider angle "
        "usually means sailing hotter on one or both gybes."
    )
    return f"""<div class="tk {extra_class}">
  <div class="tk-filters" role="toolbar">
    <div><label>Boat</label><span class="tk-seg" data-r="f-boat"></span></div>
    <div><label>Race</label><span class="tk-seg" data-r="f-race"></span></div>
    <div><label>Onto</label><span class="tk-seg" data-r="f-onto"></span></div>
    <div><label>Highlight best</label><span class="tk-seg" data-r="f-pct"></span></div>
  </div>
  <p class="tk-note" data-r="thin" hidden></p>
  <div class="tk-tiles" data-r="tiles"></div>
  <div class="tk-card">
    <h2>Speed through the {w['one']}</h2>
    <div class="tk-legend" data-r="leg-sog"></div>
    <div class="tk-chart" data-r="sog"></div>
    <p class="tk-note">SOG, 1 Hz. Entry speed is the average from −10 to −4 s. The dashed line is the median of
      every {w['one']} in view. Speed over the ground includes current and any puff or lull, so read single
      {w['many']} with care.</p>
  </div>
  <div class="tk-grid2">
    <div class="tk-card">
      <h2>Heading through the {w['one']}</h2>
      <div class="tk-legend" data-r="leg-hdg"></div>
      <div class="tk-chart" data-r="hdg"></div>
      <p class="tk-note">Degrees turned from the settled heading before the {w['one']} (−14 to −8 s). Where a line
        settles is the {w['angle']}; a hump above it is overshoot past the new course.</p>
    </div>
    <div class="tk-card">
      <h2>{w['Angle']} vs time lost</h2>
      <div class="tk-seg" data-r="f-angle" style="margin:6px 0"></div>
      <div class="tk-legend" data-r="leg-ang"></div>
      <div class="tk-chart" data-r="ang"></div>
      <p class="tk-note">{angle_note}</p>
    </div>
  </div>
  <div class="tk-card tk-well">
    <h2>What the best {w['many']} did</h2>
    <div data-r="well"></div>
    <div class="tk-tablewrap" style="margin-top:10px"><table class="tk-cmp" data-r="cmp"></table></div>
  </div>
  <div class="tk-card">
    <h2>All {w['many']}, best first</h2>
    <div class="tk-tablewrap"><table class="tk-list" data-r="tbl"></table></div>
    <p class="tk-note" data-r="excl"></p>
  </div>
</div>"""


def payload(items, boats, note, kind) -> str:
    data = {"kind": kind, "words": WORDS[kind], "boats": boats, "items": items, "note": note}
    return json.dumps(data, separators=(",", ":")).replace("</", "<\\/")


def assets() -> tuple[str, str]:
    return (HERE / "maneuver_overlay.css").read_text(), (HERE / "maneuver_overlay.js").read_text()


def plotly_tag(cdn: bool) -> str:
    if cdn:
        return f'<script src="{PLOTLY_CDN}"></script>'
    return "<script>" + (HERE / "vendor" / "plotly-basic.min.js").read_text() + "</script>"


def boat_order(boats) -> list[str]:
    """Fixed order, so each boat keeps its colour on every page (Mojo first when present)."""
    return sorted(set(boats), key=lambda b: (b != "Mojo", b))


def report_pages(overlays: list[dict], boats: list[str]) -> tuple[list[tuple[str, str, str]], str]:
    """For html_report.py: [(page id, title, body html)] and the scripts to put after Plotly."""
    css, js = assets()
    pages, init = [], []
    for kind in KINDS:
        items, skipped = collect(overlays, kind)
        if not items:
            continue
        pid = kind + "s"
        pages.append((pid, WORDS[kind]["Many"], intro(kind), fragment(kind, "tk-embedded")))
        init.append(
            f'TackOverlay(document.querySelector("#{pid} .tk"), '
            f"{payload(items, boats, skipped_note(skipped, kind), kind)});"
        )
    if not pages:
        return [], ""
    return pages, f"<style>{css}</style><script>{js}</script><script>{''.join(init)}</script>"


def standalone(overlays: list[dict], kind: str, title: str, cdn: bool) -> str:
    css, js = assets()
    items, skipped = collect(overlays, kind)
    boats = boat_order(o["boat"] for o in overlays)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
{css}
body {{ margin: 0; background: #f4f3f0; font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) body {{ background: #121211; }} }}
:root[data-theme="dark"] body {{ background: #121211; }}
main {{ max-width: 1180px; margin: 0 auto; padding: 20px 16px 48px; }}
main > h1 {{ font-size: 22px; margin: 0 0 4px; }}
main > .tk-intro {{ margin: 0 0 8px; max-width: 820px; color: var(--tk-ink2); }}
</style>
{plotly_tag(cdn)}
</head><body><main class="tk">
<h1>{html.escape(title)}</h1>
<p class="tk-intro">{intro(kind)}</p>
{fragment(kind)}
</main>
<script>{js}</script>
<script>TackOverlay(document.querySelector("main > .tk"), {payload(items, boats, skipped_note(skipped, kind), kind)});</script>
</body></html>"""


def load_overlays(reports: Path) -> list[dict]:
    """Every <reports>/<boat>/<race>/overlay.json (or <reports>/<race>/overlay.json for one boat)."""
    return [json.loads(p.read_text()) for p in sorted(reports.glob("**/overlay.json"))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reports", type=Path, required=True, help="analyze.py output: one boat, or a folder of boats")
    ap.add_argument("--kind", choices=list(KINDS), required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--cdn", action="store_true", help="load Plotly online instead of inlining it")
    a = ap.parse_args()
    overlays = load_overlays(a.reports)
    if not overlays:
        raise SystemExit(f"no overlay.json under {a.reports}: re-run analyze.py")
    title = a.title or WORDS[a.kind]["Many"]
    a.out.write_text(standalone(overlays, a.kind, title, a.cdn))
    n = len(collect(overlays, a.kind)[0])
    print(f"{a.out} ({n} {WORDS[a.kind]['many']})")


if __name__ == "__main__":
    main()
