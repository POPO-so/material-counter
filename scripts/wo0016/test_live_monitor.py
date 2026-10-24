"""Host-only tests for continuous monitor state and lifecycle."""
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


def state_module():
    assert importlib.util.find_spec("live_monitor_state") is not None, "live state is not implemented"
    return importlib.import_module("live_monitor_state")


def test_reset_preserves_new_crossing_on_boundary_and_raw_counter():
    cls = state_module().LiveMonitorState
    state = cls(mode="mock", history_limit=8)
    def result(count):
        from dataclasses import make_dataclass
        return make_dataclass("Result", ["total_count", "detections"])(count, ())
    assert state.complete_frame(result(3)).total_count == 3
    state.control("reset", {})
    assert state.complete_frame(result(4)).total_count == 1
    assert state.complete_frame(result(4)).total_count == 1
    state.control("reset", {})
    assert state.complete_frame(result(4)).total_count == 0
    assert state.snapshot()["dashboard"]["total_count"] == 0


def test_sliding_history_is_bounded_and_health_is_explicitly_mock():
    cls = state_module().LiveMonitorState
    state = cls(mode="mock", history_limit=8)
    from dataclasses import make_dataclass
    result = make_dataclass("Result", ["total_count", "detections"])(0, ())
    for _ in range(10000):
        state.complete_frame(result)
    assert len(state.completed_times) == 8
    data = state.snapshot()["dashboard"]
    assert data["mode"] == "mock"
    assert data["dma"] == "disabled"
    assert data["cma_free_kib"] is None
    assert data["frames_processed"] == 10000


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ctypes current-RSS implementation")
def test_windows_rss_sampler_does_not_accumulate_ctypes_types():
    import ctypes
    sampler = state_module().process_rss_kib
    sampler()
    before = len(ctypes._pointer_type_cache)
    for _ in range(200):
        assert sampler() > 0
    assert len(ctypes._pointer_type_cache) == before


def test_entry_defaults_and_opt_in_before_any_board_access():
    assert importlib.util.find_spec("run_live_monitor") is not None, "live entry is not implemented"
    live = importlib.import_module("run_live_monitor")
    args = live.parser().parse_args([])
    assert (args.mode, args.host, args.port, args.counting_profile) == (
        "mock", "127.0.0.1", 8080, "full-width-mixed")
    with pytest.raises(ValueError, match="execute"):
        live.validate_board_options(args)


def test_monitor_rejects_legacy_axis_before_starting_server():
    import asyncio
    import run_live_monitor as live
    args = live.parser().parse_args(["--port", "0"])
    args.counting_profile = "legacy"
    state = state_module().LiveMonitorState(mode="mock")
    state.stop.set()
    with pytest.raises(ValueError, match="full-width-mixed"):
        asyncio.run(live.run_monitor(args, state=state))


def test_live_parser_does_not_accept_finite_capture_or_permit_flags():
    import run_live_monitor as live
    for unsupported in (["--capture-dir", "unused"],
                        ["--enable-touching-split"], ["--min-split-area", "320"]):
        with pytest.raises(SystemExit):
            live.parser().parse_args(unsupported)


def test_mock_masks_recur_and_cross_x160():
    assert importlib.util.find_spec("run_live_monitor") is not None, "live entry is not implemented"
    live = importlib.import_module("run_live_monitor")
    from stream_counter import MaskCounter
    import run_stream_counter as old
    args = live.parser().parse_args([])
    config = old.counting_configuration(args)
    counter = MaskCounter(**config, discard_finished_tracks=True)
    count = 0
    for i in range(400):
        gray, mask = live.synthetic_frame(i)
        assert gray.shape == mask.shape == (240, 320)
        count = counter.update(i, mask).total_count
    assert count >= 4
    assert len(counter._tracks) < 10


def test_mock_direction_matches_requested_counter_direction():
    import run_live_monitor as live
    from stream_counter import MaskCounter
    args = live.parser().parse_args(["--direction", "-1"])
    counter = MaskCounter(**live.finite.counting_configuration(args), discard_finished_tracks=True)
    for i in range(100):
        _, mask = live.synthetic_frame(i, direction=-1)
        result = counter.update(i, mask)
    assert result.total_count == 1


