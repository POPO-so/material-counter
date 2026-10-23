"""Thread-safe latest-frame hub and tiny standard-library MJPEG web preview."""
from __future__ import annotations

import os
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator
from urllib.request import urlopen

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
        self._stream_clients = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    @property
    def sequence(self) -> int:
        with self._condition:
            return self._sequence

    @property
    def stream_clients(self) -> int:
        with self._condition:
            return self._stream_clients

    @contextmanager
    def streaming_client(self) -> Iterator[None]:
        with self._condition:
            if self._closed:
                raise RuntimeError("frame hub is closed")
            self._stream_clients += 1
            self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._stream_clients -= 1
                self._condition.notify_all()

    def wait_for_client(self, *, timeout_s: float = 0.1) -> bool:
        if timeout_s < 0:
            raise ValueError("timeout must be nonnegative")
        with self._condition:
            self._condition.wait_for(
                lambda: self._stream_clients > 0 or self._closed,
                timeout=timeout_s,
            )
            return self._stream_clients > 0

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


def render_calibration_jpeg(frame_bgr: np.ndarray, *, quality: int = PREVIEW_JPEG_QUALITY) -> bytes:
    frame = np.asarray(frame_bgr)
    if frame.dtype != np.uint8 or frame.shape != (240, 320, 3):
        raise ValueError("calibration preview must be uint8 BGR with shape (240, 320, 3)")
    if isinstance(quality, bool) or not isinstance(quality, (int, np.integer)) or not 1 <= quality <= 100:
        raise ValueError("JPEG quality must be an integer in 1..100")
    encoded, payload = cv2.imencode(".jpg", frame,
                                    [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not encoded:
        raise RuntimeError("OpenCV failed to encode the calibration JPEG")
    return payload.tobytes()


class PeriodicMjpegPublisher:
    """Copy preview snapshots quickly and encode them off the counting path."""

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
        self.handoff_timings_ms: list[float] = []
        self.background_encode_timings_ms: list[float] = []
        self.encode_errors: list[str] = []
        self._condition = threading.Condition()
        self._latest_mask = np.empty((240, 320), dtype=np.uint8)
        self._encode_mask = np.empty((240, 320), dtype=np.uint8)
        self._latest_calibration = np.empty((240, 320, 3), dtype=np.uint8)
        self._encode_calibration = np.empty((240, 320, 3), dtype=np.uint8)
        self._latest_metadata: tuple[int, int, int] | None = None
        self._latest_mode = "mask"
        self._snapshot_sequence = 0
        self._encoded_sequence = 0
        self._closing = False
        self._closed = False
        self._worker = threading.Thread(
            target=self._encode_pending,
            name="wo0016-mjpeg-encoder",
            daemon=True,
        )
        self._worker.start()

    def __call__(self, result: Any, mask: np.ndarray) -> None:
        started = time.perf_counter()
        try:
            frame_id = int(result.frame_id)
            if frame_id % self.interval:
                return
            image = np.asarray(mask)
            if image.dtype != np.uint8 or image.shape != (240, 320):
                raise ValueError("preview snapshot must be a uint8 array with shape (240, 320)")
            with self._condition:
                if self._closing or self._closed:
                    return
                np.copyto(self._latest_mask, image, casting="no")
                self._latest_metadata = (frame_id, int(result.total_count), self.line_y)
                self._latest_mode = "mask"
                self._snapshot_sequence += 1
                self._condition.notify_all()
        finally:
            self.handoff_timings_ms.append((time.perf_counter() - started) * 1000.0)

    def publish_calibration(self, frame_id: int, frame_bgr: np.ndarray) -> None:
        started = time.perf_counter()
        try:
            if isinstance(frame_id, bool) or not isinstance(frame_id, int) or frame_id < 0:
                raise ValueError("calibration frame_id must be a nonnegative integer")
            if frame_id % self.interval:
                return
            image = np.asarray(frame_bgr)
            if image.dtype != np.uint8 or image.shape != (240, 320, 3):
                raise ValueError("calibration snapshot must be uint8 BGR with shape (240, 320, 3)")
            with self._condition:
                if self._closing or self._closed:
                    return
                np.copyto(self._latest_calibration, image, casting="no")
                self._latest_metadata = (frame_id, 0, self.line_y)
                self._latest_mode = "calibration"
                self._snapshot_sequence += 1
                self._condition.notify_all()
        finally:
            self.handoff_timings_ms.append((time.perf_counter() - started) * 1000.0)

    def _encode_pending(self) -> None:
        if hasattr(os, "setpriority") and hasattr(threading, "get_native_id"):
            try:
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
            except OSError:
                pass
        while True:
            with self._condition:
                if self._snapshot_sequence <= self._encoded_sequence:
                    if self._closing:
                        return
                    self._condition.wait(timeout=0.1)
                    continue

            if not self.hub.wait_for_client(timeout_s=0.1):
                with self._condition:
                    if self._closing:
                        return
                continue

            with self._condition:
                if self._snapshot_sequence <= self._encoded_sequence:
                    continue
                if self._latest_metadata is None:
                    self._encoded_sequence = self._snapshot_sequence
                    continue
                sequence = self._snapshot_sequence
                frame_id, total_count, line_y = self._latest_metadata
                mode = self._latest_mode
                if mode == "calibration":
                    np.copyto(self._encode_calibration, self._latest_calibration, casting="no")
                else:
                    np.copyto(self._encode_mask, self._latest_mask, casting="no")
                self._encoded_sequence = sequence

            started = time.perf_counter()
            try:
                if mode == "calibration":
                    jpeg = render_calibration_jpeg(self._encode_calibration, quality=self.quality)
                else:
                    jpeg = render_preview_jpeg(
                        self._encode_mask,
                        total_count=total_count,
                        line_y=line_y,
                        quality=self.quality,
                    )
                self.hub.publish(jpeg)
                self.encoded_frames += 1
            except RuntimeError as exc:
                if not self.hub.closed:
                    self.encode_errors.append(f"{type(exc).__name__}: {exc}")
                    continue
                return
            except Exception as exc:
                self.encode_errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                self.background_encode_timings_ms.append(
                    (time.perf_counter() - started) * 1000.0
                )

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closing = True
            self._condition.notify_all()
        self._worker.join()
        with self._condition:
            self._closed = True


class MjpegLoopbackClient:
    """Read the local multipart endpoint and measure delivered preview frames."""

    def __init__(self, url: str, *, timeout_s: float = 0.5):
        if not isinstance(url, str) or not url.startswith("http://127.0.0.1:"):
            raise ValueError("loopback preview URL must use http://127.0.0.1")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            raise ValueError("timeout_s must be a positive number")
        self.url = url
        self.timeout_s = float(timeout_s)
        self.frames_received = 0
        self.frame_sequences: list[int] = []
        self.first_frame_time: float | None = None
        self.last_frame_time: float | None = None
        self.error: str | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._response: Any = None
        self._thread = threading.Thread(
            target=self._read_stream,
            name="wo0016-mjpeg-loopback-client",
            daemon=True,
        )

    @property
    def preview_client_fps(self) -> float | None:
        first = self.first_frame_time
        last = self.last_frame_time
        if self.frames_received < 2 or first is None or last is None or last <= first:
            return None
        return (self.frames_received - 1) / (last - first)

    def start(self) -> "MjpegLoopbackClient":
        if self._thread.is_alive():
            raise RuntimeError("loopback preview client is already running")
        self._thread.start()
        return self

    def _readline(self, response: Any) -> bytes | None:
        while not self._stop.is_set():
            try:
                return response.readline()
            except (socket.timeout, TimeoutError):
                continue
        return None

    @staticmethod
    def _read_exactly(response: Any, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = response.read(size - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)

    def _read_stream(self) -> None:
        try:
            with urlopen(self.url, timeout=self.timeout_s) as response:
                with self._lock:
                    self._response = response
                if response.status != 200:
                    raise RuntimeError(f"preview endpoint returned HTTP {response.status}")
                boundary = b"--frame"
                while not self._stop.is_set():
                    line = self._readline(response)
                    if line is None or not line:
                        break
                    if line.strip() != boundary:
                        continue
                    headers: dict[str, str] = {}
                    while not self._stop.is_set():
                        line = self._readline(response)
                        if line is None or not line:
                            return
                        if line in (b"\r\n", b"\n"):
                            break
                        key, separator, value = line.decode("ascii").partition(":")
                        if separator:
                            headers[key.strip().lower()] = value.strip()
                    size = int(headers["content-length"])
                    frame = self._read_exactly(response, size)
                    if len(frame) != size:
                        break
                    if "x-frame-sequence" in headers:
                        self.frame_sequences.append(int(headers["x-frame-sequence"]))
                    timestamp = time.perf_counter()
                    if self.first_frame_time is None:
                        self.first_frame_time = timestamp
                    self.last_frame_time = timestamp
                    self.frames_received += 1
                    self._readline(response)  # Consume the MIME separator CRLF.
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            if not self._stop.is_set():
                self.error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._response = None

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            response = self._response
        if response is not None:
            response.close()
        if self._thread.ident is not None:
            self._thread.join(timeout=max(1.0, self.timeout_s * 3))
            if self._thread.is_alive():
                raise RuntimeError("loopback preview client did not stop")


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
                    with hub.streaming_client():
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
                except (BrokenPipeError, ConnectionResetError, OSError, RuntimeError):
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
