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
    # LINE_AA glyph coverage varies across OpenCV builds (peak can be 251).
    text_pixels = preview[18:30, 8:210].astype(np.int16)
    assert np.any((text_pixels.min(axis=2) >= 230)
                  & (np.ptp(text_pixels, axis=2) <= 4))


def test_x160_line_and_effective_roi_border_are_drawn_on_mjpeg_frame():
    frame = render_preview_frame(np.zeros((240, 320), np.uint8), total_count=0,
                                 line_y=120, count_axis="x", line_x=160,
                                 roi_top=50, roi_bottom=190)
    assert tuple(frame[110, 160]) == (0, 0, 255)
    assert tuple(frame[50, 3]) == (50, 220, 130)
    assert tuple(frame[189, 3]) == (50, 220, 130)


def test_preview_jpeg_is_valid_and_invalid_binary_masks_are_rejected():
    mask = np.zeros((240, 320), dtype=np.uint8)

    jpeg = render_preview_jpeg(mask, total_count=0, line_y=120)

    assert jpeg[:2] == b"\xff\xd8"
    with pytest.raises(ValueError, match="Binary8"):
        render_preview_jpeg(np.ones((240, 320), dtype=np.uint8), total_count=0, line_y=120)


def test_industrial_hud_uses_gray_camera_background_mask_pip_and_tracking_overlay():
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[100:108, 40:48] = 0xFF
    gray = np.full((240, 320), 72, dtype=np.uint8)
    tracks = ({
        "track_id": 7,
        "detection": {"x_min": 128, "x_max": 140, "y_min": 110, "y_max": 120,
                      "centroid_x": 134.0, "centroid_y": 115.0, "area": 80},
        "missed": 0,
        "finished": False,
        "armed": True,
        "counted": False,
    },)

    frame = render_preview_frame(mask, total_count=3, line_y=120, count_axis="x",
                                 line_x=160, source_gray=gray, hysteresis=2.0,
                                 tracks=tracks, track_trails={7: ((120, 115), (128, 115))})

    assert frame.shape == (240, 320, 3)
    assert tuple(frame[80, 20]) == (72, 72, 72)
    # The top-right inset is rendered from this frame's DMA Binary8 mask.
    assert np.any(np.all(frame[34:82, 224:318] == (255, 255, 255), axis=2))
    # The line and hysteresis band have distinct colors around the count axis.
    assert tuple(frame[100, 160]) == (0, 0, 255)
    assert np.any(np.all(frame[:, 158:159] == (0, 210, 255), axis=2))
    # A track box and trail are visible without changing the underlying DMA mask.
    assert np.any(np.all(frame[108:123, 126:143] == (255, 180, 0), axis=2))
    assert np.any(np.all(frame[112:119, 118:131] == (255, 255, 0), axis=2))


def test_preview_hud_labels_a_software_threshold_pip(monkeypatch):
    labels = []
    original_put_text = preview_module.cv2.putText

    def capture_label(image, text, *args, **kwargs):
        labels.append(text)
        return original_put_text(image, text, *args, **kwargs)

    monkeypatch.setattr(preview_module.cv2, "putText", capture_label)
    render_preview_frame(np.zeros((240, 320), dtype=np.uint8), total_count=0,
                         line_y=120, source_gray=np.full((240, 320), 96, np.uint8),
                         pip_label="SIM TH=128")

    assert "SIM TH=128" in labels


def test_preview_track_labels_include_each_detected_pixel_area(monkeypatch):
    labels = []
    original_put_text = preview_module.cv2.putText

    def capture_label(image, text, *args, **kwargs):
        labels.append(text)
        return original_put_text(image, text, *args, **kwargs)

    monkeypatch.setattr(preview_module.cv2, "putText", capture_label)
    mask = np.zeros((240, 320), dtype=np.uint8)
    tracks = (
        {"track_id": 1, "detection": {"x_min": 20, "x_max": 34,
                                        "y_min": 30, "y_max": 44, "area": 135}},
        {"track_id": 2, "detection": {"x_min": 40, "x_max": 49,
                                        "y_min": 30, "y_max": 39, "area": 52}},
    )

    frame = render_preview_frame(mask, total_count=0, line_y=120, tracks=tracks)

    assert "T1 135px" in labels
    assert "T2 52px" in labels
    assert np.any(np.all(frame[30:45, 20:35] == (255, 180, 0), axis=2))
    assert np.any(np.all(frame[30:40, 40:50] == (255, 180, 0), axis=2))


def test_periodic_publisher_encodes_lazily_for_clients_on_a_worker_thread(monkeypatch):
    import mjpeg_preview

    encoded = []
    caller_thread = threading.get_ident()

    def fake_render(mask, *, total_count, line_y, quality, count_label, count_axis, line_x,
                    source_gray, hysteresis, pip_label, tracks, track_trails):
        encoded.append((total_count, line_y, quality, threading.get_ident(),
                        int(source_gray[0, 0]), hysteresis, count_label, pip_label,
                        len(tracks), len(track_trails)))
        return b"jpeg"

    monkeypatch.setattr(mjpeg_preview, "render_preview_jpeg", fake_render)
    hub = MjpegFrameHub()
    publisher = PeriodicMjpegPublisher(hub=hub, line_y=120,
                                      count_label="SIM COUNT", pip_label="SIM TH=128")
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
        assert [row[4:] for row in encoded] == [
            (0, 2.0, "SIM COUNT", "SIM TH=128", 0, 0),
            (0, 2.0, "SIM COUNT", "SIM TH=128", 0, 0),
        ]
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

    def slow_render(mask, *, total_count, line_y, quality, count_label, count_axis,
                    line_x, source_gray, hysteresis, pip_label, tracks, track_trails):
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
