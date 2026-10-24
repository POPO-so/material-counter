"""Offline telemetry tests: no PYNQ, camera or DMA access."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dashboard_state import DashboardState


def test_empty_state_does_not_invent_devices_or_numbers():
    state = DashboardState(source="pl_dma", clock=lambda: 10.0).snapshot()
    assert state["total_count"] is None
    assert state["processing_fps"] is None
    assert state["camera"] == state["fpga"] == state["dma"] == "unknown"


def test_completed_frames_bind_counts_and_expire_health_without_losing_total():
    now = [10.0]
    state = DashboardState(source="pl_dma", clock=lambda: now[0])
    result = SimpleNamespace(frame_id=0, total_count=7, detections=(1, 2))
    state.on_frame(result, None)
    now[0] = 12.0
    state.on_frame(SimpleNamespace(frame_id=1, total_count=8, detections=()), None)
    snapshot = state.snapshot()
    assert snapshot["total_count"] == 8 and snapshot["detections"] == 0
    assert snapshot["processing_fps"] == 0.5
    assert snapshot["dma"] == snapshot["fpga"] == "frame_verified"
    now[0] = 18.0
    snapshot = state.snapshot()
    assert snapshot["stale"] and snapshot["dma"] == "unknown"
    assert snapshot["processing_fps"] is None and snapshot["total_count"] == 8


def test_camera_software_count_is_explicitly_not_dma():
    state = DashboardState(source="software", clock=lambda: 10.0)
    state.on_frame(SimpleNamespace(frame_id=0, total_count=0, detections=()), None)
    snapshot = state.snapshot()
    assert snapshot["total_count"] == 0
    assert snapshot["source"] == "software"
    assert snapshot["fpga"] == snapshot["dma"] == "disabled"


def test_events_are_bounded_and_failed_state_has_no_healthy_devices():
    state = DashboardState(source="pl_dma", clock=lambda: 10.0)
    state.on_frame(SimpleNamespace(frame_id=0, total_count=3, detections=()), None)
    for value in range(60):
        state.set_phase("failed", f"failure {value}")
    snapshot = state.snapshot()
    assert len(snapshot["events"]) == 30
    assert snapshot["phase"] == "failed"
    assert snapshot["camera"] == snapshot["dma"] == "unknown"
    assert snapshot["events"][-1]["message"] == "failure 59"
