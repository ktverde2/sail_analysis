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

- **`<race>.csv`**: `get_data` from gun − 7 min to the race `endTime` + 1 min, `resample_ms: 1000`, with `event_key` and `race_keys: [<this race>]` set so the calculated columns exist. Metrics: `["Lat","Lon","SOG","COG","COG_Mag","Heading","Heading_Mag","MagneticVariation","Heel","Trim","TWA","TWS","TWD","VMG","TargetBoatSpeed","TargetTWA","VMGPercOfTarget","TimeToGunCalc","BelowLineCalc","Leg","VMC","XTE"]`, dropping any the step 2 probe didn't list (add `BoatSpeed` if it exists). Njord's race `endTime` can also come before the boats finish (a race someone closed early): after analysing, check that the last leg ends at a finish-line crossing, not at the end of the data, and if it doesn't, refetch to well past the last finish and set `endTime` to it. The magnetic channels let the reports show every direction magnetic, as the boat's compass reads (see below). Use `delivery_mode: "link"` and download the CSV and raceInfo JSON with `curl` before the links expire (10 min). In Claude.ai use `"embed"`; the file lands in `/mnt/user-data/tool_results/`.
- **`<race>-raceInfo.json`**: the raceInfo file from the same call (tacks, gybes, gun, line crossings).
- **`<race>-race.json`**: from the step 1 queries: `{"event", "race", "boat", "timezone", "startTime", "endTime", "course": [course.elements]}`. The script uses `startTime` as the gun and the course for line position and upwind direction.

Then run all races together:

```bash
python scripts/analyze.py <dir>/race1.csv <dir>/race2.csv --out <dir>/report
```

Read `report/executive.md` first (one line for the event, each day and each race, with flags on the outliers), then `report/event.md`, then each `report/<race>/report.md`, and look at its plots (`track.png`, `timeline.png`, `start.png`, `maneuvers.png`). Everything is also in `summary.json`, `legs.csv` and `maneuvers.csv` if you need to dig.

Courses without marks (start/finish line only) are fine: the script splits the race into upwind and downwind legs by heel (Etchells heel ~15–25° upwind, ~2–6° down), takes the finish from the line crossing rather than Njord's often-late race end, and hides Njord's VMC (it points at the finish, not up the course). The report says "Legs: detected from heel" when this happens. When the course has marks but Njord's own leg split missed a rounding, the legs come from the course marks instead (closest approach to each mark, leg 1 from the boat's own line crossing), and VMC still works.

What's in it:

