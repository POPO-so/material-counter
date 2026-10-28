import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
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
    # The center line remains a single pixel so both hysteresis edges stay visible.
    assert tuple(frame[40, 160]) == (0, 0, 255)
    assert tuple(frame[40, 159]) == (0, 0, 0)
    assert tuple(frame[40, 161]) == (0, 0, 0)
    assert np.any(np.all(frame[:, 158:159] == (0, 210, 255), axis=2))
    assert np.any(np.all(frame[:, 162:163] == (0, 210, 255), axis=2))
    # A track box and trail are visible without changing the underlying DMA mask.
    assert np.any(np.all(frame[108:123, 126:143] == (255, 180, 0), axis=2))
    assert np.any(np.all(frame[112:119, 118:131] == (255, 255, 0), axis=2))


def test_x160_hud_label_has_a_high_contrast_backdrop_and_larger_type():
    calls = []
    original_put_text = preview_module.cv2.putText

    def capture_label(image, text, origin, font, scale, color, thickness, *args, **kwargs):
        if text == "X=160":
            calls.append((origin, scale, color, thickness))
        return original_put_text(image, text, origin, font, scale, color, thickness,
                                 *args, **kwargs)

    preview_module.cv2.putText = capture_label
    try:
        frame = render_preview_frame(np.zeros((240, 320), np.uint8), total_count=0,
                                     line_y=120, count_axis="x", line_x=160,
                                     source_gray=np.full((240, 320), 72, np.uint8))
    finally:
        preview_module.cv2.putText = original_put_text

    assert len(calls) == 1
    (x, baseline), scale, color, thickness = calls[0]
    assert scale >= 0.55
    assert tuple(color) == (255, 255, 255)
    assert thickness >= 2
    assert tuple(frame[baseline - 10, x - 3]) == (12, 25, 35)


def test_compact_preview_keeps_count_line_unobstructed_and_omits_mask_pip(monkeypatch):
    labels = []
    original_put_text = preview_module.cv2.putText

    def capture_label(image, text, origin, font, scale, color, thickness, *args, **kwargs):
        labels.append((text, origin, scale))
        return original_put_text(image, text, origin, font, scale, color, thickness,
                                 *args, **kwargs)

    monkeypatch.setattr(preview_module.cv2, "putText", capture_label)
    frame = render_preview_frame(
        np.zeros((240, 320), np.uint8), total_count=5, line_y=120,
        count_axis="x", line_x=160, preview_overlay="compact", frame_id=123,
    )

    assert not any(text == "PL DMA MASK" for text, _, _ in labels)
    assert not any(text == "X=160" and scale >= 0.5 for text, _, scale in labels)
    assert any(text == "X=160" and origin[1] >= 220 and scale <= 0.35
               for text, origin, scale in labels)
    assert any("F123" in text for text, _, _ in labels)
    assert tuple(frame[40, 160]) == (0, 0, 255)


def test_frame_hub_binds_jpeg_identity_and_clear_removes_identity():
    hub = MjpegFrameHub()
    try:
        hub.publish(b"identified-jpeg", dimensions=(320, 240), frame_id=37,
                    total_count=12)
        snapshot = hub.snapshot()
        assert snapshot["frame_id"] == 37
        assert snapshot["total_count"] == 12
        assert hub.wait_for_next(after_sequence=0, timeout_s=0) == (1, b"identified-jpeg")
        hub.clear()
        assert hub.snapshot()["frame_id"] is None
        assert hub.snapshot()["total_count"] is None
    finally:
        hub.close()


