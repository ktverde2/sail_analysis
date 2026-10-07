# sail_analysis
Build tools designed to help analyze sailing data.

- `skills/sailing-coach/`: the source of the sailing-coach skill (Njord data to a coaching debrief and HTML reports). `skills/sailing-coach.skill` is the packaged version to install: upload it in Claude's Skills settings.
- `sept-odw-2026/`: Sept ODW 2026 (26–27 Sep, San Diego) data, reports and debriefs for the three tracked boats, Mojo, 969 and 1216. Open `report/Sept-ODW-2026_fleet.html` or `report/<boat>/Sept-ODW-2026_<boat>_report.html` in a browser; they work offline. `rebuild.sh` regenerates them (see `sept-odw-2026/README.md`).

## The reports
Each boat report opens on a Quick look scorecard, then the Debrief and a Deep dive (Starts, Tacks and Gybes, Upwind and Downwind Analysis, Roundings, Current, Race by race) in dropdown sections, with Show me links from the debrief to the races, tap-to-explain terms and an Across regattas page. The fleet report has the same reading aids plus race and start replays, Side by side and Wind and current.

To share a report, upload the HTML to Google Drive and email the link. Each report keeps all its data in one block; when the race data and the tack overlay data were separate, Drive's virus scan flagged the boat reports (a false positive).
