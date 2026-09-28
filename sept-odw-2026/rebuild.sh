#!/usr/bin/env bash
# Re-run the sailing-coach pipeline on data/ and name the outputs by regatta and boat.
# The skill scripts read and write fixed names (debrief.md, executive.md, event.md,
# fleet.md, ...), so this stages the named files under those names, runs, and renames back.
set -euo pipefail
cd "$(dirname "$0")"
E=Sept-ODW-2026
TITLE="Sept ODW · Sep 26"
# The sailing-coach skill's scripts: this repo's copy (skills/sailing-coach) unless overridden
S=${SAILING_COACH_SCRIPTS:-../skills/sailing-coach/scripts}
BOATS=(Mojo 969)

# Analyse every boat before building any report: each report's Current page uses all boats' data
for b in "${BOATS[@]}"; do
  python3 "$S/analyze.py" data/$b/race*.csv --out report/$b
done

for b in "${BOATS[@]}"; do
  d=report/$b
  mv $d/executive.md $d/${E}_${b}_executive-summary.md
  mv $d/event.md     $d/${E}_${b}_event-summary.md
  # html_report.py and fleet.py look for these names
  cp $d/${E}_${b}_executive-summary.md $d/executive.md
  cp $d/${E}_${b}_event-summary.md     $d/event.md
  cp $d/${E}_${b}_debrief.md           $d/debrief.md
  python3 "$S/html_report.py" $d --debrief $d/debrief.md \
    --title "$b · $TITLE" --out $d/${E}_${b}_report.html
done

python3 "$S/fleet.py" --data data --reports report --out report --title "$TITLE · Fleet" \
  --debrief report/${E}_fleet-debrief.md --html
mv report/fleet.html report/${E}_fleet.html
mv report/fleet.md   report/${E}_fleet.md
mv report/fleet.json report/${E}_fleet.json

# Tacks and gybes for both boats on one page each (each boat report has its own in the Deep dive)
for k in tack gybe; do
  python3 "$S/maneuver_overlay.py" --reports report --kind $k \
    --out report/${E}_${k}s.html --title "Sept ODW 2026 · ${k^}s"
done

for b in "${BOATS[@]}"; do rm report/$b/{executive,event,debrief}.md; done