def test_x160_count_line_and_hysteresis_edges_remain_distinct_in_stream_jpeg():
    gray = np.full((240, 320), 72, dtype=np.uint8)
    encoded = render_preview_jpeg(np.zeros((240, 320), np.uint8), total_count=0,
                                  line_y=120, quality=70, count_axis="x", line_x=160,
                                  source_gray=gray, hysteresis=2.0)
    frame = preview_module.cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8),
                                        preview_module.cv2.IMREAD_COLOR)

    left_edge = frame[40, 158]
    left_gap = frame[40, 159]
    center = frame[40, 160]
    right_gap = frame[40, 161]
    right_edge = frame[40, 162]
    assert left_edge[0] > 120 and left_edge[1] > 150
    assert int(left_gap.max()) < 100
    assert center[2] > center[1] + 50
    assert int(right_gap.max()) < 100
    assert right_edge[0] > 120 and right_edge[1] > 150


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


def test_periodic_publisher_encodes_lazily_for_clients_on_a_worker_thread(monkeypatch):
    import mjpeg_preview

    encoded = []
    caller_thread = threading.get_ident()

    def fake_render(mask, *, total_count, line_y, quality, count_label, count_axis, line_x,
                    source_gray, hysteresis, pip_label, tracks, track_trails, **kwargs):
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
            for frame_id in range(4, 8):
                publisher(type("Frame", (), {"frame_id": frame_id,
                                              "total_count": frame_id // 3})(), mask)
            first = hub.wait_for_next(after_sequence=0, timeout_s=2.0)
            assert first == (1, b"jpeg")
            for frame_id in range(8, 12):
                publisher(type("Frame", (), {"frame_id": frame_id,
                                              "total_count": frame_id // 3})(), mask)
            second = hub.wait_for_next(after_sequence=1, timeout_s=2.0)
            assert second == (2, b"jpeg")

        assert publisher.interval == 4
        assert publisher.quality == 70
        assert publisher.encoded_frames == 2
        assert [row[:3] for row in encoded] == [(1, 120, 70), (2, 120, 70)]
        assert [row[4:] for row in encoded] == [
            (0, 2.0, "SIM COUNT", "SIM TH=128", 0, 0),
            (0, 2.0, "SIM COUNT", "SIM TH=128", 0, 0),
        ]
        assert all(thread_id != caller_thread for *_, thread_id in encoded)
        assert hub.sequence == 2
        assert len(publisher.handoff_timings_ms) == 12
        assert len(publisher.background_encode_timings_ms) == 2
    finally:
        close = getattr(publisher, "close", None)
        if callable(close):
            close()
        hub.close()


def test_periodic_publisher_skips_full_frame_snapshot_copies_without_clients(monkeypatch):
    hub = MjpegFrameHub()
    publisher = PeriodicMjpegPublisher(hub=hub, line_y=120, interval=1)
    copies = []
    monkeypatch.setattr(preview_module.np, "copyto",
                        lambda *args, **kwargs: copies.append((args, kwargs)))
    mask = np.zeros((240, 320), dtype=np.uint8)
    gray = np.zeros_like(mask)
    result = type("Frame", (), {"frame_id": 1, "total_count": 0})()

    try:
        publisher(result, mask, source_gray=gray)

        assert copies == []
        assert hub.sequence == 0
        assert publisher.encoded_frames == 0
    finally:
        publisher.close()
        hub.close()


def test_frame_hub_caps_streaming_client_leases():
    hub = MjpegFrameHub(max_stream_clients=2)
    try:
        with hub.streaming_client() as first:
            assert first is True
            with hub.streaming_client() as second:
                assert second is True
                with hub.streaming_client() as third:
                    assert third is False
                assert hub.stream_clients == 2
            assert hub.stream_clients == 1
        assert hub.stream_clients == 0
    finally:
        hub.close()


def test_preview_callback_returns_while_background_jpeg_encode_is_blocked(monkeypatch):
    import mjpeg_preview

    encode_started = threading.Event()
    release_encode = threading.Event()
    callback_returned = threading.Event()

    def slow_render(mask, *, total_count, line_y, quality, count_label, count_axis,
                    line_x, source_gray, hysteresis, pip_label, tracks, track_trails, **kwargs):
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


def test_mjpeg_publisher_and_loopback_measurement_histories_have_bounded_defaults():
    hub = MjpegFrameHub()
    publisher = PeriodicMjpegPublisher(hub=hub, line_y=120)
    client = preview_module.MjpegLoopbackClient("http://127.0.0.1:8080/stream.mjpg")
    try:
        assert publisher.history_limit == 2000
        assert publisher.handoff_timings_ms.maxlen == 2000
        assert publisher.background_encode_timings_ms.maxlen == 2000
        assert publisher.encode_errors.maxlen == 2000
        assert client.frame_sequences.maxlen == 2000
        assert client.frames_received == 0
        mask = np.zeros((240, 320), dtype=np.uint8)
        for frame_id in range(12):
            publisher(type("Frame", (), {"frame_id": frame_id,
                                          "total_count": 0})(), mask)
        assert len(publisher.handoff_timings_ms) == 12
        assert publisher.total_handoff_samples == 12
    finally:
        publisher.close()
        hub.close()


def test_mjpeg_encode_error_history_is_bounded_while_counters_remain_cumulative(monkeypatch):
    import mjpeg_preview

    def fail_render(*args, **kwargs):
        raise ValueError("test encoder error")

    monkeypatch.setattr(mjpeg_preview, "render_preview_jpeg", fail_render)
    hub = MjpegFrameHub()
    publisher = PeriodicMjpegPublisher(hub=hub, line_y=120, interval=1,
                                        history_limit=3)
    mask = np.zeros((240, 320), dtype=np.uint8)
    try:
        with hub.streaming_client():
            for frame_id in range(6):
                publisher(type("Frame", (), {"frame_id": frame_id,
                                              "total_count": 0})(), mask)
                deadline = time.monotonic() + 1.0
                while (publisher.total_encode_errors < frame_id + 1
                       or publisher.total_background_encode_samples < frame_id + 1):
                    assert time.monotonic() < deadline
                    time.sleep(0.001)

        assert len(publisher.encode_errors) == 3
        assert publisher.total_encode_errors == 6
        assert len(publisher.background_encode_timings_ms) == 3
        assert publisher.total_background_encode_samples == 6
    finally:
        publisher.close()
        hub.close()


def test_mjpeg_timing_summaries_survive_rolling_history_eviction():
    hub = MjpegFrameHub()
    publisher = PeriodicMjpegPublisher(hub=hub, line_y=120, history_limit=3)
    handoff_samples = [3.0, 1.5, 7.0, 2.0, 11.0, 5.0]
    encode_samples = [6.0, 3.0, 14.0, 4.0, 22.0, 10.0]
    try:
        for elapsed_ms in handoff_samples:
            publisher._record_handoff_timing(elapsed_ms)
        for elapsed_ms in encode_samples:
            publisher._record_background_encode_timing(elapsed_ms)

        assert list(publisher.handoff_timings_ms) == handoff_samples[-3:]
        assert publisher.total_handoff_samples == 6
        assert publisher.total_handoff_ms == sum(handoff_samples)
        assert publisher.min_handoff_ms == min(handoff_samples)
        assert publisher.max_handoff_ms == max(handoff_samples)

        assert list(publisher.background_encode_timings_ms) == encode_samples[-3:]
        assert publisher.total_background_encode_samples == 6
        assert publisher.total_background_encode_ms == sum(encode_samples)
        assert publisher.min_background_encode_ms == min(encode_samples)
        assert publisher.max_background_encode_ms == max(encode_samples)
    finally:
        publisher.close()
        hub.close()


def test_mjpeg_publisher_close_times_out_and_names_live_encoder(monkeypatch):
    import mjpeg_preview

    encode_started = threading.Event()
    release_encode = threading.Event()

    def blocked_render(*args, **kwargs):
        encode_started.set()
        assert release_encode.wait(2.0)
        return b"blocked-jpeg"

    monkeypatch.setattr(mjpeg_preview, "render_preview_jpeg", blocked_render)
    hub = MjpegFrameHub()
    publisher = PeriodicMjpegPublisher(hub=hub, line_y=120)
    mask = np.zeros((240, 320), dtype=np.uint8)

    try:
        with hub.streaming_client():
            publisher(type("Frame", (), {"frame_id": 0, "total_count": 1})(), mask)
            assert encode_started.wait(1.0)
            survivors = publisher.close(timeout_s=0.05)
            assert survivors == ("wo0016-mjpeg-encoder",)
            assert publisher.worker_alive

        release_encode.set()
        assert publisher.close(timeout_s=1.0) == ()
        assert not publisher.worker_alive
    finally:
        release_encode.set()
        publisher.close(timeout_s=1.0)
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
    hub.publish(b"mock-jpeg-frame", frame_id=29, total_count=8)
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
            assert part_headers["x-frame-id"] == "29"
            assert part_headers["x-total-count"] == "8"
            frame = response.read(int(part_headers["content-length"]))
            assert frame == b"mock-jpeg-frame"
    finally:
        server.close()


def test_http_server_rejects_stream_clients_over_the_hub_limit():
    hub = MjpegFrameHub(max_stream_clients=2)
    server = MjpegPreviewServer(hub=hub, host="127.0.0.1", port=0).start()
    responses = []
    url = f"http://127.0.0.1:{server.port}/stream.mjpg"
    try:
        for _ in range(2):
            responses.append(urlopen(url, timeout=2))
        deadline = time.monotonic() + 1.0
        while hub.stream_clients != 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert hub.stream_clients == 2

        with pytest.raises(HTTPError) as error:
            urlopen(url, timeout=2)

        assert error.value.code == 503
        assert hub.stream_clients == 2
    finally:
        for response in responses:
            response.close()
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
        assert client.close(timeout_s=1.0) == ()
        server.close()


def test_loopback_client_close_reports_live_worker_and_can_retry(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocked_reader(self):
        started.set()
        release.wait(2.0)

    monkeypatch.setattr(preview_module.MjpegLoopbackClient, "_read_stream", blocked_reader)
    client = preview_module.MjpegLoopbackClient("http://127.0.0.1:8080/stream.mjpg").start()
    thread = client._thread
    try:
        assert started.wait(1.0)
        assert client.close(timeout_s=0.05) == ("wo0016-mjpeg-loopback-client",)
        assert client.worker_alive
        assert client._thread is thread
    finally:
        release.set()

    assert client.close(timeout_s=1.0) == ()
    assert not client.worker_alive
    assert client._thread is thread


def test_preview_server_close_reports_live_workers_and_can_retry(monkeypatch):
    shutdown_started = threading.Event()
    release_shutdown = threading.Event()
    server = MjpegPreviewServer(hub=MjpegFrameHub(), host="127.0.0.1", port=0).start()
    http_thread = server._thread
    original_shutdown = server._server.shutdown

    def blocked_shutdown():
        shutdown_started.set()
        release_shutdown.wait(2.0)
        original_shutdown()

    monkeypatch.setattr(server._server, "shutdown", blocked_shutdown)
    try:
        survivors = server.close(timeout_s=0.05)
        assert shutdown_started.wait(1.0)
        assert set(survivors) == {"wo0016-mjpeg-shutdown", "wo0016-mjpeg"}
        assert server._thread is http_thread
        assert server.worker_threads_alive == (
            "wo0016-mjpeg-shutdown", "wo0016-mjpeg")
    finally:
        release_shutdown.set()

    assert server.close(timeout_s=1.0) == ()
    assert server._thread is None
    assert server._server is None


def test_preview_server_close_finishes_without_surviving_threads():
    server = MjpegPreviewServer(hub=MjpegFrameHub(), host="127.0.0.1", port=0).start()

    assert server.close(timeout_s=1.0) == ()
    assert server.worker_threads_alive == ()
    assert server._thread is None
