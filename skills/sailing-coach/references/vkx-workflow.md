# From Vakaros .vkx files (no Njord)

When Kevin sends Vakaros Atlas logs instead of a Njord event, `scripts/vkx/` reads them into the same per-race files Njord gives (`raceN.csv`, `raceN-race.json`, `raceN-raceInfo.json`), so everything from `analyze.py` on is unchanged. A `.vkx` has the boat's GPS and motion sensor (position, SOG, COG, heading, heel, trim), the race timer (gun and reset) and the start-line ends the device pinged. It has **no wind, no marks and no finish line**: the marks come from the tracks, and Kevin confirms them on a map before any analysis.

1. **Organise the files**, one folder per boat named exactly for the boat: `<dir>/vkx/<boat>/*.vkx`. The file is named after the device's name in the Vakaros app, not the boat ("San Diego 10-11-2026.vkx" was 1018's), so ask which boat a file is from if it isn't clear. A log can run for days; only the races are used.
2. **Parse each boat without a course:**
   ```bash
   python scripts/vkx/vkx_report.py --boat <boat> --event "<Event>" --data-dir <dir>/raw <dir>/vkx/<boat>/*.vkx
   ```
   Each race is a gun on the device's race timer, running to the next timer reset. Every boat should show the same guns; a boat missing one didn't start its timer for that race, so say so. Directions use San Diego's 10.7° E variation (`--variation` elsewhere).
3. **Estimate the course:**
   ```bash
   python scripts/course.py detect --data <dir>/raw --out <dir>/regatta-proposed.json --event "<Event>"
   ```
   It finds the course axis from the boats' tacks, splits every track into beats and runs, and puts each mark at the boats' rounding points. A leeward mark becomes a gate when boats rounded points more than 25 m apart across the course. The finish goes square to the course where the first boat stopped racing. It prints notes for each race and every boat's closest pass at each mark. It handles windward-leeward courses only; anything else needs its marks placed on the map. Marks usually land within 10 m. The finish is the weakest guess (20–70 m off on Oct 11 2026), and a gate where every boat rounded the same end shows as one mark; the notes say when another race had a gate there.
4. **Kevin checks it on a map:**
   ```bash
   python scripts/course.py page --data <dir>/raw --regatta <dir>/regatta-proposed.json --out <dir>/course-check.html \
     --title "<Event> course check · <boats>" --doc courses/<event-slug>
   ```
   One self-contained page: every boat's track course up (or north up), zoom and pan, a time slider, and the course on top. Kevin drags marks and line ends, switches a mark to a gate, adds, removes or reorders marks, or puts the finish on the start line. Under each mark it lists how close every boat passed it (and which gate end), and under the finish when each boat crossed, so a wrong mark shows at once. The start line is the boats' own pings and can't be moved.
   - **Where the Artifact tool exists** (a claude.ai project, Claude Code): publish the page as an artifact with the `db` capability and send Kevin the link. Save writes the course to the doc named by `--doc`. Read it back with ArtifactData `get` (collection `courses`, doc id `<event-slug>`). It's a complete regatta file (`event`, `timezone`, `races`), so write it to `<dir>/regatta.json`. To re-check after a change, or when another boat is added, republish to the same URL with the same `--doc`: his saved course loads over the estimate.
   - **Anywhere else**, send the HTML file. Save then downloads `regatta-<event>.json`, which Kevin sends back.
   - Go round until Kevin says it's right. Nothing is analysed on an unconfirmed course.
5. **Check it, then parse with it:**
   ```bash
   python scripts/course.py check --data <dir>/raw --regatta <dir>/regatta.json
   python scripts/vkx/vkx_report.py --regatta <dir>/regatta.json --boat <boat> --data-dir <dir>/data <dir>/vkx/<boat>/*.vkx
   ```
   `check` lists each boat's pass at every mark and its finish crossing. A boat more than about 30 m from a mark, or one that never crosses the finish, means the course is wrong: show Kevin before going on. With a finish line in the course, `vkx_report.py` ends each race 30 s after that boat crosses it (`--finish-pad`), so each boat's race ends at its own finish, as in Njord. An `endTime` in the regatta file overrides it for every boat.
6. **Wind:** there's no wind channel, so the target comparison needs `analyze.py --tws <lo>-<hi>` (SKILL.md, Workflow step 5). Get the race-time wind from the nearest weather stations. For San Diego Bay, the 5-minute KSAN (airport) and hourly KNZY (North Island) reports from the Iowa Environmental Mesonet ASOS archive sit right beside the course. Otherwise use Kevin's estimate. Say where it came from in the Data notes. Wind *direction* still comes from the boats' tacks, as with Njord.
7. **Analyse as usual** from SKILL.md Workflow step 4's `analyze.py` on `<dir>/data/<boat>/race*.csv` (and `fleet.py` over `<dir>/data` for two or more boats). The reports' subtitles say "from Vakaros data" on their own, from the `"source"` the parser writes into `raceN-race.json`. In the debrief's Data notes, say the source was the `.vkx` file and that the marks were estimated from the tracks and checked by Kevin.

`scripts/vkx/` is a copy of github.com/ktverde2/vkx-parser (Apache-2.0, `scripts/vkx/LICENSE`; the file format is in `vkx_format_spec.md`), changed to end races at each boat's finish and to tag the source.
