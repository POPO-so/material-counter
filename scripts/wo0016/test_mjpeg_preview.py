import sys
import threading
import time
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mjpeg_preview as preview_module  # noqa: E402
from mjpeg_preview import (  # noqa: E402
    MjpegFrameHub,
    MjpegPreviewServer,
    PeriodicMjpegPublisher,
    render_preview_frame,
    render_preview_jpeg,
)


def test_preview_draws_red_virtual_line_and_count_text_on_binary_mask():
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[100:110, 40:50] = 0xFF

    preview = render_preview_frame(mask, total_count=7, line_y=120)

    assert preview.shape == (240, 320, 3)
    assert tuple(int(value) for value in preview[120, 10]) == (0, 0, 255)
    assert np.any(np.all(preview[18:30, 8:230] == (255, 255, 255), axis=2))


def test_preview_jpeg_is_valid_and_invalid_binary_masks_are_rejected():
    mask = np.zeros((240, 320), dtype=np.uint8)

    jpeg = render_preview_jpeg(mask, total_count=0, line_y=120)

    assert jpeg[:2] == b"\xff\xd8"
    with pytest.raises(ValueError, match="Binary8"):
        render_preview_jpeg(np.ones((240, 320), dtype=np.uint8), total_count=0, line_y=120)


def test_periodic_publisher_encodes_lazily_for_clients_on_a_worker_thread(monkeypatch):
    import mjpeg_preview

    encoded = []
    caller_thread = threading.get_ident()

    def fake_render(mask, *, total_count, line_y, quality):
        encoded.append((total_count, line_y, quality, threading.get_ident()))
        return b"jpeg"

    monkeypatch.setattr(mjpeg_preview, "render_preview_jpeg", fake_render)
    hub = MjpegFrameHub()
    publisher = PeriodicMjpegPublisher(hub=hub, line_y=120)
    mask = np.zeros((240, 320), dtype=np.uint8)

    try:
        for frame_id in range(4):
            publisher(type("Frame", (), {"frame_id": frame_id,
                                          "total_count": frame_id // 3})(), mask)

        assert encoded == []
        assert hub.sequence == 0

        with hub.streaming_client():
            first = hub.wait_for_next(after_sequence=0, timeout_s=2.0)
            assert first == (1, b"jpeg")
            for frame_id in range(4, 8):
                publisher(type("Frame", (), {"frame_id": frame_id,
                                              "total_count": frame_id // 3})(), mask)
            second = hub.wait_for_next(after_sequence=1, timeout_s=2.0)
            assert second == (2, b"jpeg")

        assert publisher.interval == 4
        assert publisher.quality == 70
        assert publisher.encoded_frames == 2
        assert [row[:3] for row in encoded] == [(0, 120, 70), (1, 120, 70)]
        assert all(thread_id != caller_thread for *_, thread_id in encoded)
        assert hub.sequence == 2
        assert len(publisher.handoff_timings_ms) == 8
        assert len(publisher.background_encode_timings_ms) == 2
    finally:
        close = getattr(publisher, "close", None)
        if callable(close):
            close()
        hub.close()


def test_preview_callback_returns_while_background_jpeg_encode_is_blocked(monkeypatch):
    import mjpeg_preview

    encode_started = threading.Event()
    release_encode = threading.Event()
    callback_returned = threading.Event()

    def slow_render(mask, *, total_count, line_y, quality):
        encode_started.set()
        assert release_encode.wait(2.0)
        return b"slow-jpeg"

    monkeypatch.setattr(mjpeg_preview, "render_preview_jpeg", slow_render)
    hub = MjpegFrameHub()
    publisher = PeriodicMjpegPublisher(hub=hub, line_y=120)
    mask = np.zeros((240, 320), dtype=np.uint8)
    result = type("Frame", (), {"frame_id": 0, "total_count": 1})()

    try:
        with hub.streaming_client():
            producer = threading.Thread(
                target=lambda: (publisher(result, mask), callback_returned.set())
            )
            producer.start()
            assert encode_started.wait(1.0)
            assert callback_returned.wait(1.0)
            release_encode.set()
            producer.join(timeout=1.0)
            assert not producer.is_alive()
            assert hub.wait_for_next(after_sequence=0, timeout_s=2.0) == (1, b"slow-jpeg")
    finally:
        release_encode.set()
        close = getattr(publisher, "close", None)
        if callable(close):
            close()
        hub.close()


def test_frame_hub_returns_latest_frame_without_a_backlog():
    hub = MjpegFrameHub()
    hub.publish(b"first-jpeg")
    hub.publish(b"second-jpeg")

    sequence, jpeg = hub.wait_for_next(after_sequence=0, timeout_s=0.01)

    assert sequence == 2
    assert jpeg == b"second-jpeg"
    hub.close()


def test_http_server_serves_page_and_live_mjpeg_multipart_stream():
    hub = MjpegFrameHub()
    server = MjpegPreviewServer(hub=hub, host="127.0.0.1", port=0)
    server.start()
    hub.publish(b"mock-jpeg-frame")
    base = f"http://127.0.0.1:{server.port}"
    try:
        with urlopen(base, timeout=2) as response:
            page = response.read().decode("utf-8")
            assert response.headers.get_content_type() == "text/html"
            assert "/stream.mjpg" in page
        with urlopen(base + "/stream.mjpg", timeout=2) as response:
            content_type = response.headers.get("Content-Type", "")
            assert content_type.startswith("multipart/x-mixed-replace")
            assert response.readline().strip() == b"--frame"
            part_headers = {}
            while True:
                line = response.readline()
                if line == b"\r\n":
                    break
                key, value = line.decode("ascii").split(":", 1)
                part_headers[key.lower()] = value.strip()
            assert part_headers["content-type"] == "image/jpeg"
            frame = response.read(int(part_headers["content-length"]))
            assert frame == b"mock-jpeg-frame"
    finally:
        server.close()


def test_loopback_client_reads_multipart_frames_and_measures_delivery_fps():
    hub = MjpegFrameHub()
    server = MjpegPreviewServer(hub=hub, host="127.0.0.1", port=0).start()
    client = preview_module.MjpegLoopbackClient(
        f"http://127.0.0.1:{server.port}/stream.mjpg", timeout_s=0.2
    ).start()
    try:
        deadline = time.monotonic() + 2.0
        while client.frames_received < 3 and time.monotonic() < deadline:
            hub.publish(b"mock-jpeg-frame")
            time.sleep(0.04)

        assert client.frames_received >= 2
        assert client.preview_client_fps is not None
        assert client.preview_client_fps > 0
        assert client.error is None
    finally:
        client.close()
        server.close()
