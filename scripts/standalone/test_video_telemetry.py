from dataclasses import FrozenInstanceError

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


def test_pl_counters_snapshot_wraps_raw_as_aggregate_only():
    snapshot = pl_counters_snapshot({"frames": 12, "cycles": 345})
    assert snapshot["schema"] == "material-counter.pl-counters.v1"
    assert snapshot["status"] == "aggregate-only"
    assert snapshot["aggregate"] == {"frames": 12, "cycles": 345}


def test_pl_counters_snapshot_marks_none_unknown():
    snapshot = pl_counters_snapshot(None)
    assert snapshot["schema"] == "material-counter.pl-counters.v1"
    assert snapshot["status"] == "unknown"
    assert snapshot["modules"] == []

