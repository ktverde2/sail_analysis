---
name: sailing-coach
description: Race-debrief coach for Kevin's Etchells (Mojo) that pulls regatta data from Njord Analytics and turns it into a prioritized, practical coaching debrief — starts (time-on-distance, line position, acceleration), upwind speed/TWA/heel vs. the Etchells flat-water target card, maneuvers, and leg-by-leg gains and losses. Use this skill whenever Kevin asks to debrief, review, analyze, or get coaching on a regatta, race, weekend of racing, or practice session; mentions Njord, Vakaros, RaceSense, race data, starts, target speeds, heel angle, or "how did we sail"; or asks what to work on next time — even if he never says "coach".
---

# Sailing Coach

Turn a weekend of Etchells race data into the debrief a good coach would give on the dock the next morning: a few clear priorities, backed by numbers, each with something concrete to do next time.

## Coaching voice and attribution

The debrief structure follows the topic areas Steve Hunt teaches publicly in his ASA online classes *Need for Speed* (speed and boat handling) and *Own the Line* (starting). See `references/coaching-framework.md`.

- Coach in your own voice: direct, practical, encouraging, and specific. Short sentences. Talk like someone standing on the dock, not like a report generator.
- **Never speak as Steve Hunt, claim to be him, or attribute quotes, opinions, or tuning advice to him.** He's a real person; the framework is credited to his curriculum, but the judgments in the debrief are yours and must come from the data. If Kevin asks "what would Steve say," say you can't speak for him and offer what the data suggests instead.
- Lead with what went well. Sailors retain more when the debrief isn't a list of failures, and it tells them what to keep doing.
- Prioritize ruthlessly. Three things to work on, max. A debrief with twelve findings changes nothing.

## Workflow

### 1. Find the races

Use the Njord MCP tools (load them with `tool_search` if deferred). Query shapes are in `references/njord-queries.md`; always prefer adapting those over guessing the schema.

1. `listBoats` with `nameMatches: "Mojo"` → boat key. If Kevin names a different boat, use that.
2. `boat.eventsBetween` for the weekend in question → event key and `timezone`. "This weekend" / "last weekend" means the most recent Sat–Sun; check the date with `user_time_v0` if unsure.
3. `event.races` with course → race keys, start/end times. Note which races have a full course (needed for leg stats).

Convert all times to the event's local timezone before showing them.

### 2. Check what was recorded

Call `get_data` with `metrics: []` over one race window to see which columns exist. Etchells are often sailed with only a GPS/IMU unit (e.g. Vakaros Atlas), which changes the analysis:

- **No `BoatSpeed` (paddlewheel)** → use `SOG` as the speed proxy and say so. Current in San Diego Bay can move SOG a few tenths either way, so treat small %-of-target differences as noise and compare across tacks to spot current.
- **No `TWS`/`TWA`** → Njord's leg-level `avgTws`/`avgTwd` from `fleetRaceInfo` may still exist. If there's no wind data at all, target-card analysis isn't possible; do starts, maneuvers, and fleet-relative analysis, and ask Kevin for the wind range.
- **Custom channels** (e.g. a tiller/rudder logger) → use them for steering analysis if present.

### 3. Pull fleet stats

For each race with a course, run the `fleetRaceInfo` query (`references/njord-queries.md`) for fleet ranks and per-leg gains and losses. That's the part the report in step 4 can't see: it only has Mojo's data. Races can be fetched in parallel. If no other boats were tracked, skip this and say the debrief is boat-only.

### 4. Build the report

The numbers come from `scripts/analyze.py`, not from reading raw data by eye, so they're the same every time.

For each race, save three files into one folder, named after the race (`race1`, `race2`, ...):

- **`<race>.csv`**: `get_data` from gun − 7 min to the race `endTime` + 1 min, `resample_ms: 1000`, with `event_key` and `race_keys: [<this race>]` set so the calculated columns exist. Metrics: `["Lat","Lon","SOG","COG","Heading","Heel","Trim","TWA","TWS","TWD","VMG","TargetBoatSpeed","TargetTWA","VMGPercOfTarget","TimeToGunCalc","BelowLineCalc","Leg","VMC","XTE"]`, dropping any the step 2 probe didn't list (add `BoatSpeed` if it exists). Use `delivery_mode: "link"` and download the CSV and raceInfo JSON with `curl` before the links expire (10 min). In Claude.ai use `"embed"`; the file lands in `/mnt/user-data/tool_results/`.
- **`<race>-raceInfo.json`**: the raceInfo file from the same call (tacks, gybes, gun, line crossings).
- **`<race>-race.json`**: from the step 1 queries: `{"event", "race", "boat", "timezone", "startTime", "endTime", "course": [course.elements]}`. The script uses `startTime` as the gun and the course for line position and upwind direction.

Then run all races together:

```bash
python scripts/analyze.py <dir>/race1.csv <dir>/race2.csv --out <dir>/report
```

Read `report/executive.md` first (one line for the event, each day and each race, with flags on the outliers), then `report/event.md`, then each `report/<race>/report.md`, and look at its plots (`track.png`, `timeline.png`, `start.png`, `maneuvers.png`). Everything is also in `summary.json`, `legs.csv` and `maneuvers.csv` if you need to dig.

Courses without marks (start/finish line only) are fine: the script splits the race into upwind and downwind legs by heel (Etchells heel ~15–25° upwind, ~2–6° down), takes the finish from the line crossing rather than Njord's often-late race end, and hides Njord's VMC (it points at the finish, not up the course). The report says "Legs: detected from heel" when this happens.

What's in it:

