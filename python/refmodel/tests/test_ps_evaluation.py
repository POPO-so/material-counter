"""Contract tests for the isolated H3 reference evaluator."""

import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[2]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))


def _api():
    module_name = "refmodel.ps_reference.evaluate_h3"
    if importlib.util.find_spec(module_name) is None:
        pytest.fail("H3 evaluation contract is not implemented yet")
    from refmodel.ps_reference import evaluate_h3

    return evaluate_h3


def test_truth_visibility_is_per_object():
    api = _api()
    frames = []
    for fid, x in enumerate([95, 99, 100, 103, 104]):
        frames.append({'frame_id': fid, 'objects': [
            _object(1, x, frame_id=fid, visible=(fid != 3)),
            _object(2, x, frame_id=fid, visible=True),
        ]})
    truth = api.derive_ground_truth_events(frames, line_x=100, hysteresis=2)
    assert truth['allowed_observation_frames'] == {1: 4, 2: 3}


def _object(object_id, center_x, center_y=11.5, visible=True, frame_id=0):
    x_min = int(center_x) - 3
    y_min = int(center_y) - 3
    return {
        "frame_id": frame_id,
        "object_id": object_id,
        "x_min": x_min,
        "x_max": x_min + 6,
        "y_min": y_min,
        "y_max": y_min + 6,
        "centroid_x": float(center_x),
        "centroid_y": float(center_y),
        "speed_px_per_frame": 1.0,
        "visible": visible,
    }


def _truth_frames():
    positions = {
        1: [95.0, 99.0, 100.0, 103.0, 104.0],
        2: [96.0, 99.0, 100.0, 101.0, 101.0],
        3: [105.0, 106.0, 107.0, 108.0, 109.0],
    }
    frames = []
    for frame_id in range(5):
        visible = frame_id != 3
        frames.append({
            "frame_id": frame_id,
            "objects": [
                _object(object_id, xs[frame_id], visible=visible, frame_id=frame_id)
                for object_id, xs in positions.items()
            ],
        })
    return frames


def _write_mini_dataset(root: Path) -> Path:
    dataset = root / "dataset"
    condition = dataset / "mini"
    frames_dir = condition / "frames"
    frames_dir.mkdir(parents=True)
    dropped = [1]
    frame_xmins = [4, None, 8, 10, 13]
    gt_frames = []
    for frame_id, x_min in enumerate(frame_xmins):
        visible = x_min is not None
        objects = []
        if visible:
            objects.append({
                "frame_id": frame_id,
                "object_id": 0,
                "x_min": x_min,
                "x_max": x_min + 3,
                "y_min": 10,
                "y_max": 13,
                "centroid_x": x_min + 1.5,
                "centroid_y": 11.5,
                "speed_px_per_frame": 1.0,
                "visible": True,
            })
            gray = np.full((24, 32), 24, dtype=np.uint8)
            gray[10:14, x_min:x_min + 4] = 220
            Image.fromarray(gray, mode="L").save(frames_dir / f"frame_{frame_id:04d}.png")
        else:
            objects.append(_object(0, 7.5, visible=False, frame_id=frame_id))
        gt_frames.append({"frame_id": frame_id, "objects": objects})

    sequence = {
        "sequence_id": "sequence_000",
        "condition_id": "mini",
        "width": 32,
        "height": 24,
        "num_frames": len(frame_xmins),
        "fps": 30,
        "physical_object_count": 1,
        "object_width": 4,
        "object_height": 4,
        "speed_px_per_frame": 1.0,
        "speed_profile": [],
        "spacing_px": 14.0,
        "requested_drop_frame_ids": dropped,
        "dropped_frame_ids": dropped,
    }
    config = {
        "schema_version": 1,
        "dataset_id": "mini_dataset",
        "id": "mini",
        "condition_id": "mini",
        "dropped_frame_ids": dropped,
        "sequences": [sequence],
    }
    (condition / "config.json").write_text(json.dumps(config), encoding="utf-8")
    truth = {
        "schema_version": 1,
        "dataset_id": "mini_dataset",
        "condition_id": "mini",
        "sequence_id": "sequence_000",
        "frame_width": 32,
        "frame_height": 24,
        "num_frames": len(frame_xmins),
        "physical_object_count": 1,
        "frames": gt_frames,
    }
    (condition / "ground_truth.json").write_text(json.dumps(truth), encoding="utf-8")
    root_config = {
        "schema_version": 1,
        "dataset_id": "mini_dataset",
        "canvas": {"width": 32, "height": 24},
        "conditions": [config],
    }
    (dataset / "config.json").write_text(json.dumps(root_config), encoding="utf-8")
    return dataset


def test_gt_truth_counts_center_crossings_qualified_events_and_pending_tracks():
    api = _api()

    truth = api.derive_ground_truth_events(_truth_frames(), line_x=100, hysteresis=2)

    assert truth["center_events"] == {1: 2, 2: 2}
    assert truth["qualified_events"] == {1: 3}
    assert truth["allowed_observation_frames"] == {1: 4}
    assert truth["pending_object_ids"] == [2]


