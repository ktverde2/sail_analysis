# Fall Regatta 2026 (J/24s)

Sat 14 Nov 2026, San Diego Bay, two races, two tracked J/24s: **Kud** and **WR** (Velocitek `.vtk` logs).

- `report/fleet.html`: the two boats compared (replays, starts, side by side, wind and current, fleet debrief).
- `report/<boat>/Fall-Regatta-2026_<boat>_report.html`: each boat's debrief and data.
- `report/tacks.html`, `report/gybes.html`: every comparable tack and gybe of both boats.

How it was made:
- The logs were read with `skills/sailing-coach/scripts/vkx/vtk_parser.py`. Kud pinged the start line; its pings were 23–26 m downwind of where both boats were at the gun, so the start lines, marks and finishes were estimated from the tracks and corrected by hand on the course map (`regatta.json`).
- Guns: 12:54 and 14:38 local. WR's timer also ran to zero for other sequences (12:30, 12:37, 12:45, 14:20, 14:32); those are skipped.
- No wind sensor on either boat, and no J/24 target card, so the boats are compared with each other.
- The reports are data only (no coaching); `report/about-the-data.md` is their About the data page.
- `rebuild.sh` regenerates everything (with `SAIL_CLASS=J24`) from `vtk/`, `regatta.json` and `report/about-the-data.md`.
