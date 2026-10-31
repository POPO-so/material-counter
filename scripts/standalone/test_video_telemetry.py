from dataclasses import FrozenInstanceError
from inspect import signature
from types import SimpleNamespace

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

    for invalid in (-1, True, 2**64):
        with pytest.raises((TypeError, ValueError)):
            FrameTelemetry(frame_id=1, capture_ns=invalid)


def test_frame_telemetry_rejects_non_monotonic_timestamps():
    with pytest.raises(ValueError):
        FrameTelemetry(frame_id=1, capture_ns=10, consume_ns=9)
    with pytest.raises(ValueError):
        FrameTelemetry(frame_id=1, capture_ns=10, transfer_done_ns=9)
    with pytest.raises(ValueError):
        FrameTelemetry(frame_id=1, capture_ns=10, regions_done_ns=9)

    FrameTelemetry(frame_id=1, capture_ns=10, regions_done_ns=20)


def test_percentile_uses_linear_interpolation():
    assert percentile([], 50) is None
    assert percentile((0, 10), 50) == 5
    assert percentile((0, 10, 20, 30), 95) == 28.5


def test_percentile_preserves_small_interpolated_values():
    assert percentile((0.0, 1e-13), 50) == pytest.approx(5e-14, rel=1e-12, abs=0.0)


def test_percentile_validates_percent_before_empty_input():
    with pytest.raises(ValueError):
        percentile((), -1)
    with pytest.raises(ValueError):
        percentile((1,), 101)


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


def test_camera_and_pump_time_non_increase_is_unknown():
    window = TelemetryWindow()
    assert window.record_camera(10, 2, 1, 10) is None
    expected_camera = {
        "camera_fps": None,
        "camera_overwrite_drops": None,
        "camera_sequence_errors": None,
    }
    assert window.record_camera(11, 3, 2, 10) == expected_camera
    assert window.record_camera(12, 4, 3, 9) == expected_camera

    assert window.record_pump(10, 2, 1, 10) is None
    expected_pump = {
        "display_fps": None,
        "pump_skips": None,
        "pump_submit_failures": None,
    }
    assert window.record_pump(11, 3, 2, 10) == expected_pump
    assert window.record_pump(12, 4, 3, 9) == expected_pump


def test_camera_and_pump_counter_rollback_is_unknown():
    window = TelemetryWindow()
    assert window.record_camera(10, 2, 1, 0) is None
    assert window.record_camera(9, 1, 0, 1_000_000_000) == {
        "camera_fps": None,
        "camera_overwrite_drops": None,
        "camera_sequence_errors": None,
    }

    window.reset()
    assert window.record_pump(10, 2, 1, 0) is None
    assert window.record_pump(9, 1, 0, 1_000_000_000) == {
        "display_fps": None,
        "pump_skips": None,
        "pump_submit_failures": None,
    }


def test_uint64_bounds_are_enforced_for_timestamps_counters_and_now():
    max_u64 = 2**64 - 1
    FrameTelemetry(frame_id=1, capture_ns=max_u64)

    optional_fields = (
        "consume_ns",
        "transfer_done_ns",
        "regions_done_ns",
        "track_done_ns",
        "publish_ns",
        "compose_done_ns",
        "submit_done_ns",
    )
    for field in optional_fields:
        with pytest.raises(ValueError):
            FrameTelemetry(frame_id=1, capture_ns=0, **{field: 2**64})
        with pytest.raises(TypeError):
            FrameTelemetry(frame_id=1, capture_ns=0, **{field: True})

    window = TelemetryWindow()
    for invalid in (-1, 2**64, True):
        with pytest.raises((TypeError, ValueError)):
            window.record_camera(invalid, 0, 0, 0)
        with pytest.raises((TypeError, ValueError)):
            window.record_camera(0, 0, 0, invalid)
        with pytest.raises((TypeError, ValueError)):
            window.record_pump(invalid, 0, 0, 0)
        with pytest.raises((TypeError, ValueError)):
            window.record_consumed(invalid, 0)
        with pytest.raises((TypeError, ValueError)):
            window.record_consumed(0, invalid)


def test_record_consumed_returns_effective_fps_and_handles_unknowns():
    window = TelemetryWindow()
    assert window.record_consumed(10, 0) is None
    assert window.record_consumed(12, 1_000_000_000) == {"effective_input_fps": 2.0}
    assert window.record_consumed(13, 1_000_000_000) == {"effective_input_fps": None}
    assert window.record_consumed(14, 999_999_999) == {"effective_input_fps": None}

    rollback = TelemetryWindow()
    assert rollback.record_consumed(10, 0) is None
    assert rollback.record_consumed(9, 1_000_000_000) == {"effective_input_fps": None}


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


def test_sample_omits_invalid_or_reversed_intervals():
    window = TelemetryWindow()
    window.record_frame(
        SimpleNamespace(
            capture_ns=100,
            consume_ns=200,
            transfer_done_ns=True,
            regions_done_ns=150,
            track_done_ns=300,
            publish_ns=None,
            compose_done_ns=None,
            submit_done_ns=None,
        )
    )
    metrics = window.sample(now_ns=1_000)["metrics"]
    assert metrics["camera_wait_ms"]["count"] == 1
    assert metrics["queue_transfer_ms"]["count"] == 0
    assert metrics["ps_total_ms"]["count"] == 0
    assert metrics["regions_ms"]["count"] == 0
    assert metrics["track_ms"]["count"] == 1
    assert metrics["e2e_ms"]["count"] == 0


def test_missing_stage_timestamps_do_not_add_samples():
    window = TelemetryWindow()
    window.record_frame(FrameTelemetry(frame_id=1, capture_ns=1_000))
    summaries = window.sample(now_ns=1_000)["metrics"]
    assert all(summary["count"] == 0 for summary in summaries.values())
    assert all(summary["mean"] is None for summary in summaries.values())


def test_push_and_camera_state_stays_fixed_size():
    window = TelemetryWindow(max_samples=3)
    for index in range(100):
        window.record_camera(index, index, index, index)
        window.record_pump(index, index, index, index)
    assert not hasattr(window, "_camera")
    assert not hasattr(window, "_pump")


def test_reset_clears_frames_and_counter_baselines():
    window = TelemetryWindow()
    window.record_frame(FrameTelemetry(frame_id=1, capture_ns=0))
    assert window.record_camera(4, 1, 2, 0) is None
    assert window.record_pump(5, 2, 3, 0) is None
    assert window.record_consumed(6, 0) is None
    window.reset()
    assert window.sample(now_ns=0)["frame_count"] == 0
    assert window.record_camera(8, 4, 6, 0) is None
    assert window.record_pump(9, 7, 8, 0) is None
    assert window.record_consumed(10, 0) is None


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
