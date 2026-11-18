"""The boat class the reports are for: hull length (distances are also given in boat lengths,
and the mark zone is three of them), how the class heels, and its name in the text.

Set SAIL_CLASS=J24 (or another key below) in the environment before running the scripts;
the default is Etchells, the class the skill was built for."""

from __future__ import annotations

import os

CLASSES = {
    "Etchells": {"name": "Etchells", "length_m": 9.3, "length_ft": "30.5", "beam_m": 2.12,
                 # ~15-25 deg upwind, ~2-6 deg downwind
                 "upwind_heel_deg": 9, "overlay_heel_deg": 10, "overlay_tack_heel": True, "stall_kt": 4.0, "has_targets": True, "start_standard": True},
    "J24": {"name": "J/24", "length_m": 7.32, "length_ft": "24", "beam_m": 2.71,
            # sailed flatter: ~8-20 deg upwind with the crew hiking, 0-5 deg downwind
            "upwind_heel_deg": 6, "overlay_heel_deg": 6,
            # sailed flat in the light near the top mark: tacks are told from roundings by the course
            # marks and the turn angle, not by heel; and a J/24 tacks at 3-4 kt in light air
            "overlay_tack_heel": False, "stall_kt": 3.0, "has_targets": False, "start_standard": False},
}

KEY = os.environ.get("SAIL_CLASS", "Etchells").replace("/", "")
if KEY not in CLASSES:
    raise SystemExit(f"SAIL_CLASS={KEY!r}: known classes are {', '.join(CLASSES)}")
CLS = CLASSES[KEY]
NAME = CLS["name"]
LENGTH_M = CLS["length_m"]
ZONE_M = 3 * LENGTH_M


def js() -> str:
    """A script tag giving the page's charts the class (read by charts.js, replay3d.js)."""
    return (f"<script>window.BOAT_CLASS={{name:{NAME!r},length_m:{LENGTH_M},beam_m:{CLS['beam_m']}}};"
            f"window.BOAT_LENGTH_M={LENGTH_M};</script>")
