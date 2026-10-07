# Coaching framework

The topic areas below follow the published curriculum of Steve Hunt's ASA online classes *Need for Speed* and *Own the Line*. Only the topic list comes from that source. How each topic maps to data, and the cues suggested, are this skill's own reasoning. Don't present any of it as Hunt's advice.

Each section gives: what to look at in the data, what good and bad look like, and next-time cues to pick from. Cues should be one sentence a crew member can remember on the water.

## Starting (Own the Line)

### Timing
- **Data:** `belowLineGun` (m), `sogGun`, `lineCross` (s after gun). Convert meters to seconds with `coach_calcs.py tod`.
- **Good:** within ~1 boat length and at speed at the gun; `lineCross` under ~3 s.
- **Flags:** consistently late by the same amount means a timing habit, not bad luck. Negative `lineCross` means OCS risk.
- **Cues:** Call time-to-line out loud from 30 s. Build the approach backward from the gun using the starting-ratio card.

### Acceleration and boat handling
- **Data:** `sogGunMinus5Relative` and `sogGunPlus5Relative` (ratio vs. SOG at gun), `lastManeuverSec`, `lastManeuverTimeToKill`, pre-start SOG trace.
- **Good:** SOG rising through the gun (minus5 < 1 < plus5), a final maneuver early enough to be settled.
- **Flags:** a final tack or gybe inside ~30 s with lots of time to kill left means too much time burned too late. Flat or falling SOG at the gun means the trigger was pulled late.
- **Cues:** Pick the "go" moment by the card, not by feel. Trim on hard at go; don't let the jib lag.

### Line position and lane
- **Data:** `crossLineDistToStbdRelative` (0 = RC end, 1 = pin), `distanceCrossToBiasPointRelative`, `laneXteMax`, fleet `lineCrossRank`.
- **Good:** near the favored end or deliberately in a clear lane; low lane XTE.
- **Flags:** far from the bias point with no tactical reason; large XTE (got squeezed or had to bail).
- **Cues:** Decide the end by 3 min and commit. Protect the hole to leeward.

### Recovery
- **Data:** start rank vs. first-mark rank (`legsData[0]...legEndTimeRank`).
- **Look for:** did a poor start get worse (stuck in bad air) or better (clean escape)? That separates the start from what happened after it.

## Speed (Need for Speed)

### Boat setup and sail trim
- **Data:** not directly visible unless load or trim channels exist. Speed vs. target by wind band is the proxy.
- **Ask Kevin:** rig and sail settings for each race. Correlate settings with bands where speed was on or off target.

### Angle to wind
- **Data:** TWA delta vs. target by band (`coach_calcs.py`).
- **Flags:** wide and slow = pinched-then-footed cycles or poor groove; narrow and slow = pinching.
- **Cues:** Name the mode out loud: "high," "normal," or "fast." Helm steers to the heel and speed numbers, not the telltales alone, when it's windy.

### Steering
- **Data:** `Rudder`/`ROT` if a tiller logger or rudder sensor is present; otherwise TWA std within a band.
- **Flags:** high rudder activity with no speed gain; TWA std well above other bands.
- **Cues:** Smaller tiller movements; steer through puffs by feathering, not by stalling.

### Heel angle and stability
- **Data:** heel delta vs. target and heel std by band.
- **Flags:** over target in breeze (overpowered, more leeway); under target in light air (underpowered or crew weight wrong); big std (power cycling in and out).
- **Cues:** Mainsheet and traveler hold the heel number. Crew moves before the puff arrives, not after.

### Telltale vs. heel-angle sailing
- **Data:** compare TWA std against heel std in each band. In light air, TWA should be steady; in breeze, heel should be.
- **Cue:** Below roughly 10 kts, sail to the telltales. Above that, the helm should sail to heel.

### Waves
- **Data:** can't be measured directly; `Trim` (pitch) std can hint at it if present.
- **Ask Kevin** about sea state before judging speed in chop against the flat-water card.

### Moding
- **Data:** TWA spread within a band, and whether a leg's tactical situation (lane, laylines) called for high or low mode.
- **Good:** a deliberate mode shift tied to a tactical need.
- **Flag:** random spread with no tactical story.

### Communication, focus, and crew technique
- **Data proxy for focus:** speed % of target in the first vs. last third of each upwind leg, and across races through the day. A late-day drop in consistency points to fatigue or focus, not technique.
- **Ask Kevin:** what the calls were and who made them. Communication is invisible in data but often explains the patterns above.

## Debrief habits

- Keep it collaborative: pose one question the data can't answer, and invite the crew's read before settling on a conclusion.
- Separate outcome from execution. A bad result from a good process is still a good race to learn from, and vice versa.
- Make the next practice obvious: each priority should map to a drill or a cue the team can use in the next session.
