#!/usr/bin/env python3
"""Interactive tack overlay: SOG, heading and tacking angle through every tack.

Reads the sailing-coach layout:
  <data>/<boat>/raceN.csv            Njord get_data export at 1 Hz (SOG, COG, Heading, Heel)
  <data>/<boat>/raceN-race.json      race metadata (startTime = gun)
  <reports>/<boat>/raceN/maneuvers.csv   tacks found by analyze.py

Each tack is re-anchored the same way for every boat: t = 0 is the moment the heading
passes halfway between the settled heading before and after the tack (head to wind).
The ranking and the "what the best tacks did" panel are computed in the page, so they
follow the boat/race filters.

  python tools/tack_overlay.py --data <dir>/data --reports <dir>/report \
      --out <dir>/report/<name>_tacks.html --title "Sept ODW 2026 · Tacks" \
      --embed Mojo=<dir>/report/Mojo/<name>_Mojo_report.html   # Tacks page in that boat's Deep dive
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PRE, POST = 15, 40  # seconds shown either side of head to wind
ENTRY = (-10, -4)  # entry speed window, same as analyze.py
RECOVERY_PCT = 0.95
KT = 0.514444
HERE = Path(__file__).resolve().parent


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
    rel = adiff(ref, np.asarray(series, dtype=float))
    out = rel.copy()
    for i in range(1, len(out)):
        if np.isnan(out[i]) or np.isnan(out[i - 1]):
            continue
        out[i] = out[i - 1] + adiff(out[i - 1], out[i])
    return out


def tack_record(df: pd.DataFrame, t_rep: float, meta: dict) -> dict | None:
    s = df.set_index("tg")
    win = lambda a, b, c: s.loc[(s.index >= t_rep + a) & (s.index <= t_rep + b), c]

    h_pre = circ_mean(win(-14, -8, "Heading"))
    h_post = circ_mean(win(15, 25, "Heading"))
    if np.isnan(h_pre) or np.isnan(h_post):
        return None
    turn = adiff(h_pre, h_post)
    sign = 1 if turn > 0 else -1

    # Head to wind: first second the heading has turned half-way, near the reported time.
    near = s.loc[(s.index >= t_rep - 12) & (s.index <= t_rep + 12)]
    rel = sign * unwrap_from(h_pre, near.Heading.values)
    past = np.where(rel >= abs(turn) / 2)[0]
    if not len(past):
        return None
    t0 = float(near.index[past[0]])

    seg = s.loc[(s.index >= t0 - PRE) & (s.index <= t0 + POST)]
    x = (seg.index.values - t0).round().astype(int)
    sog = seg.SOG.values.astype(float)
    hdg = sign * unwrap_from(h_pre, seg.Heading.values)
    heel = seg.Heel.abs().values.astype(float) if "Heel" in seg else np.full(len(x), np.nan)

    at = lambda a, b: (x >= a) & (x <= b)
    entry = float(np.nanmean(sog[at(*ENTRY)]))
    look = at(-3, 15)
    i_min = np.flatnonzero(look)[np.nanargmin(sog[look])]
    min_sog, t_min = float(sog[i_min]), int(x[i_min])
    rec = np.flatnonzero((x > t_min) & (sog >= RECOVERY_PCT * entry))
    t_rec = int(x[rec[0]]) if len(rec) else None

    # Time lost to the tack: seconds at entry speed given away until recovered (or +40 s).
    end = t_rec if t_rec is not None else POST
    span = at(-5, end)
    deficit = np.clip(1 - sog[span] / entry, 0, None)
    secs_lost = float(np.nansum(deficit))

    # Turn: 10% -> 90% of the heading change, and the overshoot past the new heading.
    a = abs(turn)
    t10 = x[np.flatnonzero(hdg >= 0.1 * a)[0]] if (hdg >= 0.1 * a).any() else None
    t90 = x[np.flatnonzero(hdg >= 0.9 * a)[0]] if (hdg >= 0.9 * a).any() else None
    turn_s = int(t90 - t10) if t10 is not None and t90 is not None else None
    after_turn = at(0, 15)
    overshoot = float(np.nanmax(hdg[after_turn]) - a) if after_turn.any() else None

    cog_pre = circ_mean(win(-14, -8, "COG"))
    cog_post = circ_mean(win(15, 25, "COG"))
    cog_angle = abs(adiff(cog_pre, cog_post)) if not np.isnan(cog_pre + cog_post) else None

    heel_pre = float(np.nanmean(heel[at(-10, -4)]))
    heel_post = float(np.nanmean(heel[at(15, 25)]))
    heel_at10 = float(np.nanmean(heel[at(8, 12)]))

    return {
        **meta,
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
        "cog_angle": round(cog_angle, 1) if cog_angle is not None else None,
        "turn_s": turn_s,
        "overshoot": round(overshoot, 1) if overshoot is not None else None,
        "heel_pre": round(heel_pre, 1),
        "heel_at10": round(heel_at10, 1),
        "heel_post": round(heel_post, 1),
        "x": x.tolist(),
        "sog": [None if np.isnan(v) else round(float(v), 2) for v in sog],
        "hdg": [None if np.isnan(v) else round(float(v), 1) for v in hdg],
    }


def load(data: Path, reports: Path) -> tuple[list[dict], dict]:
    tacks, skipped = [], {}
    for bdir in sorted(p for p in data.iterdir() if p.is_dir()):
        boat = bdir.name
        skip = skipped.setdefault(boat, {"double": 0, "mark or stall": 0})
        for csv in sorted(bdir.glob("race*.csv")):
            stem = csv.stem
            meta = json.loads(csv.with_name(f"{stem}-race.json").read_text())
            gun = pd.Timestamp(meta["startTime"]).timestamp()
            df = pd.read_csv(csv)
            df["tg"] = (df.SecondsSince1970 - gun).round()
            man = pd.read_csv(reports / boat / stem / "maneuvers.csv")
            for _, m in man[man.kind == "Tack"].iterrows():
                if isinstance(m.note, str) and m.note:
                    skip["double"] += 1
                    continue  # doubles: the two tacks' losses overlap
                rec = tack_record(
                    df,
                    float(m.time_s),
                    {
                        "boat": boat,
                        "race": meta.get("race", stem),
                        "leg": None if pd.isna(m.leg) else int(m.leg),
                        "onto": m.onto,
                    },
                )
                # Beat-to-beat tacks only: a tack from a stall (start, traffic) or into or out of
                # a mark rounding (heel from a run, or a 100°+ turn into a bear-away) isn't a
                # tack to compare.
                if (
                    rec
                    and rec["entry"] >= 4.0
                    and rec["heel_pre"] >= 10
                    and rec["heel_post"] >= 10
                    and rec["hdg_angle"] <= 110
                ):
                    rec["id"] = f"{boat} · {rec['race']} · {rec['clock']}"
                    tacks.append(rec)
                else:
                    skip["mark or stall"] += 1
    return tacks, skipped


def skipped_note(skipped: dict, boats: list[str]) -> str:
    d = sum(skipped[b]["double"] for b in boats)
    m = sum(skipped[b]["mark or stall"] for b in boats)
    return (
        f"Left out: {d} tacks in doubles (under 30 s apart, so their losses overlap) and {m} tacks at a "
        "mark rounding or from a stall (entry under 4 kt, heel under 10° on either side, or a turn over 110°)."
    )


INTRO = (
    "Every beat-to-beat tack, lined up at head to wind (0 s): the moment the heading is halfway through "
    "the turn. The best {pct} by <b>time lost</b> are drawn in the boat's colour and the rest in grey. "
    "Time lost is the seconds at entry speed the tack gave away until speed was back to 95% of entry. "
    "Click a line, dot or table row to follow one tack."
)


def fragment(extra_class: str = "") -> str:
    """The component's markup; tack_overlay.js fills it in."""
    return f"""<div class="tk {extra_class}">
  <div class="tk-filters" role="toolbar">
    <div><label>Boat</label><span class="tk-seg" data-r="f-boat"></span></div>
    <div><label>Race</label><span class="tk-seg" data-r="f-race"></span></div>
    <div><label>Onto</label><span class="tk-seg" data-r="f-onto"></span></div>
    <div><label>Highlight best</label><span class="tk-seg" data-r="f-pct"></span></div>
  </div>
  <div class="tk-tiles" data-r="tiles"></div>
  <div class="tk-card">
    <h2>Speed through the tack</h2>
    <div class="tk-legend" data-r="leg-sog"></div>
    <div class="tk-chart" data-r="sog"></div>
    <p class="tk-note">SOG, 1 Hz. Entry speed is the average from −10 to −4 s. The dashed line is the median of
      every tack in view. Speed over the ground includes current and any puff or lull, so read single tacks with care.</p>
  </div>
  <div class="tk-grid2">
    <div class="tk-card">
      <h2>Heading through the tack</h2>
      <div class="tk-legend" data-r="leg-hdg"></div>
      <div class="tk-chart" data-r="hdg"></div>
      <p class="tk-note">Degrees turned from the settled heading before the tack (−14 to −8 s). Where a line
        settles is the tacking angle; a hump above it is overshoot past the new course.</p>
    </div>
    <div class="tk-card">
      <h2>Tacking angle vs time lost</h2>
      <div class="tk-seg" data-r="f-angle" style="margin:6px 0"></div>
      <div class="tk-legend" data-r="leg-ang"></div>
      <div class="tk-chart" data-r="ang"></div>
      <p class="tk-note">Over the ground is from GPS course and doesn't depend on the compass. The gap between it
        and the compass angle is leeway through the tack (both tacks' leeway added together).</p>
    </div>
  </div>
  <div class="tk-card tk-well">
    <h2>What the best tacks did</h2>
    <div data-r="well"></div>
    <div class="tk-tablewrap" style="margin-top:10px"><table class="tk-cmp" data-r="cmp"></table></div>
  </div>
  <div class="tk-card">
    <h2>All tacks, best first</h2>
    <div class="tk-tablewrap"><table class="tk-list" data-r="tbl"></table></div>
    <p class="tk-note" data-r="excl"></p>
  </div>
</div>"""


