# PCC 2026, day 1 (2026-02-21), San Diego: 1044, Chomp and Mojo (Etchells)

Three races, three boats, exported from Njord at 1 Hz, from 7 min before each gun to about
3 min after Njord's race end. One folder per boat; the same files as the July samples:

| File | What |
|---|---|
| `<boat>/raceN.csv` | Njord `get_data` output (position, SOG/COG, heading, heel, trim, TimeToGunCalc, BelowLineCalc, Leg, VMC, XTE, Rank, DistanceToLeader; TWA/TWS/TWD/VMG on Mojo and 1044 only) |
| `<boat>/raceN-raceInfo.json` | Njord detected events. Chomp's have no Tack/Gybe events, so `analyze.py` detects them from heading and heel. |
| `<boat>/raceN-race.json` | Race name, timezone, gun and course (start line, windward mark, offset, leeward gate, finish) from the GraphQL `event.races` query |

Used by `tests/test_fleet.py`. Things this data exercises:

- **Njord's leg split fails for Chomp** after race 1 (its `Leg` column stays at 1–2, and `fleetRaceInfo` has no legs for it in races 2–3). `analyze.py` falls back to mark passages from the course (`course_legs`), which shows Chomp 2nd in every race.
- **Over at the gun and restarted:** Mojo race 2 and 1044 race 3 are 2.5 m over at the gun, back behind the line within ~5 s, and restart at +10 s.
- **Model-feed wind:** TWS on Mojo and 1044 is identical and changes by ~0.001 kt a second (a smoothed weather-model feed, not measured). `analyze.py` doesn't trust it, so there are no targets.
- **Offset marks:** every windward mark has an offset ~20–30 s away; rounding stats run on to the offset.
