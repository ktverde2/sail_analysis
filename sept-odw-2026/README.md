# Sept ODW 2026 (Sat 26 Sep, 3 races): Mojo and 969

These are the two boats tracked in Njord. Neither logs wind; speed is SOG.

Reports, named `<regatta>_<boat>_<kind>`:

| File | What |
|---|---|
| `report/Sept-ODW-2026_fleet.html` | Head-to-head: race replay, side-by-side stretches, fleet debrief |
| `report/Sept-ODW-2026_fleet-debrief.md` | Fleet debrief (written) |
| `report/Sept-ODW-2026_fleet.md` / `.json` | Fleet comparison tables (generated) |
| `report/<boat>/Sept-ODW-2026_<boat>_report.html` | Full report for one boat |
| `report/<boat>/Sept-ODW-2026_<boat>_debrief.md` | Coaching debrief for one boat (written) |
| `report/<boat>/Sept-ODW-2026_<boat>_executive-summary.md` | One line for each race, with flags |
| `report/<boat>/Sept-ODW-2026_<boat>_event-summary.md` | Table of all races |

`report/<boat>/raceN/` holds per-race detail and plots. `data/<boat>/raceN.csv` is Njord `get_data` at 1 Hz, with `-race.json` (the course) and `-raceInfo.json` (Mojo only). These keep the generic names because the scripts look for them.

Open the `.html` files in a browser. They work offline.

To regenerate everything with the same names after editing a debrief: `SAILING_COACH_SCRIPTS=<skill>/scripts ./rebuild.sh`