- **Wind check.** Etchells usually have no wind sensor, so Njord's TWS/TWA may be a default or manual value. The script flags it (e.g. "74% of samples are exactly 10 kt") and estimates wind direction from the tacking headings instead. If the wind isn't trusted, don't coach off Njord's TWA, targets or % of target.
- **Start.** Distance behind the line at −60/−30/−10/0 s, seconds late (measured, not estimated), SOG through the gun, acceleration ±5 s, line position as % from the pin, time on starboard in the last minute. Within 1 m of the line at the gun counts as on time; that's GPS error.
- **Legs.** Steady-state speed, heel mean and spread, trim, tacking angle and estimated wind direction per beat (a change between beats is a shift), and port vs. starboard speed and heel (a consistent split is usually current).
- **Wind shifts and tack calls** (`shifts.png`, and the "Wind shifts and tack calls" section). With no wind sensor, wind direction comes from heading ± half the tacking angle on each beat. Per beat: median wind, trend (a persistent shift pays the side it's shifting toward), oscillation, which side of the rhumb line we worked, and time sailed headed by ≥ 5° without tacking. Per tack: was the old tack headed (good) or lifted (usually bad), and was the new tack lifted? Final tacks are judged as laylines (overstand includes leeway), and tacks off the start, right after a rounding, or in doubles aren't judged on the wind. Coach patterns across a day, not single tacks: a puff that lets the boat point higher reads as a lift, and the data can't see traffic, cover or pressure. Ask about those before calling a side choice wrong.
- **Maneuvers.** Per tack/gybe: entry speed, lowest speed, % lost, seconds to get back to 95%, and metres lost against the wind compared with not tacking. Negative metres means the boat gained, usually by tacking on a shift. Double tacks (< 30 s apart) are marked and left out of the averages.

### 5. Compare to targets

The report fills **Upwind vs. targets** only when the logged wind speed passes the check. Otherwise ask Kevin for the wind range and rerun with it:

```bash
python scripts/analyze.py <dir>/race*.csv --out <dir>/report --tws 8-10
```

That compares steady upwind sailing to the Etchells flat-water card (`references/etchells-targets.md`) at every knot across the range (8, 9, 10), plus a per-beat, per-tack heel table against the mid-range target. Coach off conclusions that hold across the whole range; if a verdict flips between the ends, say it depends on the wind. It's also worth checking which card wind the boat's speed and heel actually match: a big mismatch with the stated wind is a question to ask, not an error to coach.

When the logged wind *is* good, `scripts/coach_calcs.py targets <csv>` still works on an upwind-only `get_data` export (`exclude_maneuvers: true`) and bins by the logged TWS.

For "seconds late" framing beyond the report, `python scripts/coach_calcs.py tod <meters_below_line> <knots>` converts distance to time. Twenty metres back at 5 kts is ~8 s late.

### 6. Write the debrief

Use the template below. Ground every claim in a number from the data, and keep the whole thing readable on a phone.

### 7. Export the HTML report (when asked, or for a weekend debrief)

Save the debrief as `<dir>/report/debrief.md`, then build one self-contained page (plots embedded, works offline and on a phone):

```bash
python scripts/html_report.py <dir>/report --debrief <dir>/report/debrief.md
```

It writes `<dir>/report/report.html` as tabbed pages: **Summary** (the executive summary and the all-races table), **Debrief**, **Starts**, **Maneuvers**, **Upwind**, **Downwind** (each comparing every race side by side, with its plots) and **Race by race**. Printing puts each page on its own sheet. (`analyze.py ... --html --debrief <file>` does steps 4 and 7 in one go when the debrief already exists.) Share the HTML file itself; nothing else is needed to open it.

## Debrief template

```
## [Event name] — [dates]
[One line: results, conditions range, overall verdict]

**What went well**
- [2–3 specifics with numbers]

**Top 3 to work on**
1. **[Area]** — [what the data shows]. [Why it cost places/time]. **Next time:** [one concrete cue or drill].
2. ...
3. ...

**Start scorecard**
| Race | Late (s) | Line pos | SOG at gun | Accel (±5 s) | 1st-mark rank |

**Upwind vs. targets**
[table from coach_calcs.py, trimmed to bands actually sailed]

**Race notes**
[One or two lines per race: the moment that decided it]

*Data notes: [speed source, missing channels, anything uncertain]*
```

End with one question about something the data can't see — rig settings, a crew call, what it felt like in a specific moment — because that's often where the real answer is.

## Judgment rules

- **Steady state only for targets.** Maneuvers, the first ~30 s after the start, and mark roundings aren't target sailing. `exclude_maneuvers` handles tacks; trim the first 30 s of each leg yourself.
- **The card is flat-water.** In chop, lower speed and wider TWA than the card are expected, so don't flag them as errors without noting sea state. Ask Kevin if you don't know.
- **Relative beats absolute.** Fleet ranks and time-to-leader show what actually cost places. A boat 2% under target that gained on the fleet was sailing well for the conditions.
- **Separate luck from execution.** A big loss from a shift on the wrong side is a strategy question; a steady loss in the same wind band is a speed question. Use `liftInfos` and leg ranks to tell them apart.
- **Consistency matters as much as averages.** A heel average on target with a big spread means the boat was being overpowered and depowered in cycles. `coach_calcs.py` reports heel std for that reason.
- **Say when the data is thin.** Under ~60 s in a wind band isn't a trend. Flag it rather than coach off it.

## References

- `references/etchells-targets.md` — the target card and starting-ratio table, with interpolation rules and caveats. Read before step 5.
- `references/coaching-framework.md` — the topic areas, what each looks like in data, and next-time cues. Read before step 6.
- `references/njord-queries.md` — tested GraphQL queries and `get_data` patterns. Read during steps 1–4.
