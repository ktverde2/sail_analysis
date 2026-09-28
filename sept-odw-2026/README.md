# Sept ODW 2026 (Sat 26 Sep, 3 races): Mojo and 969

These are the two boats tracked in Njord. Neither logs wind; speed is SOG.

Reports, named `<regatta>_<boat>_<kind>`:

| File | What |
|---|---|
| `report/Sept-ODW-2026_fleet.html` | Head-to-head: race replay, side-by-side stretches, fleet debrief |
| `report/Sept-ODW-2026_tacks.html` | Every beat-to-beat tack for both boats, lined up at head to wind. Shows SOG and heading overlays and tacking angle against time lost, with the best 10% highlighted and what they did differently |
| `report/Sept-ODW-2026_gybes.html` | The same for gybes. Only 3 were comparable this day, all Mojo's in Race 3 |
| `report/Sept-ODW-2026_fleet-debrief.md` | Fleet debrief (written) |
| `report/Sept-ODW-2026_fleet.md` / `.json` | Fleet comparison tables (generated) |
| `report/<boat>/Sept-ODW-2026_<boat>_report.html` | Full report for one boat. Its Deep dive has **Tacks** and **Gybes** pages with that boat's maneuvers (969 has no comparable gybes, so no Gybes page) |
| `report/<boat>/Sept-ODW-2026_<boat>_debrief.md` | Coaching debrief for one boat (written) |
| `report/<boat>/Sept-ODW-2026_<boat>_executive-summary.md` | One line for each race, with flags |
| `report/<boat>/Sept-ODW-2026_<boat>_event-summary.md` | Table of all races |

`report/<boat>/raceN/` holds per-race detail and plots. `data/<boat>/raceN.csv` is Njord `get_data` at 1 Hz, with `-race.json` (the course) and `-raceInfo.json` (Mojo only). These keep the generic names because the scripts look for them.

Open the `.html` files in a browser. They work offline.

To regenerate everything with the same names after editing a debrief: `./rebuild.sh`. It uses the skill copy in `../skills/sailing-coach`; set `SAILING_COACH_SCRIPTS` to use another.
