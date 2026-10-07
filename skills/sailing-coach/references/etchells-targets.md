# Etchells targets

Both tables come from Kevin's on-boat cards. `scripts/coach_calcs.py` embeds the target card, so the numbers here are for reading and explaining, not re-typing.

## Upwind targets — flat water

| TWS (kts) | Vb (kts) | TWA (°) | Heel (°) |
|---|---|---|---|
| 7  | 5.1 | 43 | 12 |
| 8  | 5.5 | 41 | 14 |
| 9  | 5.7 | 39 | 16 |
| 10 | 5.8 | 38 | 17 |
| 11 | 5.8 | 38 | 18 |
| 13 | 5.9 | 37 | 21 |
| 15 | 6.0 | 36 | 22 |
| 17 | 6.0 | 37 | 22 |
| 19 | 6.1 | 37 | 22 |

How to read it:

- **Gaps (12, 14, 16, 18 kts)** are linearly interpolated.
- **Outside 7–19 kts** the script clamps to the nearest row and flags the band. Say the target is extrapolated when you coach off it.
- **Vb is boat speed through the water.** If only SOG is available, current shifts the comparison. Compare port vs. starboard in the same band; a consistent split between tacks usually means current, not technique.
- **Heel caps at 22° from 15 kts up.** Above that, extra wind should become depowering (and speed stays nearly flat at 6.0–6.1), not more heel. Heel over target in breeze usually means overpowered; heel under target in light air usually means not enough power or crew weight in the wrong place.
- **TWA narrows from 43° to 36° by 15 kts, then opens slightly.** Sailing wider than target with speed at or above target is often a deliberate fast mode; wider *and* slow is a problem.
- **Flat water only.** In chop, expect lower Vb and wider TWA. Don't call that an error without knowing the sea state.

## Starting ratio — time to cover distance

Seconds needed to cover a distance at a given boat speed, used to turn "meters below the line at the gun" into "seconds late."

| Meters | 2 kts | 3 kts | 4 kts | 5 kts | 6 kts |
|---|---|---|---|---|---|
| 5   | 5   | 3  | 2.5 | 2  | 1.6 |
| 10  | 10  | 7  | 5   | 4  | 3   |
| 15  | 15  | 10 | 8   | 6  | 5   |
| 20  | 20  | 13 | 10  | 8  | 7   |
| 30  | 30  | 20 | 15  | 12 | 10  |
| 40  | 40  | 26 | 20  | 16 | 13  |
| 50  | 50  | 32 | 25  | 19 | 16  |
| 60  | 60  | 39 | 30  | 23 | 19  |
| 70  | 70  | 45 | 35  | 27 | 23  |
| 80  | 80  | 52 | 40  | 31 | 26* |
| 90  | 90  | 59 | 45  | 35 | 29* |
| 100 | 100 | 65 | 50  | 39 | 32  |

\* These two cells were hidden in the source photo and are calculated here (distance ÷ speed in m/s). Confirm against the original card.

Notes:

- The card's 2 kts column uses 1 m/s as a round number, which is about 3% slow. `coach_calcs.py tod` uses the exact conversion (1 kt = 0.5144 m/s), so its answers match the card within about 1 s.
- Use the speed the boat will actually hold on the run to the line, not the speed at the gun. A boat that's slow at the gun and still accelerating is later than the table suggests.
- Etchells length is roughly 9 m, so "a boat length back" is about 4 s late at 5 kts.
