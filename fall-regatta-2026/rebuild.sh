#!/usr/bin/env bash
# Rebuild the Fall Regatta 2026 J/24 reports from the Velocitek logs and the checked course.
#   vtk/<boat>/*.vtk   the boats' Velocitek logs
#   regatta.json       the course for each race, checked and corrected on the course map
#   report/            about-the-data.md and noaa.json are kept; everything else is regenerated.
#                      The reports are data only: no coaching (--data-only).
set -euo pipefail
cd "$(dirname "$0")"
S=${SAILING_COACH_SCRIPTS:-../skills/sailing-coach/scripts}
export SAIL_CLASS=J24   # J/24 lengths (7.32 m), mark zone and heel
TITLE="Fall Regatta 2026"
BOATS=(Kud WR)

rm -rf data
# Only Kud pinged the start line; WR borrows its pings (the course's start line wins anyway)
python3 "$S/vkx/vkx_report.py" --regatta regatta.json --boat Kud --data-dir data vtk/Kud/*.vtk
python3 "$S/vkx/vkx_report.py" --regatta regatta.json --boat WR --data-dir data vtk/WR/*.vtk \
  --line-from vtk/Kud/*.vtk
python3 "$S/course.py" check --data data --regatta regatta.json

# Analyse every boat before building any report: each report's Current page uses all boats' data
for b in "${BOATS[@]}"; do python3 "$S/analyze.py" data/$b/race*.csv --out report/$b; done
[ -f report/noaa.json ] || python3 "$S/noaa.py" --reports report

for b in "${BOATS[@]}"; do
  python3 "$S/html_report.py" report/$b --data-only --notes report/about-the-data.md \
    --title "$b · $TITLE" --out report/$b/Fall-Regatta-2026_${b}_report.html
done
python3 "$S/fleet.py" --data data --reports report --out report --title "$TITLE · J/24s" \
  --data-only --notes report/about-the-data.md --html
for k in tack gybe; do
  python3 "$S/maneuver_overlay.py" --reports report --kind $k --out report/${k}s.html --title "$TITLE · ${k^}s"
done
