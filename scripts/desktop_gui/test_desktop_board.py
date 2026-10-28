"""Hardware-free checks of the desktop camera processing boundary."""
import sys
import json
import http.client
import threading
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "wo0016"), str(HERE.parent / "gray8")]

from desktop_board import DesktopRuntime, DesktopServer
from frame_protocol import LatestFrameHub, read_frame
from live_calibration import MonochromePreprocessor
from stream_counter import MaskCounter


class CaptureHub:
    def __init__(self):
        self.client_count = 1
        self.frames = []

    def publish(self, frame):
        self.frames.append({**frame, "gray": frame["gray"].copy(), "mask": frame["mask"].copy()})


def make_runtime(hub):
    counter = MaskCounter(count_axis="x", line_x=160, hysteresis=15.0,
                          line_y=120, roi_half_height=24, min_area=12,
                          max_step=60.0, max_lateral_step=10.0, max_area=900,
                          max_bbox_width=85, max_bbox_height=85,
                          discard_finished_tracks=True)
    return DesktopRuntime("test-session", MonochromePreprocessor(), counter, hub,
                          clock=lambda: 100.0)


def test_published_gray_and_mask_are_from_the_same_processed_camera_frame():
    hub = CaptureHub()
    runtime = make_runtime(hub)
    source = np.zeros((400, 640), np.uint8)
    source[180:220, 280:320] = 255
    result = runtime.process_frame(source)
    frame = hub.frames[0]
    assert frame["frame_id"] == result.frame_id == 0
    assert frame["session_id"] == "test-session"
    assert frame["source"] == "software"
    assert np.array_equal(frame["mask"], (frame["gray"] > 128).astype(np.uint8) * 255)
    assert frame["result"]["total_count"] == result.total_count
    source[:] = 0
    assert frame["gray"].max() == 255


def test_no_subscriber_avoids_frame_copy_while_counting_continues():
    hub = CaptureHub()
    hub.client_count = 0
    runtime = make_runtime(hub)
    runtime.process_frame(np.zeros((400, 640), np.uint8))
    assert hub.frames == []
    assert runtime.state.snapshot()["dashboard"]["frames_processed"] == 1


def test_reset_applies_on_next_completed_frame():
    hub = CaptureHub()
    runtime = make_runtime(hub)
    runtime.process_frame(np.zeros((400, 640), np.uint8))
    runtime.state.control("reset", {})
    assert runtime.state.snapshot()["dashboard"]["reset_pending"] is True
    runtime.process_frame(np.zeros((400, 640), np.uint8))
    status = runtime.state.snapshot()["dashboard"]
    assert status["reset_pending"] is False
    assert status["reset_sequence"] == 1


def test_http_stream_uses_one_paired_frame_and_session_controls_require_token():
    hub = LatestFrameHub()
    runtime = make_runtime(hub)
    server = DesktopServer(runtime, "secret", port=0).start()
    response = None
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
        connection.request("GET", "/status.json")
        assert connection.getresponse().status == 403
        connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
        connection.request("GET", "/api/frames", headers={"Authorization": "Bearer secret"})
        response = connection.getresponse()
        assert response.status == 200
        deadline = time.monotonic() + 2
        while not hub.client_count and time.monotonic() < deadline:
            time.sleep(0.01)
        assert hub.client_count == 1
        runtime.process_frame(np.full((400, 640), 255, np.uint8))
        frame = read_frame(response)
        assert frame["session_id"] == "test-session"
        assert frame["frame_id"] == 0
        assert np.array_equal(frame["mask"], (frame["gray"] > 128).astype(np.uint8) * 255)
        control = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
        control.request("POST", "/api/stop", "{}", {"Authorization": "Bearer secret", "Content-Type": "application/json"})
        stop_response = control.getresponse()
        assert stop_response.status == 200
        assert json.loads(stop_response.read())["phase"] == "stopping"
        control.close()
    finally:
        runtime.state.stop.set()
        hub.close()
        if response is not None:
            response.close()
        server.close()


def test_calibration_is_applied_at_a_frame_boundary():
    runtime = make_runtime(CaptureHub())
    applied = []

    class Controls:
        values = {}
        def update(self, payload):
            applied.append(payload)
            return payload

    runtime.calibration_controls = Controls()
    outcome = []
    thread = threading.Thread(target=lambda: outcome.append(runtime.control("calibration", {"brightness": 1})))
    thread.start()
    deadline = time.monotonic() + 1
    while not runtime.has_pending_calibration and time.monotonic() < deadline:
        time.sleep(0.01)
    assert applied == []
    runtime.process_frame(np.zeros((400, 640), np.uint8))
    thread.join(1)
    assert not thread.is_alive()
    assert applied == [{"brightness": 1}]
    assert outcome == applied