def payload(tacks, boats, note) -> str:
    return json.dumps({"boats": boats, "tacks": tacks, "note": note}, separators=(",", ":")).replace("</", "<\\/")


def assets() -> tuple[str, str]:
    return (HERE / "tack_overlay.css").read_text(), (HERE / "tack_overlay.js").read_text()


def plotly_tag(cdn: bool) -> str:
    if cdn:
        return f'<script src="{PLOTLY_CDN}"></script>'
    return "<script>" + (HERE / "vendor" / "plotly-basic.min.js").read_text() + "</script>"


PLOTLY_CDN = "https://cdn.jsdelivr.net/npm/plotly.js-basic-dist-min@2.35.3/plotly-basic.min.js"


def standalone(tacks, boats, note, title, cdn) -> str:
    css, js = assets()
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
main > .tk-intro {{ margin: 0 0 8px; max-width: 820px; }}
</style>
{plotly_tag(cdn)}
</head><body><main class="tk">
<h1>{html.escape(title)}</h1>
<p class="tk-intro" style="color:var(--tk-ink2)">{INTRO.format(pct="10%")}</p>
{fragment()}
</main>
<script>{js}</script>
<script>TackOverlay(document.querySelector("main > .tk"), {payload(tacks, boats, note)});</script>
</body></html>"""


MARK = re.compile(r"<!--tk:start-->.*?<!--tk:end-->", re.S)


def embed(report: Path, tacks, boats, note, cdn) -> None:
    """Add a Tacks page to a sailing-coach boat report, in the Deep dive next to Maneuvers."""
    doc = MARK.sub("", report.read_text())  # idempotent: drop a previous embed
    css, js = assets()
    page = (
        '<!--tk:start--><section class="page" id="tacks" data-level="3"><h1>Tacks</h1>'
        f'<p class="lede">{INTRO.format(pct="10%")}</p>{fragment("tk-embedded")}</section><!--tk:end-->'
    )
    # After the Maneuvers page: before whichever page section starts next
    start = doc.find('<section class="page" id="maneuvers"')
    if start < 0:
        sys.exit(f"{report}: no Maneuvers page to put the Tacks page next to")
    nxt = doc.find('<section class="page" id="', start + 1)
    at = nxt if nxt > 0 else doc.index("<footer>")
    doc = doc[:at] + page + doc[at:]
    link = '<a href="#maneuvers" data-level="3">Maneuvers</a>'
    doc = doc.replace(link, link + '<!--tk:start--><a href="#tacks" data-level="3">Tacks</a><!--tk:end-->', 1)
    has_plotly = "plotly.js (basic" in doc or "plotly-basic.min.js" in doc
    tail = (
        "<!--tk:start-->"
        + ("" if has_plotly else plotly_tag(cdn))
        + f"<style>{css}</style><script>{js}</script>"
        + f'<script>TackOverlay(document.querySelector("#tacks .tk"), {payload(tacks, boats, note)});</script>'
        + "<!--tk:end-->"
    )
    i = doc.rindex("</body>")
    report.write_text(doc[:i] + tail + doc[i:])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--reports", type=Path, required=True)
    ap.add_argument("--out", type=Path, help="standalone page with every boat")
    ap.add_argument("--title", default="Tacks")
    ap.add_argument(
        "--embed", action="append", default=[], metavar="BOAT=REPORT.html",
        help="add a Tacks page (that boat only) to its report's Deep dive; repeat per boat",
    )
    ap.add_argument("--cdn", action="store_true", help="load Plotly online instead of inlining it")
    a = ap.parse_args()

    tacks, skipped = load(a.data, a.reports)
    # Every boat keeps the same colour on every page
    boats = sorted(skipped, key=lambda b: (b != "Mojo", b))
    if a.out:
        a.out.write_text(standalone(tacks, boats, skipped_note(skipped, boats), a.title, a.cdn))
        print(f"{a.out} ({len(tacks)} tacks)")
    for spec in a.embed:
        boat, _, path = spec.partition("=")
        mine = [t for t in tacks if t["boat"] == boat]
        embed(Path(path), mine, boats, skipped_note(skipped, [boat]), a.cdn)
        print(f"{path}: Tacks page added ({len(mine)} tacks)")


if __name__ == "__main__":
    main()
