"""Offline Stage B analysis tests; no board or camera is needed."""
from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _api():
    spec = importlib.util.find_spec("stageb_analysis")
    assert spec is not None, "stageb_analysis module should exist"
    return importlib.import_module("stageb_analysis")


def test_analyze_frame_uses_independent_binary_mask_and_overlays_metrics():
    api = _api()
    gray = np.full((240, 320), 30, dtype=np.uint8)
    mask = np.zeros_like(gray)
    mask[25:30, 15:20] = 255
    gray[25:30, 15:20] = 180

    result = api.analyze_frame(
        gray, mask, threshold=96, roi=(10, 20, 40, 50), fps=12.5
    )

    assert result.background_mean == pytest.approx(30.0)
    assert result.foreground_peak == 180
    assert result.foreground_pixels == 25
    assert result.overlay_bgr.shape == (240, 320, 3)
    assert result.overlay_bgr.dtype == np.uint8
    assert tuple(result.overlay_bgr[150, 150]) == (30, 30, 30)
    assert tuple(result.overlay_bgr[20, 10]) == (0, 255, 0)


def test_foreground_peak_is_none_when_mask_has_no_material_pixels():
    api = _api()
    gray = np.full((240, 320), 42, dtype=np.uint8)
    result = api.analyze_frame(gray, np.zeros_like(gray), threshold=80)

    assert result.background_mean == pytest.approx(42.0)
    assert result.foreground_peak is None
    assert result.foreground_pixels == 0


def test_live_analysis_reports_full_and_roi_rms_components_count_and_dma_hud():
    api = _api()
    gray = np.full((240, 320), 10, dtype=np.uint8)
    mask = np.zeros_like(gray)
    gray[10:12, 10:12] = 200
    mask[10:12, 10:12] = 255
    gray[20:23, 20:22] = 100
    mask[20:23, 20:22] = 255

    result = api.analyze_frame(
        gray, mask, threshold=96, roi=(8, 8, 24, 24), fps=30.0,
        total_count=7, count_line_y=120,
        dma_status={"mm2s": 0x1002, "s2mm": 0x1002},
    )

    assert result.background_mean == pytest.approx(10.0)
    assert result.background_rms == pytest.approx(10.0)
    assert result.foreground_peak == 200
    assert result.foreground_pixels == 10
    assert result.component_count == 2
    assert result.roi_component_count == 2
    assert result.roi_background_mean == pytest.approx(10.0)
    assert result.roi_background_rms == pytest.approx(10.0)
    assert result.roi_foreground_peak == 200
    assert result.roi_foreground_pixels == 10
    assert result.overlay_bgr.shape == (240, 320, 3)


def test_live_recorder_persists_roi_metrics_and_both_dma_status_registers():
    api = _api()
    recorder = api.BatchAuditRecorder(threshold=96, roi=(0, 0, 100, 100))
    gray = np.full((240, 320), 31, dtype=np.uint8)
    mask = np.zeros_like(gray)
    mask[1:3, 1:3] = 255

    recorder.record_frame(
        frame_id=0, gray8=gray, binary8=mask, monotonic_s=1.0,
        dmasr={"mm2s": 0x1002, "s2mm": 0x1002},
    )
    frame = recorder.to_dict()["frames"][0]

    assert frame["background_gray_mean"] == pytest.approx(31.0)
    assert frame["background_gray_rms"] == pytest.approx(31.0)
    assert frame["roi_background_gray_mean"] == pytest.approx(31.0)
    assert frame["roi_background_gray_rms"] == pytest.approx(31.0)
    assert frame["foreground_gray_peak"] == 31
    assert frame["foreground_pixels"] == 4
    assert frame["roi_foreground_pixels"] == 4
    assert frame["component_count"] == 1
    assert frame["roi_component_count"] == 1
    assert frame["dmasr"] == {"mm2s": 0x1002, "s2mm": 0x1002}


def test_analyze_frame_rejects_nonbinary_or_wrong_size_inputs():
    api = _api()
    gray = np.zeros((240, 320), dtype=np.uint8)
    bad_mask = gray.copy()
    bad_mask[0, 0] = 1

    with pytest.raises(ValueError, match="0x00 or 0xFF"):
        api.analyze_frame(gray, bad_mask, threshold=80)
    with pytest.raises(ValueError, match="shape"):
        api.analyze_frame(gray[:100], gray[:100], threshold=80)


def test_batch_report_keeps_dma_unknown_and_labels_event_time_as_observation():
    api = _api()
    recorder = api.BatchAuditRecorder(threshold=96, roi=(0, 0, 320, 240))
    gray = np.full((240, 320), 30, dtype=np.uint8)
    mask = np.zeros_like(gray)
    mask[5:7, 5:7] = 255
    mask[30:31, 30:33] = 255
    event = SimpleNamespace(track_id=4, frame_id=1, centroid_x=31.0, centroid_y=30.0)
    frame_result = SimpleNamespace(frame_id=1, total_count=1, events=(event,))

    recorder.record_frame(
        frame_id=0,
        gray8=gray,
        binary8=mask,
        monotonic_s=100.0,
        empty_belt_assumption=True,
    )
    recorder.record_frame(
        frame_id=1,
        gray8=gray,
        binary8=mask,
        monotonic_s=104.9,
        frame_result=frame_result,
    )
    report = recorder.to_dict()

    assert report["frames"][0]["component_areas"] == [4, 3]
    assert report["frames"][0]["assumed_false_trigger_pixels"] == 7
    assert report["frames"][0]["empty_belt_assumption"] is True
    assert report["frames"][1]["assumed_false_trigger_pixels"] is None
    assert report["frames"][1]["dmasr"] is None
    assert report["count_event_observations"][0]["observation_monotonic_s"] == 104.9
    assert "not exact physical crossing time" in report["count_event_observations"][0]["timestamp_semantics"]
    assert report["observed_duration_s"] == pytest.approx(4.9)
    assert report["duration_classification"] == "under_5_seconds"
    assert report["five_second_sampling_verified"] is False


