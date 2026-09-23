# July ODW, 2026-07-19, San Diego: Mojo (Etchells)

Two races exported from Njord at 1 Hz, from 7 min before each gun to the finish.
Used as real-world test data by `tests/test_analyze.py`.

| File | What |
|---|---|
| `raceN.csv` | Njord `get_data` output (position, SOG/COG, heading, heel, trim, wind, targets, TimeToGunCalc, BelowLineCalc, Leg, VMC, XTE) |
| `raceN-raceInfo.json` | Njord detected events (tacks, gybes, gun, line crossings). Includes events outside the race window; `analyze.py` filters them. |
| `raceN-race.json` | Race name, timezone, gun time and course, from the Njord GraphQL `event.races` query |

Known issue: the logged wind (TWS/TWA/TWD) isn't measured. Race 1 TWS is exactly 10 kt for 74% of samples;
race 2 TWS is ~2.5 kt while the boat sails ~6 kt. Njord's target and %-of-target columns inherit this.
