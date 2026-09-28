#!/usr/bin/env python3
"""Pull an event's official results from YachtScoring (the scoring system SDYC uses).

Usage:
  python yachtscoring.py <event id> --out official.json

The event id is the number in the results URL
(https://www.yachtscoring.com/event_results_cumulative/<id>). Writes every boat's points per race,
finish status, net total and overall place, plus each race's start time, so fleet.py can put the
tracked boats in context of the whole fleet.

Only places and points are kept. YachtScoring's finish and elapsed times can be data-entry
artifacts (at PCC 2026 some "finishes" are the next morning, and others fall after the next
race's start), so gaps between boats come from the GPS tracks, not from here.

Needs network access to api.yachtscoring.com. No third-party packages.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

API = "https://api.yachtscoring.com/v1/public/event"


def get(path: str) -> dict:
    req = urllib.request.Request(f"{API}/{path}", headers={"User-Agent": "sail-analysis"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch(event_id: int) -> dict:
    event = get(str(event_id))
    races = get(f"{event_id}/races").get("rows", [])
    cum = get(f"{event_id}/cumulative-result")
    boats = []
    for circle in cum["data"]:
        for div in circle["divisions"]:
            for cls in div["classes"]:
                for place, b in enumerate(cls["boats"], 1):  # listed in finishing order
                    recs = {r["raceNumber"]: r for r in b["records"]}
                    owner = b.get("owner") or {}
                    boats.append(
                        {
                            "class": cls["className"],
                            "overall_place": place,
                            "sail": b["sailNumber"].replace("USA", "").strip(),
                            "name": b["name"].strip(),
                            "skipper": " ".join(
                                x.strip(" /")
                                for x in (owner.get("firstName"), owner.get("lastName"))
                                if x
                            ),
                            "corinthian": bool(b.get("isCorinthianTeam")),
                            "points": [
                                recs[n]["raceValue"] if n in recs else None
                                for n in range(1, len(races) + 1)
                            ],
                            "status": [
                                recs[n]["finishStatus"] if n in recs else None
                                for n in range(1, len(races) + 1)
                            ],
                            "net": b.get("raceTotal"),
                            "drops": [
                                b[f"throwOut{k}"] for k in range(1, 8) if b.get(f"throwOut{k}")
                            ],
                        }
                    )
    return {
        "event": event.get("name", "").strip(),
        "event_id": event_id,
        "source": f"https://www.yachtscoring.com/event_results_cumulative/{event_id}",
        "races": [
            # YachtScoring labels local times with "Z"; keep them as local wall-clock times
            {"number": r["raceNumber"], "start_local": r["startTime"][:19].replace("T", " ")}
            for r in sorted(races, key=lambda r: r["raceNumber"])
        ],
        "boats": boats,
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("event_id", type=int)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    data = fetch(a.event_id)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(data, indent=1))
    print(f"{data['event']}: {len(data['boats'])} boats, {len(data['races'])} races -> {a.out}")


if __name__ == "__main__":
    main()