def test_empty_belt_pixels_remain_unknown_without_offline_samples():
    api = _api()
    recorder = api.BatchAuditRecorder(threshold=96)

    report = recorder.to_dict()

    assert report["frames"] == []
    assert report["assumed_false_trigger_pixel_total"] is None
    assert report["empty_belt_assumption"] is None
    assert report["observed_duration_s"] is None
    assert report["five_second_sampling_verified"] is False


def test_offline_png_cli_writes_json_and_calibration_pngs(tmp_path: Path):
    api = _api()
    gray_dir = tmp_path / "gray"
    mask_dir = tmp_path / "binary"
    output_dir = tmp_path / "result"
    gray_dir.mkdir()
    mask_dir.mkdir()
    gray = np.full((240, 320), 30, dtype=np.uint8)
    mask = np.zeros_like(gray)
    mask[40:45, 50:55] = 255
    gray[40:45, 50:55] = 180
    assert cv2.imwrite(str(gray_dir / "frame_0001.png"), gray)
    assert cv2.imwrite(str(mask_dir / "frame_0001.png"), mask)

    exit_code = api.main([
        "--offline",
        "--gray-dir", str(gray_dir),
        "--binary-dir", str(mask_dir),
        "--output-dir", str(output_dir),
        "--threshold", "96",
        "--empty-belt-assumption",
    ])

    assert exit_code == 0
    report_path = output_dir / "report.json"
    calibration_path = output_dir / "calibration_frame_0001.png"
    assert report_path.is_file()
    assert calibration_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["execution_mode"] == "offline_png_sequence"
    assert report["frames"][0]["dmasr"] is None
    assert report["frames"][0]["assumed_false_trigger_pixels"] == 25


def test_offline_png_cli_requires_explicit_offline_marker():
    api = _api()

    with pytest.raises(SystemExit) as exc_info:
        api.main(["--gray-dir", "gray", "--binary-dir", "mask", "--output-dir", "out"])

    assert exc_info.value.code == 2


def test_long_timestamp_span_does_not_claim_continuous_five_second_sampling():
    api = _api()
    recorder = api.BatchAuditRecorder(threshold=96)
    gray = np.zeros((240, 320), dtype=np.uint8)
    mask = np.zeros_like(gray)
    for frame_id, timestamp in ((0, 20.0), (1, 26.0)):
        recorder.record_frame(
            frame_id=frame_id, gray8=gray, binary8=mask, monotonic_s=timestamp
        )

    report = recorder.to_dict()

    assert report["observed_duration_s"] == pytest.approx(6.0)
    assert report.get("observation_span_at_least_5s") is True
    assert report["five_second_sampling_verified"] is False


def test_recorder_rejects_repeated_frame_ids_and_out_of_range_dmasr():
    api = _api()
    recorder = api.BatchAuditRecorder(threshold=96)
    gray = np.zeros((240, 320), dtype=np.uint8)
    mask = np.zeros_like(gray)
    recorder.record_frame(frame_id=4, gray8=gray, binary8=mask, monotonic_s=1.0)

    with pytest.raises(ValueError, match="frame_id values must strictly increase"):
        recorder.record_frame(frame_id=4, gray8=gray, binary8=mask, monotonic_s=2.0)
    with pytest.raises(ValueError, match="frame_id values must strictly increase"):
        recorder.record_frame(frame_id=3, gray8=gray, binary8=mask, monotonic_s=2.0)
    with pytest.raises(ValueError, match="32-bit"):
        recorder.record_frame(frame_id=5, gray8=gray, binary8=mask, monotonic_s=3.0,
                              dmasr=0x1_0000_0000)


@pytest.mark.parametrize("with_existing_file", [False, True])
def test_offline_cli_refuses_existing_output_dir_without_changing_it(
    tmp_path: Path, with_existing_file: bool
):
    api = _api()
    gray_dir = tmp_path / "gray"
    mask_dir = tmp_path / "binary"
    output_dir = tmp_path / "result"
    gray_dir.mkdir()
    mask_dir.mkdir()
    output_dir.mkdir()
    gray = np.zeros((240, 320), dtype=np.uint8)
    mask = np.zeros_like(gray)
    assert cv2.imwrite(str(gray_dir / "frame.png"), gray)
    assert cv2.imwrite(str(mask_dir / "frame.png"), mask)
    sentinel = output_dir / "report.json"
    if with_existing_file:
        sentinel.write_text("preserve this audit evidence\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        api.main([
            "--offline", "--gray-dir", str(gray_dir), "--binary-dir", str(mask_dir),
            "--output-dir", str(output_dir),
        ])

    assert exc_info.value.code == 2
    assert list(output_dir.iterdir()) == ([sentinel] if with_existing_file else [])
    if with_existing_file:
        assert sentinel.read_text(encoding="utf-8") == "preserve this audit evidence\n"
