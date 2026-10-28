"""HTTP and publisher regressions for the optional live monitor controls."""
import json
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mjpeg_preview
from mjpeg_preview import MjpegFrameHub, MjpegPreviewServer, PeriodicMjpegPublisher


def get_json(server, path="/status.json"):
    with urlopen(f"http://127.0.0.1:{server.port}{path}", timeout=2) as response:
        return json.load(response)


def post_json(server, path, payload, *, origin=None, host=None, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if origin is not None:
        headers["Origin"] = origin
    if host is not None:
        headers["Host"] = host
    request = Request(
        f"http://127.0.0.1:{server.port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    return urlopen(request, timeout=2)


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def frame(frame_id, total_count=0):
    return SimpleNamespace(frame_id=frame_id, total_count=total_count, tracks=(),
                           detections=(), roi_top=0, roi_bottom=240)


def test_live_control_routes_dispatch_only_when_callback_is_configured():
    calls = []

    def control(action, payload):
        calls.append((action, payload))
        return {"ok": True, "action": action}

    with MjpegPreviewServer(host="127.0.0.1", port=0,
                            live_control_callback=control,
                            state_provider=lambda: {"dashboard": {"phase": "running"}}) as server:
        capabilities = get_json(server)["capabilities"]
        assert capabilities["reset"] is True
        assert capabilities["stop"] is True
        assert capabilities["view_mode"] is True

        for path, action, payload in (
            ("/api/reset-count", "reset", {}),
            ("/api/view-mode", "view", {"mode": "binary"}),
            ("/api/stop", "stop", {}),
        ):
            with post_json(server, path, payload) as response:
                assert json.load(response) == {"ok": True, "action": action}

    assert calls == [
        ("reset", {}),
        ("view", {"mode": "binary"}),
        ("stop", {}),
    ]


def test_live_controls_accept_missing_origin_but_reject_cross_origin_and_large_json():
    calls = []
    callback = lambda action, payload: calls.append((action, payload)) or {"ok": True}
    with MjpegPreviewServer(host="127.0.0.1", port=0,
                            live_control_callback=callback) as server:
        same_origin = f"http://127.0.0.1:{server.port}"
        with post_json(server, "/api/reset-count", {}, origin=same_origin) as response:
            assert response.status == 200
        assert calls == [("reset", {})]

        with pytest.raises(HTTPError) as error:
            post_json(server, "/api/reset-count", {}, origin="https://attacker.invalid")
        assert error.value.code == 403
        attacker_host = f"attacker.invalid:{server.port}"
        with pytest.raises(HTTPError) as error:
            post_json(server, "/api/reset-count", {}, origin=f"http://{attacker_host}",
                      host=attacker_host)
        assert error.value.code == 403
        with pytest.raises(HTTPError) as error:
            post_json(server, "/api/view-mode", {"mode": "x" * 600})
        assert error.value.code == 400
        assert calls == [("reset", {})]


def test_live_control_routes_stay_disabled_without_callback():
    with MjpegPreviewServer(host="127.0.0.1", port=0) as server:
        capabilities = get_json(server)["capabilities"]
        assert capabilities["reset"] is False
        assert capabilities["stop"] is False
        assert capabilities["view_mode"] is False
        for path in ("/api/reset-count", "/api/view-mode", "/api/stop"):
            with pytest.raises(HTTPError) as error:
                post_json(server, path, {})
            assert error.value.code == 403


@pytest.mark.parametrize("phase, expected", [
    ("waiting", False), ("running", True), ("stopping", False),
    ("stopped", False), ("fail_hold", False),
])
def test_live_control_capabilities_follow_monitor_phase(phase, expected):
    with MjpegPreviewServer(
            host="127.0.0.1", port=0,
            live_control_callback=lambda action, payload: {},
            state_provider=lambda: {"dashboard": {"phase": phase}}) as server:
        capabilities = get_json(server)["capabilities"]
        assert capabilities["reset"] is expected
        assert capabilities["stop"] is expected
        assert capabilities["view_mode"] is expected


def test_status_exposes_runtime_health_and_view_mode_without_reinterpreting_values():
    health = {"cma_free_kib": 1234, "rss_kib": 5678,
              "dma_status": {"mm2s": "0x00000001", "s2mm": "0x00000001"}}
    with MjpegPreviewServer(
            host="127.0.0.1", port=0,
            state_provider=lambda: {"resource_health": health, "view_mode": "binary"}) as server:
        status = get_json(server)
        assert status["health"] == health
        assert status["view_mode"] == "binary"


def test_publisher_uses_optional_bounded_histories_and_preserves_legacy_lists():
    bounded = PeriodicMjpegPublisher(
        hub=MjpegFrameHub(), line_y=120, interval=1, history_limit=2,
    )
    try:
        for index in range(5):
            bounded(frame(index), np.zeros((240, 320), dtype=np.uint8))
        assert len(bounded.handoff_timings_ms) == 2
        assert bounded.handoff_timings_ms.maxlen == 2
        assert bounded.background_encode_timings_ms.maxlen == 2
        assert bounded.encode_errors.maxlen == 2
        bounded.handoff_timings_ms.extend(range(5))
        bounded.background_encode_timings_ms.extend(range(5))
        bounded.encode_errors.extend(str(index) for index in range(5))
        assert list(bounded.handoff_timings_ms) == [3, 4]
        assert list(bounded.background_encode_timings_ms) == [3, 4]
        assert list(bounded.encode_errors) == ["3", "4"]
    finally:
        bounded.close()
        bounded.hub.close()

    legacy = PeriodicMjpegPublisher(hub=MjpegFrameHub(), line_y=120)
    try:
        assert isinstance(legacy.handoff_timings_ms, list)
        assert isinstance(legacy.background_encode_timings_ms, list)
        assert isinstance(legacy.encode_errors, list)
    finally:
        legacy.close()
        legacy.hub.close()


def test_binary_view_provider_uses_dma_mask_as_main_background(monkeypatch):
    hub = MjpegFrameHub()
    captured = []

    def render(mask, **kwargs):
        captured.append((mask.copy(), kwargs["source_gray"].copy()))
        return b"jpeg"

    monkeypatch.setattr(mjpeg_preview, "render_preview_jpeg", render)
    publisher = PeriodicMjpegPublisher(
        hub=hub, line_y=120, interval=1, view_mode_provider=lambda: "binary",
    )
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[70:90, 70:90] = 0xFF
    gray = np.full((240, 320), 127, dtype=np.uint8)
    try:
        with hub.streaming_client():
            publisher(frame(0), mask, gray)
            assert wait_until(lambda: bool(captured))
        np.testing.assert_array_equal(captured[0][0], mask)
        np.testing.assert_array_equal(captured[0][1], mask)
    finally:
        publisher.close()
        hub.close()


def test_invalidate_clears_old_jpeg_discards_inflight_encode_and_forces_fresh_frame(monkeypatch):
    hub = MjpegFrameHub()
    encode_started = threading.Event()
    release_encode = threading.Event()
    rendered = []

    def delayed_render(mask, **kwargs):
        count = kwargs["total_count"]
        rendered.append(count)
        if count == 1:
            encode_started.set()
            assert release_encode.wait(2)
            return b"stale-count-one"
        return b"fresh-count-zero"

    monkeypatch.setattr(mjpeg_preview, "render_preview_jpeg", delayed_render)
    publisher = PeriodicMjpegPublisher(hub=hub, line_y=120, interval=4)
    mask = np.zeros((240, 320), dtype=np.uint8)
    gray = np.zeros((240, 320), dtype=np.uint8)
    try:
        with hub.streaming_client():
            hub.publish(b"old-jpeg", dimensions=(320, 240))
            publisher(frame(0, 1), mask, gray)
            assert encode_started.wait(2)

            publisher.invalidate()
            assert hub.sequence == 1
            assert hub.wait_for_next(after_sequence=0, timeout_s=0) is None
            release_encode.set()

            assert wait_until(lambda: publisher._encoded_sequence >= 1)
            assert hub.sequence == 1

            # Frame 1 would normally be skipped by interval=4. Invalidation
            # requires the first post-reset frame to be encoded immediately.
            publisher(frame(1, 0), mask, gray)
            assert wait_until(lambda: rendered == [1, 0] and hub.sequence == 2)
            assert hub.wait_for_next(after_sequence=1, timeout_s=0) == (2, b"fresh-count-zero")
    finally:
        release_encode.set()
        publisher.close()
        hub.close()


def test_dashboard_has_live_controls_health_fields_and_mock_label():
    page = (Path(__file__).resolve().parent / "dashboard.html").read_text(encoding="utf-8")
    for expected in (
        "api/reset-count", "api/view-mode", "api/stop", "view_mode",
        "cma_free_kib", "cma_total_kib", "memory_total_kib", "cpu_percent", "mode==='mock'", "合成",
        "completed-frame sliding window", "fps-note", "stopping:'正在停止监控'",
        "fail_hold:'DMA 故障保持'", "合成 Binary8",
    ):
        assert expected in page


def test_browser_decoder_priming_does_not_inflate_unique_delivery_metrics():
    from mjpeg_preview import MjpegLoopbackClient
    hub = MjpegFrameHub()
    with MjpegPreviewServer(hub=hub, host="127.0.0.1", port=0) as server:
        client = MjpegLoopbackClient(f"http://127.0.0.1:{server.port}/stream.mjpg", timeout_s=1).start()
        try:
            assert wait_until(lambda: hub.stream_clients > 0)
            hub.publish(b"one-unique-frame")
            assert wait_until(lambda: client.frames_received >= 1)
            time.sleep(0.1)
            assert client.frames_received == 1
            assert client.frame_sequences == [1]
        finally:
            client.close()


def test_mjpeg_flushes_a_boundary_after_each_payload_for_single_frame_clients():
    with MjpegPreviewServer(host="127.0.0.1", port=0) as server:
        server.hub.publish(b"jpeg-one", dimensions=(320, 240))
        client = socket.create_connection(("127.0.0.1", server.port), timeout=1)
        client.settimeout(0.3)
        try:
            client.sendall(b"GET /stream.mjpg HTTP/1.1\r\nHost: localhost\r\n\r\n")
            received = bytearray()
            marker = b"jpeg-one\r\n--frame\r\n"
            deadline = time.monotonic() + 2
            while marker not in received and time.monotonic() < deadline:
                try:
                    chunk = client.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                received.extend(chunk)
            assert marker in received
        finally:
            client.close()


def test_mjpeg_primes_legacy_single_frame_browser_without_replaying_live_frames():
    def read_stream(server, jpeg, expected_parts):
        server.hub.publish(jpeg, dimensions=(320, 240))
        client = socket.create_connection(("127.0.0.1", server.port), timeout=1)
        client.settimeout(0.2)
        try:
            client.sendall(b"GET /stream.mjpg HTTP/1.1\r\nHost: localhost\r\n\r\n")
            received = bytearray()
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline and received.count(jpeg) < expected_parts:
                try:
                    chunk = client.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                received.extend(chunk)
            assert received.count(jpeg) == expected_parts
            return bytes(received)
        finally:
            client.close()

    with MjpegPreviewServer(host="127.0.0.1", port=0) as server:
        response = read_stream(server, b"legacy-jpeg", expected_parts=2)
        assert response.count(b"Content-Type: image/jpeg") == 2

    with MjpegPreviewServer(host="127.0.0.1", port=0,
                            live_control_callback=lambda action, payload: {}) as server:
        response = read_stream(server, b"live-jpeg", expected_parts=1)
        assert response.count(b"Content-Type: image/jpeg") == 1


def test_dashboard_page_and_version_update_without_server_restart(tmp_path, monkeypatch):
    import hashlib
    page = tmp_path / "dashboard.html"
    page.write_text("first", encoding="utf-8")
    monkeypatch.setattr(mjpeg_preview, "__file__", str(tmp_path / "mjpeg_preview.py"))
    with MjpegPreviewServer(host="127.0.0.1", port=0) as server:
        with urlopen(f"http://127.0.0.1:{server.port}/", timeout=2) as response:
            assert b"first" in response.read()
        assert get_json(server, "/api/page-version")["sha256"] == hashlib.sha256(b"first").hexdigest()
        page.write_text("second", encoding="utf-8")
        with urlopen(f"http://127.0.0.1:{server.port}/", timeout=2) as response:
            assert b"second" in response.read()
        assert get_json(server, "/api/page-version")["sha256"] == hashlib.sha256(b"second").hexdigest()


def test_resource_control_is_independent_and_exposes_confirmed_state():
    class Monitor:
        def __init__(self):
            self.enabled = False
            self.mode = 'normal'
        def control(self, enabled, mode):
            self.enabled, self.mode = enabled, mode
            return self.snapshot()
        def snapshot(self):
            return {'enabled': self.enabled, 'mode': self.mode, 'metrics': {}}
    monitor = Monitor()
    with MjpegPreviewServer(host='127.0.0.1', port=0, resource_monitor=monitor) as server:
        assert get_json(server)['resource_monitor']['enabled'] is False
        with post_json(server, '/api/resource-monitor', {'enabled': True, 'mode': 'detail'}) as response:
            assert json.load(response)['enabled'] is True
        assert get_json(server)['resource_monitor']['mode'] == 'detail'
        with post_json(server, '/api/resource-monitor', {'enabled': False, 'mode': 'normal'}) as response:
            assert json.load(response)['enabled'] is False
        with pytest.raises(HTTPError) as error:
            post_json(server, '/api/resource-monitor', {'enabled': True, 'mode': 'normal'}, origin='https://attacker.invalid')
        assert error.value.code == 403


def test_http_resource_monitor_accepts_actual_keyword_only_control():
    from resource_monitor import ResourceMonitor
    monitor = ResourceMonitor(source='mock')
    try:
        with MjpegPreviewServer(host='127.0.0.1', port=0, resource_monitor=monitor) as server:
            with post_json(server, '/api/resource-monitor', {'enabled': True, 'mode': 'normal'}) as response:
                assert json.load(response)['enabled'] is True
            with post_json(server, '/api/resource-monitor', {'enabled': False, 'mode': 'normal'}) as response:
                assert json.load(response)['enabled'] is False
    finally:
        monitor.close()


def test_camera_server_can_disable_board_html_while_keeping_status():
    with MjpegPreviewServer(host="127.0.0.1", port=0, serve_dashboard=False) as server:
        with pytest.raises(HTTPError) as error:
            urlopen(f"http://127.0.0.1:{server.port}/", timeout=2)
        assert error.value.code == 404
        assert get_json(server)["schema"] == 1
