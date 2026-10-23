"""Offline integration of full-field mapping and guarded mixed-row profile."""
import asyncio
from pathlib import Path
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import live_calibration as calibration
import run_stream_counter as runner


def test_letterbox_preserves_full_source_extent_and_uses_black_padding():
    raw = np.arange(400, dtype=np.uint16)[:, None] % 256
    raw = np.broadcast_to(raw.astype(np.uint8), (400, 640)).copy()
    raw[:, 320:322] = 255
    processor = calibration.MonochromePreprocessor(mapping="letterbox")
    gray, binary = processor(raw)
    assert gray.shape == binary.shape == (240, 320)
    np.testing.assert_array_equal(gray[20:220], raw[::2, ::2])
    assert not gray[:20].any() and not gray[220:].any()
    assert np.all(gray[20:220, 160] == 255)
    np.testing.assert_array_equal(binary, np.where(gray > 128, 255, 0).astype(np.uint8))
    gray[:20] = 255
    gray2, _ = processor(np.zeros((400, 640), np.uint8))
    assert gray2 is gray and not gray2.any()


def test_mapping_metadata_does_not_claim_unmeasured_180mm_calibration():
    geometry = calibration.frame_geometry("letterbox")
    assert geometry["source_roi_xyxy"] == [0, 0, 640, 400]
    assert geometry["active_output_roi_xyxy"] == [0, 20, 320, 220]
    assert geometry["physical_calibration_status"] == "NOT_MEASURED"
    assert geometry["pixels_per_mm_y"] is None
    with pytest.raises(ValueError):
        calibration.frame_geometry("crop_center")


def test_full_width_profile_binds_guards_full_roi_and_strict_directional_tracking():
    args = runner._parser().parse_args(["--counting-profile", "full-width-mixed"])
    config = runner.counting_configuration(args)
    assert {k: config[k] for k in ("roi_mode", "guarded_merge", "single_intact_area",
                                   "max_merge_area", "max_merged_span", "strict_lateral_gate")} == {
        "roi_mode": "full", "guarded_merge": True, "single_intact_area": 95,
        "max_merge_area": 260, "max_merged_span": 24, "strict_lateral_gate": True}
    assert config["count_axis"] == "x" and config["line_x"] == 160
    assert config["component_cluster_gap"] == 14
    assert config["max_step"] == 48 and config["max_lateral_step"] == 10
    assert config["max_missing"] == 3 and config["cooldown_frames"] == 0
    assert runner.preprocessing_configuration(args)["mapping"] == "letterbox"
    legacy = runner.counting_configuration(runner._parser().parse_args([]))
    assert "guarded_merge" not in legacy and legacy["component_cluster_gap"] == 16


def test_letterbox_camera_prefetch_does_not_overwrite_previous_frame():
    class Capture:
        def __init__(self):
            self.props, self.reads, self.released = {}, 0, False
        def isOpened(self): return True
        def set(self, key, value): self.props[key] = value; return True
        def get(self, key): return self.props[key]
        def read(self):
            self.reads += 1
            return True, np.full((400, 640, 3), self.reads, np.uint8)
        def release(self): self.released = True
    capture = Capture()
    class FakeCV:
        def __getattr__(self, key): return getattr(cv2, key)
        def VideoCapture(self, *args): return capture
    metadata = {}
    mapping_timings = []
    async def consume():
        stream = runner._camera_gray_frames_impl(
            "fake", max_frames=2, cv2_module=FakeCV(), frame_mapping="letterbox",
            capture_metadata=metadata, mapping_timings_ms=mapping_timings)
        first = await anext(stream)
        await asyncio.sleep(.02)
        second = await anext(stream)
        assert not np.shares_memory(first, second)
        assert np.all(first[20:220] == 3) and np.all(second[20:220] == 4)
        assert not first[:20].any() and not second[220:].any()
        await stream.aclose()
    asyncio.run(consume())
    assert capture.released
    assert metadata["frame_geometry"]["mapping"] == "letterbox"
    assert len(mapping_timings) == 2 and all(value >= 0 for value in mapping_timings)