def test_continuous_camera_stops_and_releases_without_frame_archive():
    import asyncio
    import cv2
    import run_stream_counter as finite
    closed = []
    stop = [False]
    class Capture:
        def isOpened(self): return True
        def set(self, *args): return True
        def get(self, prop):
            return {cv2.CAP_PROP_FOURCC: cv2.VideoWriter_fourcc(*"MJPG"),
                    cv2.CAP_PROP_FRAME_WIDTH: 640, cv2.CAP_PROP_FRAME_HEIGHT: 400,
                    cv2.CAP_PROP_FPS: 60}.get(prop, 1)
        def read(self): return True, np.zeros((400, 640), np.uint8)
        def release(self): closed.append(True)
    fake = SimpleNamespace(**{name: getattr(cv2, name) for name in (
        "CAP_V4L2", "CAP_PROP_FOURCC", "CAP_PROP_FRAME_WIDTH", "CAP_PROP_FRAME_HEIGHT",
        "CAP_PROP_FPS", "CAP_PROP_BUFFERSIZE", "INTER_NEAREST", "VideoWriter_fourcc", "resize")})
    fake.VideoCapture = lambda *args: Capture()
    async def collect():
        count = 0
        async for _ in finite._camera_gray_frames_impl(
                "/dev/fake", max_frames=None, live_stop_requested=lambda: stop[0], cv2_module=fake):
            count += 1
            if count == 5: stop[0] = True
        return count
    assert asyncio.run(collect()) == 5
    assert closed == [True]


@pytest.mark.parametrize("safe", [True, False])
def test_board_cleanup_frees_only_proven_idle_buffers(monkeypatch, safe):
    import run_live_monitor as live
    state = state_module().LiveMonitorState(mode="board")
    session = live.BoardSession(live.parser().parse_args([]), state)
    freed, held = [], []
    session.api = SimpleNamespace(free=lambda b: freed.append(b), fail_hold=lambda *args: held.append(args))
    session.dma = object()
    session.tx, session.rx = (object(), object()), (object(), object())
    monkeypatch.setattr(live.finite, "_dma_buffers_safe_to_release", lambda *args: safe)
    quarantine = live.finite.board_once.QUARANTINED_BUFFERS
    old_size = len(quarantine)
    try:
        if safe:
            session.close()
            session.close()
            assert len(freed) == 4 and not held
        else:
            with pytest.raises(RuntimeError, match="FAIL_HOLD"):
                session.close()
            assert not freed and held
            assert state.phase == "fail_hold"
            assert len(quarantine) == old_size + 1
    finally:
        del quarantine[old_size:]


def test_mock_monitor_http_stream_controls_and_shutdown():
    import asyncio
    import json
    import queue
    import threading
    import urllib.request
    import run_live_monitor as live
    state = state_module().LiveMonitorState(mode="mock", history_limit=8)
    args = live.parser().parse_args(["--no-auth", "--port", "0", "--mock-fps", "60", "--history-limit", "8"])
    ready, errors = queue.Queue(), []
    def run():
        try:
            asyncio.run(live.run_monitor(args, state=state, server_ready=ready.put))
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=run)
    worker.start()
    server = ready.get(timeout=5)
    base = f"http://127.0.0.1:{server.port}"
    def post(path, value):
        request = urllib.request.Request(base + path, data=json.dumps(value).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)
    try:
        with urllib.request.urlopen(base + "/stream.mjpg", timeout=5) as response:
            assert b"Content-Type: image/jpeg" in response.read(200)
        with urllib.request.urlopen(base + "/status.json", timeout=3) as response:
            data = json.load(response)
        assert data["capabilities"]["reset"]
        assert data["telemetry"]["mode"] == "mock"
        assert data["health"]["rss_kib"] > 0
        post("/api/reset-count", {})
        post("/api/view-mode", {"mode": "binary"})
        assert state.view_mode == "binary"
        post("/api/stop", {})
    finally:
        state.stop.set()
        worker.join(timeout=5)
    assert not worker.is_alive() and not errors
    assert server.hub.closed
    assert state.phase == "stopped"


def test_new_batch_counts_exactly_once_with_real_tracker():
    import run_live_monitor as live
    from stream_counter import MaskCounter
    config = live.finite.counting_configuration(live.parser().parse_args([]))
    counter = MaskCounter(**config, discard_finished_tracks=True)
    state = state_module().LiveMonitorState(mode="mock", history_limit=8)
    for i in range(250):
        _, mask = live.synthetic_frame(i)
        if i in (40, 150):
            # First reset immediately before crossing; second after crossing.
            state.control("reset", {})
        displayed = state.complete_frame(counter.update(i, mask))
    assert counter.total_count == 3
    assert displayed.total_count == 1
    assert state.reset_sequence == 2


