# PCC 2026 (2026-02-21 and 22), San Diego: 1044, Chomp and Mojo (Etchells)

Five races (1–3 on Saturday, 4–5 on Sunday), three boats, exported from Njord at 1 Hz, from 7 min before each gun to about
3 min after Njord's race end. One folder per boat; the same files as the July samples:

| File | What |
|---|---|
| `<boat>/raceN.csv` | Njord `get_data` output (position, SOG/COG, heading, heel, trim, TimeToGunCalc, BelowLineCalc, Leg, VMC, XTE, Rank, DistanceToLeader; TWA/TWS/TWD/VMG on Mojo and 1044 only) |
| `<boat>/raceN-raceInfo.json` | Njord detected events. Chomp's have no Tack/Gybe events, so `analyze.py` detects them from heading and heel. |
| `<boat>/raceN-race.json` | Race name, timezone, gun and course (start line, windward mark, offset, leeward gate, finish) from the GraphQL `event.races` query |
| `official.json` | Official results from YachtScoring (event 50508, 42 boats), written by `scripts/yachtscoring.py`: places, points, day and event totals, Corinthian division |

Used by `tests/test_fleet.py`. Things this data exercises:

- **Njord's leg split fails for Chomp** after race 1 (its `Leg` column stays at 1–2, and `fleetRaceInfo` has no legs for it in races 2–5). `analyze.py` falls back to mark passages from the course (`course_legs`), which shows Chomp 2nd of the three in races 1–4 and 3rd in race 5.
- **Over at the gun and restarted:** Mojo race 2 and 1044 race 3 are 2.5 m over at the gun, back behind the line within ~5 s, and restart at +10 s. On Sunday Mojo is over in both races (8 m in race 4, 1.3 m in race 5), sails on 10–15 s and restarts at +44 s and +52 s.
- **Model-feed wind:** TWS on Mojo and 1044 is identical and changes by ~0.001 kt a second (a smoothed weather-model feed, not measured). `analyze.py` doesn't trust it, so there are no targets.
- **Offset marks:** the windward marks in races 1–4 have an offset ~20–30 s away; rounding stats run on to the offset.
- **Finish near the line end:** Chomp crosses race 5's finish close to the end; line crossings count up to 20 m past either end.
- **Photo finish:** race 4, Chomp is 2 s ahead of Mojo by GPS but a place behind officially (within the antenna-to-bow difference). Every other race's GPS order matches the official places.
- **YachtScoring finish times aren't usable:** some are data-entry artifacts (race 1's last finish is the next morning), so only places and points are read.