def test_equal_raw_total_does_not_replace_per_object_event_matching():
    api = _api()
    truth = {
        "center_events": {1: 2, 2: 4},
        "qualified_events": {1: 3, 2: 5},
        "allowed_observation_frames": {1: 3, 2: 5},
        "pending_object_ids": [],
    }
    predicted = [
        {"object_id": 1, "frame_id": 3},
        {"object_id": 1, "frame_id": 5},
    ]

    score = api.score_count_events(truth, predicted)

    assert score["predicted_event_count"] == score["qualified_truth_count"] == 2
    assert score["event_tp"] == 1
    assert score["event_fn"] == 1
    assert score["duplicate_fp"] == 1
    assert score["spurious_fp"] == 0


def test_gt_geometry_adapter_returns_only_regions_and_shuffles_deterministically():
    api = _api()
    objects = [
        _object(1, 20.0),
        _object(2, 40.0),
        _object(3, 60.0),
    ]

    first = api.regions_from_gt_objects(objects, seed=123)
    second = api.regions_from_gt_objects(objects, seed=123)

    assert first == second
    assert sorted(region.x_min for region in first) == sorted(obj["x_min"] for obj in objects)
    assert [region.x_min for region in first] != [obj["x_min"] for obj in objects]
    assert all(not hasattr(region, "object_id") for region in first)
    assert all(not hasattr(region, "visible") for region in first)


def test_dataset_evaluation_records_global_missing_frame_and_is_deterministic(tmp_path):
    api = _api()
    dataset = _write_mini_dataset(tmp_path)

    first = api.evaluate_dataset(dataset, tmp_path / "run1", line_x=12, hysteresis=2)
    second = api.evaluate_dataset(dataset, tmp_path / "run2", line_x=12, hysteresis=2)

    assert first["sequence_count"] == 1
    assert first["modes"] == ["image", "gt_geometry"]
    assert first["trace_sha256"] == second["trace_sha256"]
    assert first["input_sha256"] == second["input_sha256"]
    assert first["input_unchanged_after_run"] is True
    for mode in first["modes"]:
        trace_path = tmp_path / "run1" / "trace" / mode / "mini.json"
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        missing = trace["frames"][1]
        assert missing["observation_present"] is False
        assert missing["regions"] == []
        assert missing["feature_metrics"] == {"feature_tp": 0, "feature_fn": 0, "feature_fp": 0}
        assert missing["events"] == []
        assert first["condition_summaries"][mode]["mini"]["missing_frames"] == 1


def test_evaluator_never_passes_ground_truth_records_to_tracker(tmp_path, monkeypatch):
    api = _api()
    dataset = _write_mini_dataset(tmp_path)
    original_update = api.Tracker.update
    update_calls = []

    def guarded_update(tracker, frame_id, detections):
        detections = list(detections)
        assert all(isinstance(region, api.Region) for region in detections)
        assert all(not hasattr(region, "object_id") and not hasattr(region, "visible")
                   for region in detections)
        update_calls.append((frame_id, len(detections)))
        return original_update(tracker, frame_id, detections)

    monkeypatch.setattr(api.Tracker, "update", guarded_update)
    api.evaluate_dataset(dataset, tmp_path / "guarded", line_x=12, hysteresis=2)

    assert len(update_calls) == 2 * 5
    assert update_calls[1] == (1, 0)
    assert update_calls[6] == (1, 0)


def test_evaluator_rejects_missing_visible_png_instead_of_masking_it(tmp_path):
    api = _api()
    dataset = _write_mini_dataset(tmp_path)
    (dataset / "mini" / "frames" / "frame_0002.png").unlink()

    with pytest.raises(ValueError, match="frame|PNG|missing"):
        api.evaluate_dataset(dataset, tmp_path / "rejected", line_x=12, hysteresis=2)

    assert not (tmp_path / "rejected").exists()


def test_actual_h3_independent_truth_totals_are_35_31_and_four_pending():
    api = _api()
    data = Path(__file__).resolve().parents[3] / "data" / "synthetic" / "h3_full"
    root_config = json.loads((data / "config.json").read_text(encoding="utf-8"))
    center_count = qualified_count = pending_count = 0

    for item in root_config["conditions"]:
        truth_path = data / item["id"] / "ground_truth.json"
        gt = json.loads(truth_path.read_text(encoding="utf-8"))
        truth = api.derive_ground_truth_events(gt["frames"], line_x=160, hysteresis=2)
        center_count += len(truth["center_events"])
        qualified_count += len(truth["qualified_events"])
        pending_count += len(truth["pending_object_ids"])

    assert (center_count, qualified_count, pending_count) == (35, 31, 4)
