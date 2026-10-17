"""Thread-safe latest-frame hub and tiny standard-library MJPEG web preview."""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import cv2
import numpy as np

PREVIEW_FRAME_INTERVAL = 4
PREVIEW_JPEG_QUALITY = 70


_HTML_PAGE = b"""<!doctype html>
<html><head><meta charset=\"utf-8\"><title>Material Counter</title></head>
<body style=\"margin:0;background:#111;color:#eee;font:16px sans-serif\">
<main style=\"max-width:960px;margin:1rem auto;text-align:center\">
<h1>Material Counter</h1><img src=\"/stream.mjpg\" alt=\"Live Binary8 mask\" style=\"width:100%;height:auto\">
</main></body></html>"""


class MjpegFrameHub:
    """Keep only the newest frame so a slow browser never queues stale video."""

    def __init__(self):
        self._condition = threading.Condition()
        self._sequence = 0
        self._jpeg: bytes | None = None
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    @property
    def sequence(self) -> int:
        with self._condition:
            return self._sequence

    def publish(self, jpeg: bytes | bytearray | memoryview) -> int:
        if not isinstance(jpeg, (bytes, bytearray, memoryview)) or not jpeg:
            raise ValueError("published JPEG must be non-empty bytes")
        with self._condition:
            if self._closed:
                raise RuntimeError("frame hub is closed")
            self._jpeg = bytes(jpeg)
            self._sequence += 1
            self._condition.notify_all()
            return self._sequence

    def wait_for_next(self, *, after_sequence: int, timeout_s: float = 1.0
                      ) -> tuple[int, bytes] | None:
        if after_sequence < 0 or timeout_s < 0:
            raise ValueError("sequence and timeout must be nonnegative")
        with self._condition:
            ready = self._condition.wait_for(
                lambda: self._sequence > after_sequence or self._closed,
                timeout=timeout_s,
            )
            if not ready or self._jpeg is None or self._sequence <= after_sequence:
                return None
            return self._sequence, self._jpeg

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


def render_preview_frame(mask: np.ndarray, *, total_count: int, line_y: int,
                         label: str = "Total Count") -> np.ndarray:
    """Render the Binary8 mask with a red line and a readable live count."""
    image = np.asarray(mask)
    if image.dtype != np.uint8 or image.ndim != 2 or image.shape != (240, 320):
        raise ValueError("mask must be a uint8 array with shape (240, 320)")
    if np.any((image != 0) & (image != 0xFF)):
        raise ValueError("mask values must be Binary8 0x00 or 0xFF")
    if isinstance(total_count, bool) or not isinstance(total_count, (int, np.integer)) or total_count < 0:
        raise ValueError("total_count must be a nonnegative integer")
    if isinstance(line_y, bool) or not isinstance(line_y, (int, np.integer)) or not 0 <= line_y < image.shape[0]:
        raise ValueError("line_y must be an integer inside the mask")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("label must be non-empty text")

    frame = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    cv2.line(frame, (0, int(line_y)), (image.shape[1] - 1, int(line_y)),
             (0, 0, 255), 2, lineType=cv2.LINE_8)
    text = f"{label}: {int(total_count)}"
    cv2.rectangle(frame, (4, 6), (min(frame.shape[1] - 1, 244), 34), (24, 24, 24), -1)
    cv2.putText(frame, text, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.58,
                (255, 255, 255), 2, lineType=cv2.LINE_AA)
    return frame


def render_preview_jpeg(mask: np.ndarray, *, total_count: int, line_y: int,
                        quality: int = 85) -> bytes:
    if isinstance(quality, bool) or not isinstance(quality, (int, np.integer)) or not 1 <= quality <= 100:
        raise ValueError("JPEG quality must be an integer in 1..100")
    frame = render_preview_frame(mask, total_count=total_count, line_y=line_y)
    encoded, payload = cv2.imencode(".jpg", frame,
                                    [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not encoded:
        raise RuntimeError("OpenCV failed to encode the preview JPEG")
    return payload.tobytes()


class PeriodicMjpegPublisher:
    """Encode a preview every N frames while the caller processes every frame."""

    def __init__(self, *, hub: MjpegFrameHub, line_y: int,
                 interval: int = PREVIEW_FRAME_INTERVAL,
                 quality: int = PREVIEW_JPEG_QUALITY):
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
            raise ValueError("preview interval must be a positive integer")
        if isinstance(quality, bool) or not isinstance(quality, int) or not 1 <= quality <= 100:
            raise ValueError("JPEG quality must be an integer in 1..100")
        self.hub = hub
        self.line_y = line_y
        self.interval = interval
        self.quality = quality
        self.encoded_frames = 0
        self.encode_timings_ms: list[float] = []

    def __call__(self, result: Any, mask: np.ndarray) -> None:
        frame_id = result.frame_id
        if frame_id % self.interval:
            return
        started = time.perf_counter()
        jpeg = render_preview_jpeg(mask, total_count=result.total_count,
                                   line_y=self.line_y, quality=self.quality)
        self.hub.publish(jpeg)
        self.encode_timings_ms.append((time.perf_counter() - started) * 1000.0)
        self.encoded_frames += 1


class MjpegPreviewServer:
    """Serve an HTML page and multipart MJPEG at the requested host and port."""

    def __init__(self, *, hub: MjpegFrameHub | None = None,
                 host: str = "0.0.0.0", port: int = 8080):
        if not isinstance(host, str) or not host:
            raise ValueError("host must be non-empty text")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("port must be an integer in 0..65535")
        self.hub = hub or MjpegFrameHub()
        self.host = host
        self.requested_port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            return self.requested_port
        return int(self._server.server_address[1])

    def start(self) -> "MjpegPreviewServer":
        if self._server is not None:
            raise RuntimeError("preview server is already started")
        hub = self.hub

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:
                if self.path in ("/", "/index.html"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(_HTML_PAGE)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(_HTML_PAGE)
                    return
                if self.path != "/stream.mjpg":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                sequence = 0
                try:
                    while not hub.closed:
                        item = hub.wait_for_next(after_sequence=sequence, timeout_s=1.0)
                        if item is None:
                            continue
                        sequence, jpeg = item
                        header = (b"--frame\r\nContent-Type: image/jpeg\r\n"
                                  + f"Content-Length: {len(jpeg)}\r\nX-Frame-Sequence: {sequence}\r\n\r\n".encode("ascii"))
                        self.wfile.write(header)
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        self._server = ThreadingHTTPServer((self.host, self.requested_port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="wo0016-mjpeg", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self.hub.close()
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._server = None
        self._thread = None

    def __enter__(self) -> "MjpegPreviewServer":
        return self.start()

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()
