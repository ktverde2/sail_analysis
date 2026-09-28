#!/usr/bin/env bash
# Re-run the sailing-coach pipeline on data/ and name the outputs by regatta and boat.
# The skill scripts read and write fixed names (debrief.md, executive.md, event.md,
# fleet.md, ...), so this stages the named files under those names, runs, and renames back.
set -euo pipefail
cd "$(dirname "$0")"
E=Sept-ODW-2026
TITLE="Sept ODW · Sep 26"
S=${SAILING_COACH_SCRIPTS:?set SAILING_COACH_SCRIPTS to the sailing-coach skill scripts dir}
BOATS=(Mojo 969)

for b in "${BOATS[@]}"; do
  d=report/$b
  python3 "$S/analyze.py" data/$b/race*.csv --out $d
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

# Tacks: a standalone page for both boats, and a Tacks page in each boat report's Deep dive
EMBED=()
for b in "${BOATS[@]}"; do EMBED+=(--embed "$b=report/$b/${E}_${b}_report.html"); done
python3 ../tools/tack_overlay.py --data data --reports report \
  --out report/${E}_tacks.html --title "Sept ODW 2026 · Tacks" "${EMBED[@]}"

for b in "${BOATS[@]}"; do rm report/$b/{executive,event,debrief}.md; done
