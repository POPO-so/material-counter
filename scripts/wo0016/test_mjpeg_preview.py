import sys
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mjpeg_preview import (  # noqa: E402
    MjpegFrameHub,
    MjpegPreviewServer,
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
