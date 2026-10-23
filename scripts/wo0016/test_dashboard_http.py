"""Exercise the real HTTP server without camera/FPGA access."""
import json
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dashboard_state import DashboardState
from mjpeg_preview import MjpegPreviewServer


def get(server, path):
    with urlopen(f"http://127.0.0.1:{server.port}{path}", timeout=2) as response:
        assert response.headers["Cache-Control"] == "no-store"
        return json.load(response)


def test_status_without_provider_has_no_fake_telemetry_or_controls():
    with MjpegPreviewServer(host="127.0.0.1", port=0) as server:
        payload = get(server, "/status.json")
        assert payload["telemetry"] is None
        assert payload["preview"]["fps"] is None
        assert payload["preview"]["width"] is None
        assert not any(payload["capabilities"].values())


def test_state_compatibility_and_status_bind_real_completed_result():
    state = DashboardState(source="software")
    state.on_frame(SimpleNamespace(frame_id=0, total_count=2, detections=(1,)))
    raw = {"status": "READY", "dashboard": state.snapshot()}
    with MjpegPreviewServer(host="127.0.0.1", port=0, state_provider=lambda: raw) as server:
        assert get(server, "/state.json") == raw
        data = get(server, "/status.json")
        assert data["telemetry"]["total_count"] == 2
        assert data["telemetry"]["fpga"] == "disabled"
        assert data["runtime_status"] == "READY"


def test_provider_failure_returns_503_instead_of_healthy_status():
    def broken():
        raise RuntimeError("private hardware details")
    with MjpegPreviewServer(host="127.0.0.1", port=0, state_provider=broken) as server:
        for path in ("/status.json", "/state.json"):
            with pytest.raises(HTTPError) as error:
                get(server, path)
            assert error.value.code == 503
            assert b"private" not in error.value.read()


def test_calibration_capability_requires_mode_and_callback():
    for mode, callback, expected in ((False, lambda x: x, False),
                                     (True, None, False), (True, lambda x: x, True)):
        with MjpegPreviewServer(host="127.0.0.1", port=0,
                                state_provider=lambda: {"calibration_mode": mode},
                                control_callback=callback) as server:
            assert get(server, "/status.json")["capabilities"]["calibration"] is expected


def test_unavailable_count_controls_are_rejected():
    with MjpegPreviewServer(host="127.0.0.1", port=0) as server:
        for path in ("/api/start", "/api/stop", "/api/reset"):
            request = Request(f"http://127.0.0.1:{server.port}{path}", data=b'{}',
                              headers={"Content-Type": "application/json"})
            with pytest.raises(HTTPError) as error:
                urlopen(request, timeout=2)
            assert error.value.code == 403


def test_hub_metrics_use_actual_publish_times_and_dimensions():
    with MjpegPreviewServer(host="127.0.0.1", port=0) as server:
        server.hub.publish(b"test-only", dimensions=(320, 240))
        time.sleep(.02)  # Windows monotonic may have a coarse timer resolution.
        server.hub.publish(b"test-only", dimensions=(320, 240))
        data = get(server, "/status.json")["preview"]
        assert data["fps"] > 0 and data["sequence"] == 2
        assert (data["width"], data["height"]) == (320, 240)


def test_idle_disconnected_stream_releases_client_lease():
    with MjpegPreviewServer(host="127.0.0.1", port=0) as server:
        client = socket.create_connection(("127.0.0.1", server.port), timeout=2)
        client.sendall(b"GET /stream.mjpg HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert b"200" in client.recv(1024)
        deadline = time.monotonic() + 2
        while not server.hub.stream_clients and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.hub.stream_clients == 1
        client.close()
        deadline = time.monotonic() + 3
        while server.hub.stream_clients and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.hub.stream_clients == 0