- **Wind check.** Etchells usually have no wind sensor, so Njord's TWS/TWA may be a default or manual value. The script flags it (e.g. "74% of samples are exactly 10 kt") and estimates wind direction from the tacking headings instead. If the wind isn't trusted, don't coach off Njord's TWA, targets or % of target.
- **Start.** Distance behind the line at −60/−30/−10/0 s, seconds late (measured, not estimated), SOG through the gun, acceleration ±5 s, line position as % from the pin, time on starboard in the last minute. Within 1 m of the line at the gun counts as on time (GPS error), unless the boat then went back behind the line and restarted: that's an OCS return at any distance, and its first beat is analysed from the restart.
- **Legs.** Steady-state speed, heel mean and spread, trim, tacking angle and estimated wind direction per beat (a change between beats is a shift), and port vs. starboard speed and heel (a consistent split is usually current).
- **Wind shifts and tack calls** (`shifts.png`, and the "Wind shifts and tack calls" section). With no wind sensor, wind direction comes from heading ± half the tacking angle on each beat. Per beat: median wind, trend (a persistent shift pays the side it's shifting toward), oscillation, which side of the rhumb line we worked, and time sailed headed by ≥ 5° without tacking. Per tack: was the old tack headed (good) or lifted (usually bad), and was the new tack lifted? Final tacks are judged as laylines (overstand includes leeway), and tacks off the start, right after a rounding, or in doubles aren't judged on the wind. Coach patterns across a day, not single tacks: a puff that lets the boat point higher reads as a lift, and the data can't see traffic, cover or pressure. Ask about those before calling a side choice wrong.
- **Roundings** (`roundings.png`, "Mark roundings" section). Every windward and leeward rounding: the approach (layline tack and overstand, or last gybe), SOG in / lowest / out, seconds to settle (10 s VMG back to 90% of the next leg's), closest distance to the mark and which gate mark, and **metres lost** toward the marks: VMC (to this mark, then the next) from 30 s before to 60 s after the mark against the steady VMC of the legs either side. Each rounding also has **vs best** (metres more than the best rounding of its type in the same analysis) and up to three **tips** comparing it with that best: exit angle, overstand, distance off the mark, speed dip, time to settle, loss on the approach, a maneuver right after the mark. The goal is zero metres lost; the best so far is the next milestone. Roundings are often where the most distance changes hands, so compare them across races. In the HTML report each rounding's track map opens **zoomed on the mark**, with the **zone** drawn (three hull lengths: an Etchells is 9.3 m, so 28 m) and the moment the boat entered it; a button widens it to the minute either side.
- **Upwind polars** (`polar.png`). Speed against wind angle for each beat, starboard right and port left, with the card's targets as diamonds; height is upwind VMG. The wind angle comes from headings, so the average angle equals half the tacking angle by design. Read the spread (high vs. fast mode) and the port/starboard difference, not the absolute angle.
- **Distance sailed** (legs table, Upwind/Downwind pages): each leg's distance sailed vs. the straight line mark to mark, and vs. the *ideal* at our own average track angle to the wind in steady wind. Extra vs ideal is distance beyond what our angles needed (sailing low, overstanding, extra zig-zags); shifts can make it negative.
- **Maneuvers.** Per tack/gybe, scored toward the mark (VMC), in two parts that add up: **handling** (`distance_lost_m`: the dip in VMC through the turn, −5..+25 s, against a line from the VMC before to the VMC once settled; the cost of the turn itself) and the **call** (`call_m`: what changing onto the new tack's VMC was worth; negative = gained, e.g. tacking off a header). Entry speed, lowest speed, % lost and seconds back to 95% explain the handling; they don't score it. Left out of the averages, with a note: doubles (< 30 s apart), maneuvers that straddle a mark (in the rounding's number), ones made from past the layline (their gain is ending the overstand: a layline call), and ones that gain more than 5 m through the turn itself (a shift or puff arrived mid-turn, so the handling can't be judged).
- **Current** (`drift.json`, the **Current** page). COG against heading, from steady sailing only (no maneuvers, roundings or the first and last 20 s of each leg). The gap adds up the compass offset, leeway and current. Without a paddlewheel, current running along the wind can't be told from leeway, so each boat is fitted, over all its races, for: **compass offset** (the same on every heading: an offset near the local magnetic variation, about 11° in San Diego, means the unit is applying variation it shouldn't (heading reads low: subtracted; high: added twice)), **upwind slip** (leeway plus any along-wind current: between boats in the same water, a slip difference is a leeway difference, and that's the firm finding) and **current across the course** (kt, + toward the right side looking upwind). Then a breakdown (upwind/downwind x port/starboard), a leg-by-leg chart (both tacks moving together from one beat to the next means the water changed; apart means the slip did), and a **course map** of every boat's data in cells (upwind up the page): current across, drift angle, or coverage, filterable by boat, race, leg type and tack. The page writes its own notes: a big compass offset, slip differences between boats, whether the cross-course current is measurable, and a drift that differs between gybes on one boat but not another in the same water (compass deviation, not current). With three or more boats, a boat whose cross-course current disagrees with the others' by 0.25 kt or more in most of its races is flagged as a **suspect compass** (heading-dependent error, often with negative slip, the track to windward of the heading): it's left off the map and out of the fleet comparisons, and its heading-based angles shouldn't be coached from. With two boats, disagreement can't say which boat is wrong. Coach from what's firm (slip differences, offsets to fix in the instruments); treat a cross-course current under ~0.2 kt, or one the boats disagree on, as unmeasured, and a patchy map with no organised region as noise.
- **Tide and current from NOAA** (`noaa.json`, on the Current page). After `analyze.py`, run `python scripts/noaa.py --reports <dir>/report` (the fleet folder, or one boat's). It finds the NOAA current-prediction and tide stations nearest the course from the boats' GPS, and saves their predictions around the races (and the tide gauge's observations) to `noaa.json`; reports then rebuild offline. Needs `api.tidesandcurrents.noaa.gov` on the network allowlist; if it's blocked, say so and carry on without it. Subordinate current stations only publish max/slack times; the script fills in between them the way NOAA does (checked against a harmonic station: within 0.03 kt). The page shows the tide, the station's current with the races shaded, NOAA's current resolved across the course against what the boats measured, and an arrow on the map. Coach timing from it (flood, ebb, when it turns), not strength, unless the boats' measurement agrees: a station at a channel mouth a few km away can predict a knot the race area never saw. `--current-station ID` picks another station (the page lists the nearest few in `noaa.json`).
- **Laylines and ladder rungs** (`ladder.py`, `ladder.js`). Each leg's wind axis and half tacking (or gybing) angle come from the boats' own GPS tracks: the bisector of the two tacks' median track directions (a run takes its axis from the beats either side, since a run sailed on one gybe can't show it). Every course map (the race track, each leg's track, each rounding's close-up, and in the fleet report the replay and the side-by-side map) has a **Laylines & rungs** button, top left, that shows the leg's laylines (from the mark; from each end of a gate or finish line, each layline from the end on its own side) and rungs every 100 m (counted from the end a boat reaches first) (hover a label for the distance to sail from that rung). On the replay they follow the leading boat's leg, and each boat's **To sail** is the same distance-to-sail measure. The boat report uses that boat's tracks; the fleet report uses every boat's.
- **Tack and gybe overlays** (`overlay.json`, the **Tacks** and **Gybes** pages). Every comparable tack (or gybe) lined up at the middle of the turn (head to wind, or dead downwind): VMC to the mark (default) or SOG with entry, lowest and back-to-95% markers, heading turned (where it settles is the tacking or gybing angle; a hump is overshoot), and the angle by compass and over the ground against **time lost toward the mark** (the handling cost: the dip in VMC through the turn against a line from the VMC going in to the VMC once settled, in seconds; the call is shown beside it). The best 10% (or 20/25%) by time lost toward the mark are highlighted, and **What the best tacks did** compares their medians with the rest: entry and lowest speed, time to recover, turn time, overshoot, heel at +10 s, angle. Left out: doubles, stalls (entry under 4 kt), and turns at a mark (a tack over 110° or into a run; a gybe heeled like a beat on either side). Coach from the pattern, not one tack: say which boat-handling habit the best ones share (e.g. "back to heel quickly on the new tack"). Under ~10 in view the page warns that it's too few to rank; with few gybes, describe them rather than rank them. Tacks from past the layline and ones that gain through the turn (a shift mid-turn) are left out of the ranking.

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

For each priority, open `references/playbook/index.md`, which maps what the data shows to the right file (starts, upwind strategy, tactics, roundings, downwind, boat handling, Etchells technique), and take the principle and the next-time fix from there. Use the playbook to pick the right fix, not to pad the debrief: one principle per priority, in your own words.

### 7. Export the HTML report (when asked, or for a weekend debrief)

Write the review at three depths (templates below) and save them in `<dir>/report/`: `overview.md` (**Quick look**, for crew who won't dig: two minutes), `debrief.md` (**Debrief**: the coaching, as now) and `deep-dive.md` (**Deep dive**: race notes, leg by leg). Then build one self-contained page (plots embedded, works offline and on a phone):

```bash
python scripts/html_report.py <dir>/report --debrief <dir>/report/debrief.md \
  --overview <dir>/report/overview.md --deep <dir>/report/deep-dive.md
```

The page opens on **Quick look**, and a three-way switch at the top moves between the depths; each depth has its own pages. Without `--overview` the Quick look is the coach's summary lifted from the debrief; without `--deep` the Deep dive is just the data pages. It writes `<dir>/report/report.html`: **Quick look**; **Debrief** and **Summary** (the executive summary and the all-races table); then, at Deep dive, **Race notes**, **Starts**, **Maneuvers**, **Tacks** and **Gybes** (the overlays, when there are comparable ones), **Upwind** (each beat's track and polar), **Downwind** (each run's track), **Roundings** (a close-up track and speed/VMG chart for every mark), **Current** (COG vs heading and the course map; in the fleet layout it uses every boat analysed alongside this one, so analyse all boats before building any report), each comparing every race side by side, and **Race by race** (the whole-race track). Charts are interactive: hover (or tap on a phone) shows time from the gun (time *to* the gun on the start charts), speed, VMG, heading and heel; drag to zoom, double-click to reset. They come from each race's `plotdata.json` and the Plotly library in `scripts/vendor/`, all inlined, so the file still works offline. Add `--static` to `html_report.py` for PNG charts instead. **To send the report**, upload the HTML to Google Drive (or similar) and email the link: Gmail's virus scan flags interactive HTML attachments (a false positive), and recipients download the file and open it in a browser. `--cdn` (on `html_report.py` or `analyze.py`) loads Plotly online instead of inlining it for a ~1 MB smaller file; the charts then need internet when the file is opened. Printing puts each page on its own sheet. (`analyze.py ... --html --debrief <file>` does steps 4 and 7 in one go when the debrief already exists.) Share the HTML file itself; nothing else is needed to open it.

## Fleet comparison (several tracked boats in one event)

When the event has more than one boat in Njord (`event.boats`), and Kevin asks how the boats compared, do every boat, then the fleet:

1. **Pull each boat** exactly as in steps 1–4, one folder per boat (`<dir>/data/<boat>/raceN.csv` plus `-raceInfo.json` and `-race.json`). Pass the other boats as `rank_boat_keys` so `Rank`/`DistanceToLeader` come along. Probe each boat's channels first: some log no wind at all.
2. **Analyse each boat:** `python scripts/analyze.py <dir>/data/<boat>/race*.csv --out <dir>/report/<boat>`.
3. **Compare:** `python scripts/fleet.py --data <dir>/data --reports <dir>/report --out <dir>/report --title "<Event> · Day N · Fleet"`. It writes `fleet.md` and `fleet.json`: results, gap to the leader at every mark, leg-by-leg times against the fastest boat, starts, side of the course per leg, roundings, and **where the time went**. That splits each boat's finishing gap into start, upwind legs and downwind legs against the race winner, and the parts add up to the gap.
   It also writes, per race:
   - **Wind and sides.** The wind through every leg, from every boat's GPS tracks: each boat's two tacks give its angle over the ground, and every second the wind is its track turned by half that angle. Compass calibration, leeway and steady current all drop out. The fleet's wind is the median in each minute. Per leg: start and end direction (magnetic), swing, pattern (analyze.py's thresholds) and each boat's average distance from the rhumb line. **What paid** is judged only on a persistent shift: whether the boat furthest toward the shift gained on the boat furthest the other way. The table also gives what the shift alone was worth: lateral separation × sin(net shift), at the leg's speed toward the mark. When that's under 10 s, the boats were too close together for the side to matter. When the actual gain is much bigger than it, the rest is pressure or speed; say so. The same convention works on runs: + is right looking at the mark, and a left shift favours the left looking at the mark on beats and runs alike.
   - **Starts.** Every tracked boat from −3:00 to +2:00, in the course frame. The first beat's ladder gives which end was favoured, both in metres further up the course and in metres less to sail. Per boat: where it crossed (% from the pin) and what that spot gave away in distance to sail; distance behind the line and speed at −60, −30, −10 s and the gun; **time on distance** at −30 and −10 s (sailing straight at the line at that speed, how early or late it would have been); its place and metres behind the leader up the ladder at +30 s, +1 min and +2 min; and the nearest tracked boat at the gun. The fleet page has a **Starts** page with a replay of each start, zoomed on the line: a slider and play, arrows along each boat's course, the first beat's rungs as a toggle, and a live panel showing each boat's distance behind the line and time on distance before the gun, then distance behind the leader up the ladder after it. Below the replay is a card of plain facts for each boat. In a debrief, `[[chart:starts:race3]]` embeds one race's start.
   - **Close roundings.** Every mark where two or more boats rounded within 30 s of each other, with each boat's time into the zone, round the mark and out of it, and its closest pass. When the first boat reached the zone, each other boat is marked as clear astern, clear ahead or overlapped (along the first boat's course, within a hull length), or as having gone round the other gate mark. The closest approach between each pair is also given. It's GPS, so a metre or two: good for a debrief, not a protest.
   - **Side by side** (end of `fleet.md`): every stretch where two boats sailed within 200 m on the same leg and the same tack or gybe for at least a minute. They had the same wind, so the gain is the boats. **Gain is distance to sail to the mark** (`ladder.py`): inside the laylines that's rungs up the ladder, so sailing lower loses rungs; past a layline it's the straight line back to the mark, so overstanding counts against the boat. It's split into **speed** and **course** (height or depth, plus not overstanding: how much of each metre sailed brought the mark closer), and a stretch that ends past a layline says so. Never report progress straight up the course or straight-line distance to the mark as a gain: the first ignores overstanding, the second treats a boat off to one side inside the laylines as further away when it isn't. This is the firmest boat-against-boat evidence there is without a wind sensor: use it to say *why* a boat is faster (e.g. "slower but higher, and gains"), and cite stretches by race and time so readers can watch them on the replay. Each stretch has an id (`R2-9`) and **where** it was: along the leg (first, middle or last third, from the last mark or its offset to the next) and across it (left, middle or right of the rhumb line, looking at the next mark). "Where the stretches were" counts them by zone. Where the gains happen matters as much as their size: e.g. height that pays in open water but not on the layline approach is a different fix from height that never pays.
   - **Why, stretch by stretch** (end of `fleet.md`): one sentence per stretch saying what won it (speed or angle, by how much, the heel difference, whether the gain was steady or a burst, and time possibly in the other boat's wind shadow). Cite these rather than re-deriving them. On the page, each stretch opens to a table of both boats and a second-by-second chart (moving along it shows every value at that second in a panel beside the chart, no hover box over the plots): metres gained (with its speed and angle parts), SOG (15 s rolling average with a shaded ±1 SD band of the 1 Hz readings; heel and trim the same), pointing (track angle to the course; the compass heading dotted), heel, and fore-aft trim. Heading and trim come from each boat's own sensors, zeroed differently (trim differed by up to ~11° between the PCC boats), so compare their changes, not the two boats' values; the track angle is from GPS and is the one to coach pointing from. Wind shadow is a geometric flag (within 75 m and 25° of straight downwind of the other boat, with the course axis as the wind): say "possibly in bad air".
4. **Official results.** SDYC events are scored on YachtScoring: the event id is in the results link (`yachtscoring.com/event_results_cumulative/<id>`, linked from the SDYC event page). Run `python scripts/yachtscoring.py <id> --out <dir>/data/official.json`, then give `fleet.py` `--official <dir>/data/official.json --sail <boat>=<sail number>` for each boat; the entry list (boat name, sail number, skipper) is in the file. That adds each boat's official places, day total and rank, event place and Corinthian place, plus what a place was worth in seconds. Use official places and points only; YachtScoring's finish times can be data-entry artifacts, so gaps always come from GPS. Needs `api.yachtscoring.com` on the network allowlist.
5. **Write each boat's review at the three depths** (`overview.md`, `debrief.md`, `deep-dive.md` in `<dir>/report/<boat>/`, the templates below) and a **fleet debrief** (`<dir>/report/fleet-debrief.md`). In the fleet debrief, **What went well** is each boat's biggest strength and **Top 3 to work on** is one priority per boat, each with a **Next time:** line. Add a "best in the fleet by area" table and a head-to-head section. Use the fleet as the benchmark: when wind isn't measured, the fastest boat *is* the target.
6. **HTML:** `html_report.py <dir>/report/<boat> --debrief ... --overview ... --deep ... --title "<Boat> · <Event>"` for each boat, and `fleet.py ... --debrief <dir>/report/fleet-debrief.md --html` for `fleet.html`: gap charts, a **race replay** for every race (play or drag the slider; a panel beside the chart shows each boat's leg, tack, speed, heel, distance to the next mark and gap at the last mark at that moment), the **Starts** page (a replay of each start and each boat's start against the others), the **Current** page (the same as in each boat report, from every boat), the **Side by side** page (a course map of every stretch where it happened, filterable by race, beats or runs, and pair, with a list beside it: hover to find a stretch on the map, click to watch it on the replay; then each pair's totals, a chart and map of its stretches, and a table with Watch links). Each boat's own debrief and data stay in that boat's report, not the fleet page. In the fleet debrief, put an interactive chart next to the takeaway it proves with a line of its own: `[[chart:replay]]` (the race replay, with a button per race), `[[chart:pairmap]]` (the map of where the boats sailed side by side), `beats` (time lost per beat), `angles` (tacking angle by compass vs over the ground), `sides` (side of the course vs time lost), `heel` (upwind heel by beat), `exits` (speed out of the leeward marks) or `tacks` (tack recovery). One race's chart: `[[chart:wind:race4]]` (its wind trace, and where each boat was across the course) or `[[chart:marks:race5]]` (its close roundings, with a time slider). Put a bold one-line takeaway above each. Keep chart lines out of the What went well and Top 3 lists: place them after a list, not inside it.
7. **Tacks and gybes across the fleet:** `python scripts/maneuver_overlay.py --reports <dir>/report --kind tack --out <dir>/report/tacks.html --title "<Event> · Tacks"` (and `--kind gybe` for `gybes.html`). One page with every boat's comparable maneuvers, a boat filter, and the best 10% ranked across the fleet, so you can see whose tacks are the benchmark. Each boat's report already has its own on its Tacks and Gybes pages.

Checks that matter with several boats:

- **Say what the wind did.** Every fleet debrief gets a **What the wind did, and which side paid** section, race by race, from the Wind and sides tables: when a shift came (minutes into the leg), how big it was, where each boat was when it came, and whether being on that side paid, with what the shift alone was worth. Every boat debrief gets a **What the wind did, and where we were** section with the same facts from that boat's side, and a **Next time** that turns it into a rule the crew can use on the water. For example: a shift of 10° or more that holds for two or three minutes is the new wind, so sail toward it before tacking back. Say "no side paid" plainly when the wind was steady or oscillating, or when the boats were too close together for it to matter.
- **Current across the course.** The Current page splits the course into nine zones (left, middle and right of the rhumb line, 150 m either side being the middle, by bottom, middle and top thirds). Its default view is **VMC by zone**, on the beats or the runs: each boat against its own average in that race, split into the part the current explains and the rest (wind and sailing). Water running down the course, measured from upwind slip, takes that speed off VMC on a beat and adds it on a run. It also shows the current along and across the course, in knots against each boat's own average, with the noise. It leaves out a compass-suspect boat and has a race filter. Answer "did one side have better current?" from it, and say plainly when the difference is inside the noise. A side sailed early and one sailed late also differ by the tide, so check race by race.
- **Current: check it tack to tack.** The Current page also compares port with starboard on each leg for each boat. The SOG difference, turned into the cross-course current that would cause it, needs no compass at all. Real current shows on every boat, holds from a beat to the run after it and agrees with the drift. A tack that's faster on every boat, but flips on the runs and doesn't show in the drift, is the sea or the pressure, not current: coach it as a mode on that tack.
- **Usually only some of the fleet is tracked.** Say so in every report. Order, gaps, "fastest" and "which side paid" are among the tracked boats only, and never the race results. Write "first of the three tracked boats", not "won"; "ahead of Chomp", not "2nd". `fleet.py` labels its tables that way. If Kevin has the official results, put the real places alongside.
- **Don't trust Njord's fleet table blindly.** `fleetRaceInfo` depends on Njord detecting each rounding; for a boat without wind or maneuver events it can stop after the first leg, which silently re-ranks everyone. `fleet.py` takes mark passages from the course for every boat. Cross-check the finish order against the tracks.
- **Distances in metres and boat lengths.** Every distance gained or lost (to the mark, side by side, in a rounding or a tack, behind the line at the start, between boats at a mark) is given as both: "45 m (4.8 lengths)", or "m (lengths)" in a table. Lengths have one decimal under 10 and are whole above that. A boat length is `BOAT_LENGTH_M` in analyze.py (9.3 m, Etchells LOA); every report and chart takes it from there, and so does the mark zone (three lengths). Positions across the course ("320 m out on the left") and rates ("m a minute") stay in metres only.
- **Directions are magnetic.** Sailors steer and call shifts by the compass, so every absolute direction in the reports and the debrief (wind direction, headings, the course axis, current set) is magnetic. The scripts compute in true, because GPS geometry is true, and convert for display using each boat's own `Heading` − `Heading_Mag` (Njord makes true by adding the variation, about 10.7° E in San Diego). Say "mag" when you quote one. Angles between directions (tacking angle, angle to the wind, leeway, drift) are the same either way.
- **Compare angles over the ground, not by compass.** Heading sensors differ between boats (one can read 5–11° off: the Current page measures each boat's offset). The tacking angle from GPS course is compass-free; the gap between the heading angle and the COG angle is leeway, and big symmetric leeway (both tacks alike) suggests pinching.
- **Say which side paid by the boats, not by the shift calls.** Each boat's wind estimate comes from its own few tacks and they disagree. The fleet's gains by side are the firmer evidence.

## The three depths of a boat review

- **Quick look (`overview.md`)**, for crew who won't read further. Under 15 lines: the official result, two or three headline bullets (the story in numbers), **Keep doing** (2–3 bullets) and **Work on next** (three one-line priorities, each with the fix). No tables, no caveats beyond one line.
- **Debrief (`debrief.md`)**: the template below.
- **Deep dive (`deep-dive.md`)**: the nuances, for whoever wants to relive the races. Open with **Patterns across the regatta** (side-by-side totals, tacks, roundings, with the numbers), then one section per race going start → each beat → each mark → each run, with times from the gun so readers can find them on the fleet replay. Cite side-by-side stretches, shifts sailed headed, laylines and roundings. End with questions for the crew. Every number must come from the report files; say "looks like" for anything the data can't see (traffic, bad air, calls).

## Debrief template

Write in bullets, not paragraphs: a bold headline per point, facts as sub-bullets underneath (two-space indent under `-`, three under `1.`), one fact per bullet. The HTML report lifts each **What went well** headline and each **Top 3** headline with its **Next time:** line into a "Coach's summary" at the top of the Summary page. So make headlines self-contained (area plus the key number), keep each Next time to one or two sentences, and put drills on a separate **Drill:** bullet.

```
## [Event name] — [dates]
- [Conditions range]
- [Overall verdict in one line]
- [Where most of the time went]

**What went well**
- **[Headline with the number, e.g. "Upwind speed on the card (100–101% on Saturday)"]**
  - [Supporting fact]
  - [Supporting fact]

**Top 3 to work on**
1. **[Area: what the data shows, e.g. "Roundings: 563 m lost, mostly on the exit"]**
   - [Fact with number]
   - [Why it cost places/time]
   - **Next time:** [one concrete cue, one or two sentences]
   - **Drill:** [optional]
2. ...
3. ...

*Also worth a look:*
- **[Area]**
  - [Fact]

**Start scorecard**
| Race | Back at −60 s | Late (s) | Line pos | SOG at −30 s / gun | Accel (±5 s) | Last tack/gybe before gun |

**Upwind vs. targets**
[table from coach_calcs.py, trimmed to bands actually sailed]

**Race notes**
- **[Race]:** [the moment that decided it]
  - [Supporting fact]

**Data notes**
- [Speed source, missing channels, anything uncertain: one per bullet]

**Question** (or **Two questions**)
- [Something the data can't see]
```

End with one question about something the data can't see — rig settings, a crew call, what it felt like in a specific moment — because that's often where the real answer is.

## Judgment rules

- **Success is getting to the mark: VMC, and the time it takes.** Every comparison, ranking, "best", "what went well" and recommendation is judged on progress to the next mark, never on speed, heel or angle alone. In order of authority:
  1. **Time between marks** (leg times, gaps at each mark, "where the time went"): the final word.
  2. **VMC to the mark** (`vmc_avg`: the leg's distance to sail at the boats' tacking or gybing angle over its time; `vmc` second by second from `ladder.py`) and **metres gained or lost toward the mark** (side-by-side stretches, tacks, gybes, roundings).
  3. SOG, heel, trim, pointing angle, speed lost in a tack: **explanations only**. Use them to say *why* VMC was better or worse, never as the result. "Faster" is not a finding; "faster, and it got to the mark sooner" is.
  - A boat that's faster but lower, or that overstands, sails extra distance: only VMC says whether the speed paid. Say so when speed and VMC disagree.
  - A recommendation must name the VMC or time it would win back (e.g. "roundings cost 72 m toward the mark"), not only a speed or heel number.
  - Races without marks (legs detected from heel) can't give VMC: `progress` in `summary.json` is then `vmg_wind`. Say that gains there are along the wind, not to a mark.
- **Steady state only for targets.** Maneuvers, the first ~30 s after the start, and mark roundings aren't target sailing. `exclude_maneuvers` handles tacks; trim the first 30 s of each leg yourself.
- **The card is flat-water.** In chop, lower speed and wider TWA than the card are expected, so don't flag them as errors without noting sea state. Ask Kevin if you don't know.
- **Relative beats absolute.** Fleet ranks and time-to-leader show what actually cost places. A boat 2% under target that gained on the fleet was sailing well for the conditions.
- **Separate luck from execution.** A big loss from a shift on the wrong side is a strategy question; a steady loss in the same wind band is a speed question. Use `liftInfos` and leg ranks to tell them apart.
- **Consistency matters as much as averages.** A heel average on target with a big spread means the boat was being overpowered and depowered in cycles. `coach_calcs.py` reports heel std for that reason.
- **Say when the data is thin.** Under ~60 s in a wind band isn't a trend. Flag it rather than coach off it.

## Learning from a link

When Kevin shares a link or article to learn from: read it, distil the useful points in your own words into the matching `references/playbook/*.md` file (a sentence or two each, how it shows up in our data, and the source link), and log it under **Ingested** in `references/sources.md`. Don't copy text, and don't attribute coaching judgments to named people. If you can't reach the page, say so and add it to the **Inbox** there.

## References

- `references/etchells-targets.md` — the target card and starting-ratio table, with interpolation rules and caveats. Read before step 5.
- `references/coaching-framework.md` — the topic areas, what each looks like in data, and next-time cues. Read before step 6.
- `references/playbook/*.md` — racing principles distilled from outside sources, by topic, each tied to what the data shows. Read the relevant file during step 6.
- `references/sources.md` — the sources behind the playbook, plus an inbox of links still to read.
- `references/njord-queries.md` — tested GraphQL queries and `get_data` patterns. Read during steps 1–4.
