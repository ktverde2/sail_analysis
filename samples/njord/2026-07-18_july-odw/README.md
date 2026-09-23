# July ODW, 2026-07-18, San Diego: Mojo (Etchells)

Three races exported from Njord at 1 Hz, from 7 min before each gun to Njord's race end. Used by
`tests/test_analyze.py` to cover courses **without marks**: each course has only a start line and a
finish line on the same spot, so Njord's `Leg` column is a single leg and its `VMC` points at the
finish. `analyze.py` detects upwind/downwind legs from heel instead.

Also note: Njord's race 1 end (19:44Z) is 10 minutes after the boat actually finished (19:34Z).
Logged wind is filler here too (TWS exactly 6 kt for much of the day).
