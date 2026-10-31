from dataclasses import FrozenInstanceError
from inspect import signature

import pytest

from scripts.standalone.video_telemetry import (
    FrameTelemetry,
    TelemetryWindow,
    percentile,
    pl_counters_snapshot,
)


def test_frame_telemetry_is_frozen_and_rejects_invalid_timestamps():
    frame = FrameTelemetry(frame_id=1, capture_ns=10)
    with pytest.raises(FrozenInstanceError):
        frame.frame_id = 2

    for invalid in (-1, True):
        with pytest.raises((TypeError, ValueError)):
            FrameTelemetry(frame_id=1, capture_ns=invalid)


def test_percentile_uses_linear_interpolation():
    assert percentile([], 50) is None
    assert percentile((0, 10), 50) == 5
    assert percentile((0, 10, 20, 30), 95) == 28.5


def test_percentile_preserves_small_interpolated_values():
    assert percentile((0.0, 1e-13), 50) == pytest.approx(5e-14, rel=1e-12, abs=0.0)


def test_window_keeps_only_max_samples_frames():
    window = TelemetryWindow(max_samples=3)
    for frame_id in range(4):
        window.record_frame(FrameTelemetry(frame_id, frame_id * 1_000_000_000))

    result = window.sample(now_ns=3_000_000_000)
    assert result["frame_count"] == 3


def test_sample_computes_e2e_percentiles_and_empty_summaries():
    window = TelemetryWindow()
    window.record_frame(
        FrameTelemetry(
            frame_id=1,
            capture_ns=1_000_000,
            submit_done_ns=10_000_125,
        )
    )
    result = window.sample(now_ns=10_000_125)
    assert result["e2e_ms"]["p95"] == 9.000125

    empty = TelemetryWindow().sample(now_ns=0)
    for summary in empty["metrics"].values():
        assert summary == {
            "count": 0,
            "mean": None,
            "min": None,
            "max": None,
            "p50": None,
            "p95": None,
            "p99": None,
        }


def test_camera_and_pump_counter_signatures_deltas_and_fractional_fps():
    assert list(signature(TelemetryWindow.record_camera).parameters) == [
        "self", "captured_total", "overwrite_drops", "sequence_errors", "now_ns"
    ]
    assert list(signature(TelemetryWindow.record_pump).parameters) == [
        "self", "submitted", "skipped", "submit_failures", "now_ns"
    ]
    window = TelemetryWindow()
    assert window.record_camera(10, 2, 1, 0) is None
    assert window.record_camera(11, 4, 5, 300_000_000) == {
        "camera_fps": 10 / 3,
        "camera_overwrite_drops": 2,
        "camera_sequence_errors": 4,
    }
    assert window.record_pump(20, 3, 1, 0) is None
    assert window.record_pump(21, 8, 4, 300_000_000) == {
        "display_fps": 10 / 3,
        "pump_skips": 5,
        "pump_submit_failures": 3,
    }


def test_ps_total_spans_transfer_to_track():
    window = TelemetryWindow()
    window.record_frame(
        FrameTelemetry(
            frame_id=1,
            capture_ns=0,
            consume_ns=10_000_000,
            transfer_done_ns=20_000_000,
            regions_done_ns=30_000_000,
            track_done_ns=50_000_000,
        )
    )
    result = window.sample(now_ns=50_000_000)
    assert result["ps_total_ms"]["mean"] == 30
    assert result["regions_ms"]["mean"] == 10
    assert result["track_ms"]["mean"] == 20


def test_sample_filters_frames_by_capture_timestamp():
    window = TelemetryWindow(window_s=2)
    window.record_frame(FrameTelemetry(frame_id=1, capture_ns=2_999_999_999))
    window.record_frame(FrameTelemetry(frame_id=2, capture_ns=3_000_000_000))
    assert window.sample(now_ns=5_000_000_000)["frame_count"] == 1


def test_missing_stage_timestamps_do_not_add_samples():
    window = TelemetryWindow()
    window.record_frame(FrameTelemetry(frame_id=1, capture_ns=1_000))
    summaries = window.sample(now_ns=1_000)["metrics"]
    assert all(summary["count"] == 0 for summary in summaries.values())
    assert all(summary["mean"] is None for summary in summaries.values())


def test_reset_clears_frames_and_counter_baselines():
    window = TelemetryWindow()
    window.record_frame(FrameTelemetry(frame_id=1, capture_ns=0))
    assert window.record_camera(4, 1, 2, 0) is None
    assert window.record_pump(5, 2, 3, 0) is None
    window.reset()
    assert window.sample(now_ns=0)["frame_count"] == 0
    assert window.record_camera(8, 4, 6, 0) is None
    assert window.record_pump(9, 7, 8, 0) is None


def test_pl_counters_snapshot_wraps_raw_as_aggregate_only():
    snapshot = pl_counters_snapshot(
        {"sequence": 7, "clock_hz": 100_000_000, "counters": {"frames": 12}}
    )
    assert snapshot == {
        "schema": "material-counter.pl-counters.v1",
        "sequence": 7,
        "clock_hz": 100_000_000,
        "aggregate": {"frames": 12},
        "modules": [],
        "coverage": "aggregate-only",
    }


def test_pl_counters_snapshot_marks_none_unknown():
    snapshot = pl_counters_snapshot(None)
    assert snapshot == {
        "schema": "material-counter.pl-counters.v1",
        "clock_hz": None,
        "aggregate": None,
        "modules": [],
        "coverage": "unknown",
    }

