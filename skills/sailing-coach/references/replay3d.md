# 3D replay (`scripts/replay3d.py`)

A self-contained HTML page that replays every tracked boat in 3D, in the style of RaceQs or Vakaros RaceSense: follow one boat from behind, orbit and zoom around it, pan away to see the others, or pull back to the whole course. No coaching, just the tracks.

```
python3 scripts/replay3d.py --data <dir>/data --report <dir>/report --out <dir>/replay/<Event>_3d_replay.html [--title "..."] [--terrain grid.npz]
```

- **Inputs.** `data/<boat>/raceN.csv` and `raceN-race.json` (course marks, gun) from the Njord fetch or `vkx_report.py`; `report/<boat>/raceN/summary.json` from `analyze.py` (legs, so each boat's leg and the spinnaker); `report/fleet.json` from `fleet.py` when it exists (boat colours and order, wind direction every minute, each leg's ladder axis and layline angle). Without fleet.json the wind is each leg's estimate.
- **What's measured.** Position (1 s, interpolated between samples), SOG, heel and fore-aft trim. The hull points along COG when moving (above 2.5 kt) and along the compass, corrected by its median offset from COG, when slow, so a compass that's off doesn't twist the boat. Wind direction comes from the boats' tacking angles (fleet.json), not a sensor. Directions are shown magnetic.
- **What's drawn, not measured.** How far the sails are eased (from the true wind angle and an assumed 10 kt), and when the spinnaker is up (on a downwind leg, more than 100° off the wind, away from the marks). The wind streaks on the water only show direction. The page says so in its footnote.
- **Cards.** Each boat's SOG, heading, heel, leg and metres to go up or down the ladder to the leg's mark, the gap to the leader on the same leg, and before the gun the distance below the line.
- **Controls.** Drag to orbit, right-drag or two fingers to pan, scroll or pinch to zoom, click a boat (label or card) to follow it, double-click to recentre. Cameras: Behind (turns with the boat), Steady, Course (whole course looking upwind) and Top. Trail length, labels, the followed boat's ladder and laylines, wind streaks and true-size boats are toggles (by default boats grow when you zoom out so they stay visible). Keys: space, ←/→ (shift: a minute), 1–9 follow a boat, c, t, b, s.
- **Shoreline and depth.** When the course lies inside a grid in `scripts/terrain/` (or `--terrain`), the land is drawn as a low surface and the water is tinted by depth. `terrain/san-diego-bay.npz` covers San Diego Bay: NOAA BlueTopo at 4 m, block-averaged to 20 m (`land` = fraction of no-data cells, 0–255; `depth_q` = depth × 4 in metres; `E0`, `N1` = UTM corner, `dx`, `zone`). Another venue's grid in the same format can be added there. The land has no heights.
- **Sharing.** One file, about 2 MB: three.js r160 (`scripts/vendor/three.min.js`, MIT) is inlined and all the data is in one JSON block (as the reports are, for Google Drive's virus scan). Needs WebGL, which any recent desktop or phone browser has.