@pytest.mark.parametrize("direction", ["1", "-1"])
def test_mock_cli_exercises_selected_full_width_profile(capsys, direction):
    assert runner.main(["--counting-profile", "full-width-mixed", "--mock-frames", "8",
                        "--direction", direction]) == 0
    import json
    result = json.loads(capsys.readouterr().out)
    assert result["counting_profile"] == "full-width-mixed"
    assert result["total_count"] == 5 and result["board_accessed"] is False
    assert result["counting_config"]["roi_mode"] == "full"


def test_formal_ready_identity_binds_geometry_and_guards(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import wo0017_capture_profile
    monkeypatch.setattr(wo0017_capture_profile, "validate_stage1_receipt",
                        lambda *args: {"status": "OVERLAY_STAGE1_PASS"})
    boot = "35983d66-bb1d-4a8a-85e5-802170825515"
    args = runner._parser().parse_args([
        "--mode", "board", "--wo0017-profile", "b1", "--no-consume-guard",
        "--attempt-id", "offline-test", "--capture-dir", str(tmp_path),
        "--expected-boot-id", boot, "--stage1-receipt", "offline-receipt.json",
        "--stage1-receipt-sha256", "d" * 64, "--max-frames", "115",
        "--ground-truth-count", "0", "--material-label", "empty_belt",
        "--stabilization-delay-s", "3", "--counting-profile", "full-width-mixed"])
    plan = SimpleNamespace(boot_id=boot, manifest_sha256="a" * 64,
                           bit_sha256="b" * 64, hwh_sha256="c" * 64)
    identity = runner.formal_capture_context(args, plan)["identity"]
    assert identity["preprocessing_config"] == runner.preprocessing_configuration(args)
    assert identity["counting_config"] == runner.counting_configuration(args)
    assert {"live_calibration.py", "stream_counter.py"} <= identity["runtime_sha256"].keys()
    args.counting_profile = "legacy"
    legacy = runner.formal_capture_context(args, plan)["identity"]
    assert "preprocessing_config" not in legacy
    assert legacy != identity


def test_recorded_raw_components_replay_with_same_guarded_config():
    from dataclasses import asdict
    from replay_recorded_components import replay
    config = runner.counting_configuration(runner._parser().parse_args([
        "--counting-profile", "full-width-mixed"]))
    counter = runner.MaskCounter(**config)
    frames, live_events = [], []
    for frame_id, x in enumerate((96, 128, 164, 196)):
        mask = np.zeros((240, 320), np.uint8)
        # Two fragments in the upper row, intact independent object below.
        mask[23:27, x-4:x+5] = 255
        mask[30:34, x-4:x+5] = 255
        mask[195:206, x-5:x+6] = 255
        result = counter.update(frame_id, mask)
        frames.append({"frame_id": frame_id,
                       "raw_detections": [asdict(d) for d in result.raw_detections],
                       "detections": [asdict(d) for d in result.detections]})
        live_events.extend(asdict(e) for e in result.events)
    recorded = replay(frames, config)
    assert recorded["events"] == live_events
    assert recorded["replayed_total_count"] == counter.total_count == 2


def test_frame_roi_metadata_uses_effective_profile_instead_of_cli_values():
    args = runner._parser().parse_args(["--counting-profile", "full-width-mixed",
                                       "--count-axis", "y", "--line-x", "90",
                                       "--line-y", "80"])
    counter = runner.MaskCounter(**runner.counting_configuration(args))
    result = counter.update(0, np.zeros((240, 320), np.uint8))
    assert runner.counting_roi_metadata(result, counter) == {
        "top": 0, "bottom_exclusive": 240, "line_y": 120, "line_x": 160, "count_axis": "x"}


@pytest.mark.parametrize("gap", [4, 12])
def test_five_close_intact_rows_remain_independent_through_crossing(gap):
    args = runner._parser().parse_args(["--counting-profile", "full-width-mixed"])
    counter = runner.MaskCounter(**runner.counting_configuration(args))
    for frame_id, x in enumerate((96, 128, 164, 196)):
        mask = np.zeros((240, 320), np.uint8)
        for top in range(30, 30 + 5 * (11 + gap), 11 + gap):
            mask[top:top+11, x-5:x+6] = 255
        result = counter.update(frame_id, mask)
        assert len(result.detections) == 5
    assert result.total_count == 5
