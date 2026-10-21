from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay_recorded_components


REPO_ROOT = Path(__file__).resolve().parents[2]
ATTEMPT5 = REPO_ROOT / "data/results/wo0016-stageB2-attempt5-prep-20261021/attempt-5-recovery-2-sample-20261021/attempt-5-recovery-2"


def test_default_replay_matches_attempt5_official_recorded_output(tmp_path, monkeypatch):
    config_path = REPO_ROOT / "data/results/wo0016-stageB2-attempt5-prep-20261021/counting-config.json"
    official_config = json.loads(config_path.read_text(encoding="utf-8"))
    recorded_frames = json.loads((ATTEMPT5 / "frame-results.json").read_text(encoding="utf-8"))
    official_result = json.loads((ATTEMPT5 / "attempt-5-recovery-2-result.json").read_text(encoding="utf-8"))

    output = tmp_path / "attempt5-replay"
    monkeypatch.setattr(sys, "argv", [
        "replay_recorded_components.py", str(ATTEMPT5 / "frame-results.json"), str(output)
    ])
    replay_recorded_components.main()
    replayed = json.loads((output / "replay.json").read_text(encoding="utf-8"))

    assert replay_recorded_components.DEFAULT_COUNTING_CONFIG == official_config
    assert replayed["configuration"] == official_config
    assert replayed["frames_processed"] == official_result["frames_processed"] == 165
    assert replayed["replayed_total_count"] == official_result["total_count"] == 4
    assert replayed["board_accessed"] is False
    assert [row["total_count"] for row in replayed["frame_totals"]] == [
        frame["total_count"] for frame in recorded_frames
    ]
    assert replayed["events"] == [
        event for frame in recorded_frames for event in frame["events"]
    ]