def test_live_mock_current_memory_plateaus_after_warmup():
    import gc
    import tracemalloc
    import run_live_monitor as live
    from stream_counter import MaskCounter
    from mjpeg_preview import PeriodicMjpegPublisher, MjpegFrameHub
    config = live.finite.counting_configuration(live.parser().parse_args([]))
    counter = MaskCounter(**config, discard_finished_tracks=True)
    state = state_module().LiveMonitorState(mode="mock", history_limit=16)
    publisher = PeriodicMjpegPublisher(hub=MjpegFrameHub(), line_y=120, history_limit=16)
    tracemalloc.start()
    try:
        def frames(start, count):
            for i in range(start, start + count):
                gray, mask = live.synthetic_frame(i)
                result = state.complete_frame(counter.update(i, mask))
                publisher(result, mask, gray)
        frames(0, 500)
        gc.collect()
        baseline = tracemalloc.get_traced_memory()[0]
        frames(500, 3000)
        gc.collect()
        growth = tracemalloc.get_traced_memory()[0] - baseline
        assert growth < 256 * 1024
        assert len(publisher.handoff_timings_ms) <= 16
        assert len(counter._tracks) <= 2
    finally:
        tracemalloc.stop()
        publisher.close()


def test_dma_timeout_closes_source_and_preserves_buffers_in_hold():
    import asyncio
    import run_live_monitor as live
    from test_bounded_stream import make_stream
    stream, recv, _ = make_stream(1)
    stream.transfer_timeout_s = 0.01
    async def stuck():
        await asyncio.Event().wait()
    recv.wait_async = stuck
    closed = []
    async def frames():
        try:
            yield np.zeros((240, 320), np.uint8)
        finally:
            closed.append(True)
    with pytest.raises(TimeoutError):
        asyncio.run(stream.run(frames()))
    assert stream.dma_may_be_active and closed == [True]
    held, freed = [], []
    session = live.BoardSession(live.parser().parse_args([]), state_module().LiveMonitorState(mode="board"))
    session.stream, session.dma = stream, object()
    session.tx = tuple(s.tx_buffer for s in stream.slots)
    session.rx = tuple(s.rx_buffer for s in stream.slots)
    session.api = SimpleNamespace(free=freed.append, fail_hold=lambda *args: held.append(args))
    quarantine = live.finite.board_once.QUARANTINED_BUFFERS
    old_size = len(quarantine)
    try:
        with pytest.raises(RuntimeError, match="FAIL_HOLD"):
            session.close()
        assert held and not freed
    finally:
        del quarantine[old_size:]


def test_reset_api_status_and_hud_use_the_same_frame_baseline(monkeypatch):
    import json
    import time
    import urllib.request
    import mjpeg_preview as preview
    import run_live_monitor as live
    from stream_counter import MaskCounter
    state = state_module().LiveMonitorState(mode="mock")
    counter = MaskCounter(**live.finite.counting_configuration(live.parser().parse_args([])),
                          discard_finished_tracks=True)
    rendered = []
    def render(mask, **kwargs):
        rendered.append(kwargs["total_count"])
        return b"test-jpeg"
    monkeypatch.setattr(preview, "render_preview_jpeg", render)
    hub = preview.MjpegFrameHub()
    publisher = preview.PeriodicMjpegPublisher(hub=hub, line_y=120, interval=1, history_limit=4)
    state.invalidate_preview = publisher.invalidate
    with preview.MjpegPreviewServer(host="127.0.0.1", port=0, hub=hub,
            state_provider=state.snapshot, live_control_callback=state.control) as server:
        try:
            with hub.streaming_client():
                for i in range(60):
                    gray, mask = live.synthetic_frame(i)
                    result = state.complete_frame(counter.update(i, mask))
                publisher(result, mask, gray)
                base = f"http://127.0.0.1:{server.port}"
                request = urllib.request.Request(base + "/api/reset-count", data=b"{}",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=3) as response:
                    assert json.load(response)["pending"]
                gray, mask = live.synthetic_frame(60)
                result = state.complete_frame(counter.update(60, mask))
                publisher(result, mask, gray)
                deadline = time.monotonic() + 3
                while not rendered or rendered[-1] != 0:
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
                with urllib.request.urlopen(base + "/status.json", timeout=3) as response:
                    status = json.load(response)
                assert status["telemetry"]["total_count"] == result.total_count == rendered[-1] == 0
                assert counter.total_count == 1
        finally:
            publisher.close()


def monitor_result(*, velocity=4.0, area=135, events=(), tracks=True):
    from dataclasses import make_dataclass
    track = {"track_id": 1, "forward_velocity": velocity, "missed": 0,
             "finished": False, "detection": {"area": area}}
    return make_dataclass("MonitorResult", ["total_count", "detections", "tracks", "events"])(
        len(events), (), (track,) if tracks else (), events)


