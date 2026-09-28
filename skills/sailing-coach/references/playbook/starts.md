# Starts

Why it matters more in an Etchells: the hull is long and heavy, so a boat that isn't at full speed at the gun falls off the front row and rarely gets it back. A clean start with a lane held for the first few minutes usually means a good first mark. [N1]

## What a good start is
- Front row, at full speed, in clear air, able to take the first shift. One boat sitting on the weather hip takes away the option to tack on the first header, which wastes an otherwise good start. [S2]
- **Data:** `late (s)`, `SOG at gun`, `accel ±5 s` (positive = accelerating through the gun), line position. Good: under ~2 s late, SOG near upwind target, positive acceleration.

## Time on distance
- Know how long the boat takes to reach full speed from a slow luff in each wind range, and build the approach backward from the gun: distance ÷ speed, minus the acceleration time. [S1, S3]
- Take line sights (a shore transit through the pin) at safe distances of four to five, three and two lengths back, not only right on the line, because the late sights get blocked when the fleet lines up. GPS pings of both ends help the same way. [S2]
- The bow calls distance to the line; one person calls time. A good line sight plus a clear time call is worth more than anything else in the last minute. [N2]
- **Data:** metres behind the line at −60/−30/0 s and seconds late. A consistent lateness race to race is a timing habit, not bad luck. `coach_calcs.py tod` converts metres to seconds.
- **Cue:** "Go point by the card, not by feel." Drill: repeated timed runs at a buoy, from a stop to full speed, until the crew knows the number for today's wind. [N1, S1]

## Final approach
- Etchells don't turn quickly (rudder on the skeg). Avoid big bear-aways on starboard in the last ~45 s; hold a close-hauled-ish angle with the jib eased or half-luffing and let the helm find the build angle in the last ~15 s. [N2]
- Wide, slow turns in the prestart keep speed; a full circle can take ~25 s in light air. Drone or track review of turn rates is a good debrief tool. [N1]
- Protect a hole to leeward: it's the runway for accelerating in first gear (slightly below close-hauled) in the last 5–10 s. If too early, slow by heading up toward head-to-wind rather than bearing off, which eats the hole. [S2, S3]
- Start accelerating before, or at the latest with, the boats around you. Sheet in progressively as speed builds; trimming hard without speed just slides the boat sideways. [S3]
- **Data:** `last tack/gybe before gun`, prestart maneuver count in the last 5 min, SOG trace from −30 s. A final maneuver inside ~30 s, or SOG falling into the gun, points here.

## Acceleration off the line
- For the first minute boatspeed is king: foot slightly and don't pinch until the boat has gone through the gears. Full close-hauled trim too soon loads the boat up and it slips sideways. [S2]
- With the bow down it can take up to a minute to reach full speed; once there, the keel starts working and the boat can point. [N2]
- **Data:** SOG at +10/+30 s vs. target; VMG in the first minute (excluded from steady-state targets, so look at it directly).

## Line bias vs. favored side
- The end that is more upwind is favored. Check it head-to-wind mid-line or, more accurately, by compass: line bearing vs. head-to-wind bearing. Re-check through the sequence; the bias moves with the shifts. [S2]
- Short line or bias under ~5° → start near the middle and keep both sides. Big fleet or bias of 15–20° → start close to the favored end but a third of the way down, not in the pile-up at the end. [S2]
- When the favored end and the favored side disagree: on a short beat treat the bias as the first shift and take it; on a longer beat, favor getting to the side. [S4]
- Big-fleet experience: leverage-seeking starts at the ends produced big scores; starting near the middle where the line was less crowded, and working to the favored side up the first beat, was more consistent. [N3, N2]
- **Data:** line position (0 = pin, 100% = boat end) against the side we sailed on the first beat (`% right of rhumb`) and the beat's wind trend. Starting at the boat end and then fighting to go left, or vice versa, is worth a note.

## Plan B
- Know the bail-out before the sequence starts: if the lane closes with ~20 s to go, get onto port behind the row early and duck to a clear lane toward the favored side, rather than sitting in bad air. [S3]

Sources: [N1] North Sails, Etchells speed guide · [N2] North Sails, Etchells speedy tips · [N3] North Sails, Etchells speed reading (2018 Worlds) · [S1] SailZing, execute the start · [S2] Sailing World, starts done right · [S3] Sailmon, advanced start strategies · [S4] Sailing World, when line bias and course favor disagree. Links in `../sources.md`.
