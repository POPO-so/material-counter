"""Single-variable A/B for the opt-in band_gate birth semantics.

Each scenario is a synthetic sequence of qualified detections with a known
truth count. Both runs use identical tracker settings except birth_semantics,
so any difference is attributable to the birth rule alone.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stream_counter import Detection, MaskCounter


SETTINGS = dict(width=320, height=240, count_axis="x", line_x=160,
                hysteresis=4, confirm_hits=2, max_missing=1,
                max_step=40, max_x_step=40)


SCENARIOS = (
    {"name": "late_birth_in_band", "truth": 1,
     "note": "real item first visible inside the hysteresis band",
     "frames": [[(154, 100)], [(166, 100)]]},
    {"name": "birth_inside_count_zone", "truth": 0,
     "note": "segmentation artifact appears past the line",
     "frames": [[(170, 100)], [(180, 100)]]},
    {"name": "duplicate_reentry", "truth": 1,
     "note": "item crosses, then a second blob appears past the line",
     "frames": [[(140, 100)], [(150, 100)], [(160, 100)], [(170, 100)],
                [], [(166, 100)]]},
    {"name": "in_band_flicker", "truth": 0,
     "note": "artifact oscillates inside the band and never leaves it",
     "frames": [[(154, 100)], [(156, 100)], [(154, 100)], [(156, 100)]]},
    {"name": "band_birth_then_forward", "truth": None,
     "note": "in-band birth wiggles back, then moves forward across; truth is "
             "ambiguous because the band rule cannot tell a late real item from "
             "a flicker that later recovers",
     "frames": [[(154, 100)], [(152, 100)], [(151, 100)], [(166, 100)]]},
    {"name": "two_late_items", "truth": 2,
     "note": "two real items first visible inside the band",
     "frames": [[(154, 100), (154, 140)], [(166, 100), (166, 140)]]},
    {"name": "fragment_after_crossing_in_band", "truth": 1,
     "note": "item crosses, then a fragment appears inside the band",
     "frames": [[(140, 100)], [(150, 100)], [(160, 100)], [(170, 100)],
                [], [], [(156, 100)], [(166, 100)]]},
)


def _box(x: int, y: int) -> Detection:
    return Detection(x, x + 9, y, y + 9, 100, x + 4.5, y + 4.5)


def run_scenario(frames, semantics: str) -> int:
    counter = MaskCounter(birth_semantics=semantics, **SETTINGS)
    for frame_id, boxes in enumerate(frames):
        counter.update_detections(frame_id, [_box(x, y) for x, y in boxes])
    return counter.total_count


def summarize() -> list[dict[str, object]]:
    rows = []
    for scenario in SCENARIOS:
        baseline = run_scenario(scenario["frames"], "baseline")
        candidate = run_scenario(scenario["frames"], "band_gate")
        truth = scenario["truth"]
        rows.append({
            "scenario": scenario["name"],
            "truth": truth,
            "baseline_count": baseline,
            "band_gate_count": candidate,
            "baseline_error": None if truth is None else baseline - truth,
            "band_gate_error": None if truth is None else candidate - truth,
            "note": scenario["note"],
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    rows = summarize()
    scored = [row for row in rows if row["truth"] is not None]
    totals = {
        "scored_scenarios": len(scored),
        "ambiguous_scenarios": len(rows) - len(scored),
        "baseline_abs_error": sum(abs(int(row["baseline_error"])) for row in scored),
        "band_gate_abs_error": sum(abs(int(row["band_gate_error"])) for row in scored),
    }
    payload = {"settings": SETTINGS, "scenarios": rows, "totals": totals}
    target = Path(arguments.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    for row in rows:
        truth = "?" if row["truth"] is None else row["truth"]
        print(f'{row["scenario"]:36s} truth={truth} '
              f'baseline={row["baseline_count"]} band_gate={row["band_gate_count"]}')
    print(f'absolute error: baseline={totals["baseline_abs_error"]} '
          f'band_gate={totals["band_gate_abs_error"]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())