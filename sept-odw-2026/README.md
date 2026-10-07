# Sept ODW 2026 (Sat 26 and Sun 27 Sep, 5 races): Mojo, 969 and 1216

These are the three boats tracked in Njord. None logs wind; speed is SOG. Races 1–3 were on Saturday. Races 4 and 5 are Sunday's (Njord calls them Race 1 and Race 2 on that day).

Reports, named `<regatta>_<boat>_<kind>`:

| File | What |
|---|---|
| `report/Sept-ODW-2026_fleet.html` | Head-to-head: race replay, side-by-side stretches, fleet debrief |
| `report/Sept-ODW-2026_tacks.html` | Every beat-to-beat tack for all three boats, lined up at head to wind. Shows SOG and heading overlays and tacking angle against time lost, with the best 10% highlighted and what they did differently |
| `report/Sept-ODW-2026_gybes.html` | The same for gybes |
| `report/Sept-ODW-2026_fleet-debrief.md` | Fleet debrief (written) |
| `report/Sept-ODW-2026_fleet.md` / `.json` | Fleet comparison tables (generated) |
| `report/<boat>/Sept-ODW-2026_<boat>_report.html` | Full report for one boat. Its Deep dive has a **Tacks and Gybes** page and an **Upwind and Downwind Analysis** page, each split into sections that open and close |
| `report/<boat>/Sept-ODW-2026_<boat>_debrief.md` | Coaching debrief for one boat (written) |
| `report/<boat>/Sept-ODW-2026_<boat>_executive-summary.md` | One line for each race, with flags |
| `report/<boat>/Sept-ODW-2026_<boat>_event-summary.md` | Table of all races |

`report/noaa.json` is NOAA's tide and current near the course for the race times (0.8 nmi east of Point Loma Light, and the San Diego tide gauge); delete it to refetch. `report/<boat>/raceN/` holds per-race detail and plots. `data/<boat>/raceN.csv` is Njord `get_data` at 1 Hz, with `-race.json` (the course) and `-raceInfo.json` (Njord's detected events; 969 has none for Saturday). Race 5 is fetched to 22:05 and its `endTime` set to 22:01:24, because Njord's race end (21:42:46) came before any boat finished. These keep the generic names because the scripts look for them.

Open the `.html` files in a browser. They work offline.

To regenerate everything with the same names after editing a debrief: `./rebuild.sh`. Mojo's Across regattas page uses MidWinters West and July ODW, analysed in the project folders `midwinters-west-2026/` and `odw-july-2026/`: run `HISTORY="/mnt/project-files/midwinters-west-2026/report /mnt/project-files/odw-july-2026/report" ./rebuild.sh` to keep it. It uses the skill copy in `../skills/sailing-coach`; set `SAILING_COACH_SCRIPTS` to use another.
