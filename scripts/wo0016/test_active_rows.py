"""Exact full-frame compatibility for the letterbox scan optimization."""
from pathlib import Path
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stream_counter import MaskCounter


def pair(**kwargs):
    options = dict(roi_mode="full", count_axis="x", guarded_merge=True,
                   component_cluster_gap=14, max_step=48, max_missing=3)
    options.update(kwargs)
    return MaskCounter(**options), MaskCounter(**options, active_rows=(20, 220))


def test_active_rows_preserve_every_result_and_auxiliary_state():
    baseline, cropped = pair()
    rng = np.random.default_rng(71)
    for frame_id in range(60):
        mask = np.zeros((240, 320), np.uint8)
        for _ in range(12):
            y, x = int(rng.integers(20, 213)), int(rng.integers(0, 310))
            mask[y:y+7, x:x+8] = rng.choice([0, 255], (7, 8)).astype(np.uint8)
        mask[20:24, 0:5] = 255
        mask[216:220, 315:320] = 255
        mask[70:105, 180:220] = 255  # rejected glare contours also need +20
        mask[130:138, 100+frame_id:108+frame_id] = 255
        assert baseline.update(frame_id, mask) == cropped.update(frame_id, mask)
        assert baseline.last_associations == cropped.last_associations
        assert baseline.rejected_detections == cropped.rejected_detections
        assert len(baseline.rejected_contours) == len(cropped.rejected_contours)
        for left, right in zip(baseline.rejected_contours, cropped.rejected_contours):
            np.testing.assert_array_equal(left, right)
        assert baseline._extract_roi_detections(mask) == cropped._extract_roi_detections(mask)


def test_native_projection_and_ccl_only_receive_200_rows(monkeypatch):
    _, cropped = pair()
    shapes = []
    reduce = cv2.reduce
    ccl = cv2.connectedComponentsWithStatsWithAlgorithm
    def reduction(image, *args, **kwargs):
        shapes.append(image.shape)
        return reduce(image, *args, **kwargs)
    def components(image, *args, **kwargs):
        shapes.append(image.shape)
        return ccl(image, *args, **kwargs)
    monkeypatch.setattr(cv2, "reduce", reduction)
    monkeypatch.setattr(cv2, "connectedComponentsWithStatsWithAlgorithm", components)
    mask = np.zeros((240, 320), np.uint8)
    mask[20:25, 70:75] = 255
    result = cropped.update(0, mask)
    assert shapes == [(200, 320)] * 3
    assert (result.roi_top, result.roi_bottom) == (0, 240)
    assert len(result.row_projection) == 240
    assert result.detections[0].y_min == 20


@pytest.mark.parametrize("value", [1, 255])
@pytest.mark.parametrize("row", [0, 235])
def test_unexpected_padding_is_never_silently_dropped(value, row):
    baseline, cropped = pair()
    mask = np.zeros((240, 320), np.uint8)
    mask[row:row+5, 20:25] = value
    if value == 1:
        for counter in (baseline, cropped):
            with pytest.raises(ValueError, match="Binary8"):
                counter.update(0, mask)
    else:
        assert baseline.update(0, mask) == cropped.update(0, mask)


@pytest.mark.parametrize("rows", [(0, 241), (20, 20), (-1, 220), (True, 220), (20.5, 220)])
def test_active_rows_validate_bounds(rows):
    with pytest.raises(ValueError, match="active_rows"):
        MaskCounter(roi_mode="full", active_rows=rows)


def test_lane_mode_rejects_active_rows():
    with pytest.raises(ValueError, match="active_rows"):
        MaskCounter(active_rows=(20, 220))


def test_only_letterbox_profile_enables_active_rows():
    import run_stream_counter as runner
    parser = runner._parser()
    legacy = runner.counting_configuration(parser.parse_args([]))
    mixed = runner.counting_configuration(parser.parse_args(["--counting-profile", "full-width-mixed"]))
    assert "active_rows" not in legacy
    assert mixed["active_rows"] == [20, 220]


def test_zero_mask_and_noncontiguous_views_preserve_results():
    baseline, cropped = pair()
    mask = np.zeros((240, 640), np.uint8)[:, ::2]
    assert baseline.update(0, mask) == cropped.update(0, mask)
    mask[90:97, 140:149] = 255
    assert baseline.update(1, mask) == cropped.update(1, mask)


def test_portable_ccl_fallback_preserves_global_geometry(monkeypatch):
    monkeypatch.setattr(cv2, "connectedComponentsWithStatsWithAlgorithm", None)
    baseline, cropped = pair()
    mask = np.zeros((240, 320), np.uint8)
    mask[100:107, 150:158] = 255
    mask[106, 157] = 0  # nonintegral centroid
    assert baseline.update(0, mask) == cropped.update(0, mask)


def test_sealed_300_frame_replay_matches_board_records_exactly():
    import importlib.util
    import json
    from capture_dataset import load_dataset
    from replay_active_rows10 import canonical
    root = Path(__file__).resolve().parents[2] / (
        "data/results/wo0017-physical-calibration-20261023/physical-calibration-20261023-10")
    if not (root / "capture/frame-records.jsonl").is_file():
        pytest.skip("sealed -10 raw capture is local-only; Git contains receipts")
    _, frames = load_dataset(root / "capture")
    config = frames[0].metadata["algorithm_config"]
    spec = importlib.util.spec_from_file_location(
        "sealed_counter10_test", root / "scripts/wo0016/stream_counter.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    original = module.MaskCounter(**config)
    cropped = MaskCounter(**config, active_rows=[20, 220])
    recorded = json.loads(next(root.glob("standby-reopen-*/frame-results.json")).read_text())
    assert len(frames) == len(recorded) == 300
    for frame, board_result in zip(frames, recorded):
        assert canonical(original.update(frame.frame_id, frame.mask)) == canonical(
            cropped.update(frame.frame_id, frame.mask)) == canonical(board_result)
        assert original.last_associations == cropped.last_associations
        assert canonical(original.rejected_detections) == canonical(cropped.rejected_detections)
        for left, right in zip(original.rejected_contours, cropped.rejected_contours):
            np.testing.assert_array_equal(left, right)
        assert len(original.rejected_contours) == len(cropped.rejected_contours)
    assert original.total_count == cropped.total_count == 12


def test_integrated_split_options_preserve_sealed_manifest_config_bridge():
    import inspect
    import json
    import run_stream_counter as runner
    import verify_touching_split_replay as replay

    path = Path(__file__).resolve().parents[2] / (
        "data/results/wo0017-physical-calibration-20261023/"
        "physical-calibration-20261023-10/capture/manifest.json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    recorded = {key: value for key, value in manifest["config"].items()
                if key in inspect.signature(MaskCounter).parameters}
    recorded["active_rows"] = [20, 220]
    current = runner.counting_configuration(runner._parser().parse_args([
        "--counting-profile", "full-width-mixed"]))
    assert current["max_split_area"] == 900
    assert replay._baseline_configuration(current) == recorded