def test_conveyor_speed_window_conversion_and_expiry():
    now = [0.0]
    state = state_module().LiveMonitorState(mode="mock", history_limit=3,
                                           mm_per_pixel=0.75, clock=lambda: now[0])
    for i, velocity in enumerate((2., 4., 6., 8.)):
        now[0] = i / 20
        state.complete_frame(monitor_result(velocity=velocity))
    data = state.snapshot()["dashboard"]
    assert data["px_per_frame"] == pytest.approx(6)
    assert data["px_per_second"] == pytest.approx(120)
    assert data["cm_per_second"] == pytest.approx(9)
    assert data["mm_per_pixel"] == 0.75
    assert len(state.velocity_history) == 3
    now[0] += 6
    assert state.snapshot()["dashboard"]["cm_per_second"] is None
    now[0] += 1
    state.complete_frame(monitor_result(tracks=False))
    assert state.snapshot()["dashboard"]["px_per_frame"] is None


@pytest.mark.parametrize("velocity", [None, float("nan"), float("inf"), -2.0])
def test_invalid_velocity_is_not_reported_as_conveyor_speed(velocity):
    state = state_module().LiveMonitorState(mode="mock")
    state.complete_frame(monitor_result(velocity=velocity))
    assert state.snapshot()["dashboard"]["px_per_frame"] is None


def test_crossing_log_contains_area_velocity_and_gap_recovery_and_reset():
    from stream_counter import CountEvent
    state = state_module().LiveMonitorState(mode="mock")
    event = CountEvent(1, 10, 162, 110, inferred_gap=True)
    state.complete_frame(monitor_result(events=(event,)))
    data = state.snapshot()["dashboard"]
    assert data["last_crossing_area_px"] == 135
    assert "触发速度预测跨帧恢复" in data["events"][-1]["message"]
    assert "135px" in data["events"][-1]["message"]
    assert "4.00 px/帧" in data["events"][-1]["message"]
    assert data["events"][-1]["area_px"] == 135
    state.control("reset", {})
    state.complete_frame(monitor_result())
    assert state.snapshot()["dashboard"]["last_crossing_area_px"] is None


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_invalid_mm_per_pixel_rejected_before_server_start(value):
    import asyncio
    import run_live_monitor as live
    args = live.parser().parse_args(["--mm-per-pixel", value])
    with pytest.raises(ValueError, match="mm-per-pixel"):
        asyncio.run(live.run_monitor(args))


def test_mm_per_pixel_default_and_custom():
    import run_live_monitor as live
    assert live.parser().parse_args([]).mm_per_pixel == 0.75
    assert live.parser().parse_args(["--mm-per-pixel", "0.5"]).mm_per_pixel == 0.5


def test_speed_uses_frame_means_and_ignores_unobserved_tracks():
    from dataclasses import replace
    now = [0.0]
    state = state_module().LiveMonitorState(mode="mock", history_limit=3,
                                           mm_per_pixel=0.5, clock=lambda: now[0])
    result = monitor_result(velocity=0.0)
    state.complete_frame(result)
    now[0] = 0.1
    tracks = (result.tracks[0], {**result.tracks[0], "track_id": 2, "forward_velocity": 8.0},
              {**result.tracks[0], "track_id": 3, "forward_velocity": 100.0, "missed": 1},
              {**result.tracks[0], "track_id": 4, "forward_velocity": 100.0, "finished": True})
    state.complete_frame(replace(result, tracks=tracks))
    data = state.snapshot()["dashboard"]
    assert data["px_per_frame"] == pytest.approx(2.0)
    assert data["cm_per_second"] == pytest.approx(1.0)
    for _ in range(10):
        now[0] += 0.1
        state.complete_frame(monitor_result(tracks=False))
    now[0] += 0.1
    state.complete_frame(monitor_result(velocity=6.0))
    assert state.snapshot()["dashboard"]["px_per_frame"] == 6.0


def test_missing_event_track_is_explicit_unknown_and_log_is_bounded():
    from stream_counter import CountEvent
    state = state_module().LiveMonitorState(mode="mock")
    result = monitor_result(events=(CountEvent(99, 1, 161, 110),), tracks=False)
    for _ in range(40):
        state.complete_frame(result)
    data = state.snapshot()["dashboard"]
    assert len(data["events"]) == 30
    assert data["last_crossing_area_px"] is None
    assert "面积 N/A，速度 N/A" in data["events"][-1]["message"]
    assert "跨帧恢复" not in data["events"][-1]["message"]
