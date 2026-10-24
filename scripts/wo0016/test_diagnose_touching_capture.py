import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnose_touching_capture import analyze_frames


def test_two_close_independent_nuts_are_lost_by_proximity_cluster():
    frames = []
    for frame_id, x in enumerate(range(80, 216, 15)):
        mask = np.zeros((240, 320), dtype=np.uint8)
        mask[110:120, x:x + 8] = 255
        mask[110:120, x + 15:x + 23] = 255
        frames.append((frame_id, mask))
    config = {
        "roi_mode": "full", "count_axis": "x", "line_x": 160,
        "guarded_merge": True, "single_intact_area": 95,
        "max_merge_area": 260, "max_merged_span": 24,
        "component_cluster_gap": 14, "max_step": 48,
        "max_lateral_step": 10, "strict_lateral_gate": True,
    }

    report = analyze_frames(frames, config)

    assert report["ground_truth_status"] == "NOT_VERIFIED"
    assert report["baseline_total"] == 1
    assert report["no_gap_total"] == 2
    assert report["proximity_merge_frames"] == list(range(len(frames)))
    assert all(row["qualified_binary8_components"] == 2 for row in report["frames"])


def test_one_intact_nut_does_not_gain_an_event_without_proximity_cluster():
    frames = []
    for frame_id, x in enumerate(range(80, 216, 15)):
        mask = np.zeros((240, 320), dtype=np.uint8)
        mask[110:120, x:x + 8] = 255
        frames.append((frame_id, mask))
    report = analyze_frames(frames, {
        "roi_mode": "full", "count_axis": "x", "line_x": 160,
        "guarded_merge": True, "component_cluster_gap": 14,
        "max_step": 48,
    })

    assert report["baseline_total"] == report["no_gap_total"] == 1
    assert report["proximity_merge_frames"] == []
