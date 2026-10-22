"""Thread-safe latest-frame hub and tiny standard-library MJPEG web preview."""
from __future__ import annotations

import os
import socket
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator
from urllib.request import urlopen

import cv2
import numpy as np

PREVIEW_FRAME_INTERVAL = 4
PREVIEW_JPEG_QUALITY = 70


_HTML_PAGE = """<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>WO-0017 · Industrial HUD Preview</title><style>
:root{color-scheme:dark;--bg:#07111b;--panel:#101d29;--line:#213747;--cyan:#2ce0d0;--muted:#8fa4b7;--text:#e7f0f7}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 10% 0,#173044 0,transparent 42%),var(--bg);color:var(--text);font:15px system-ui,sans-serif}
main{max-width:1240px;margin:30px auto;padding:0 20px}.top{display:flex;justify-content:space-between;align-items:end;margin-bottom:18px;gap:18px}
.eyebrow{color:var(--cyan);font:700 11px ui-monospace,monospace;letter-spacing:.16em}h1{margin:6px 0 0;font-size:25px;letter-spacing:.02em}
.badge{border:1px solid #305267;border-radius:99px;padding:8px 12px;color:#a8c2d2;font:12px ui-monospace,monospace}
.frame{background:#050a0e;border:1px solid var(--line);border-radius:16px;padding:10px;box-shadow:0 22px 70px #0008}.frame img{display:block;width:100%;height:auto;border-radius:9px}
.footer{display:flex;justify-content:space-between;color:var(--muted);font:12px ui-monospace,monospace;margin:12px 4px 0;gap:12px}
@media(max-width:650px){main{margin:16px auto;padding:0 10px}.top{align-items:start;flex-direction:column}h1{font-size:20px}}
</style></head><body><main>
<header class=\"top\"><div><div class=\"eyebrow\">POPO-SO / MATERIAL COUNTER / WO-0017</div><h1>Industrial HUD Preview</h1></div><span class=\"badge\">LIVE · MJPEG</span></header>
<section class=\"frame\"><img src=\"/stream.mjpg\" alt=\"Camera image with labeled binary mask PIP\"></section>
<footer class=\"footer\"><span>Read-only preview stream · no formal sampling</span><span>The frame HUD identifies the PIP source</span></footer>
</main></body></html>""".encode("utf-8")


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
                         label: str = "TOTAL COUNT", count_axis: str = "y",
                         line_x: int = 160, source_gray: np.ndarray | None = None,
                         hysteresis: float = 2.0,
                         pip_label: str = "PL DMA MASK",
                         tracks: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
                         track_trails: dict[int, tuple[tuple[float, float], ...]] | None = None
                         ) -> np.ndarray:
    """Render the Gray8 camera frame with tracking HUD and Binary8 DMA-mask PIP."""
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
    if not isinstance(pip_label, str) or not pip_label.strip():
        raise ValueError("pip_label must be non-empty text")
    if (isinstance(hysteresis, bool) or not isinstance(hysteresis, (int, float))
            or not np.isfinite(hysteresis) or hysteresis < 0):
        raise ValueError("hysteresis must be a finite nonnegative number")

    if source_gray is None:
        background = image
    else:
        background = np.asarray(source_gray)
        if background.dtype != np.uint8 or background.ndim != 2 or background.shape != image.shape:
            raise ValueError("source_gray must be a uint8 Gray8 frame with shape (240, 320)")
    frame = cv2.cvtColor(background, cv2.COLOR_GRAY2BGR)
    if count_axis not in ("x", "y") or not isinstance(line_x, int) or not 0 <= line_x < image.shape[1]:
        raise ValueError("invalid count axis or line_x")
    start, end = (((line_x, 0), (line_x, image.shape[0]-1)) if count_axis == "x"
                  else ((0, int(line_y)), (image.shape[1]-1, int(line_y))))
    cv2.line(frame, start, end,
             (0, 0, 255), 2, lineType=cv2.LINE_8)
    boundary_values = (line_x - hysteresis, line_x + hysteresis) if count_axis == "x" else (
        line_y - hysteresis, line_y + hysteresis
    )
    for boundary in boundary_values:
        boundary = int(round(boundary))
        if count_axis == "x" and 0 <= boundary < image.shape[1]:
            for y in range(0, image.shape[0], 10):
                cv2.line(frame, (boundary, y), (boundary, min(y + 4, image.shape[0] - 1)),
                         (0, 210, 255), 1, lineType=cv2.LINE_8)
        elif count_axis == "y" and 0 <= boundary < image.shape[0]:
            for x in range(0, image.shape[1], 10):
                cv2.line(frame, (x, boundary), (min(x + 4, image.shape[1] - 1), boundary),
                         (0, 210, 255), 1, lineType=cv2.LINE_8)

    for track in tracks:
        if track.get("finished") or track.get("missed", 0):
            continue
        detection = track.get("detection", {})
        if not all(key in detection for key in ("x_min", "x_max", "y_min", "y_max")):
            continue
        box = (int(detection["x_min"]), int(detection["y_min"]),
               int(detection["x_max"]), int(detection["y_max"]))
        color = (50, 220, 130) if track.get("counted") else (255, 180, 0)
        cv2.rectangle(frame, (box[0], box[1]), (box[2], box[3]), color, 1, lineType=cv2.LINE_8)
        cv2.putText(frame, f"T{int(track.get('track_id', 0))}",
                    (box[0], max(10, box[1] - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.32,
                    color, 1, lineType=cv2.LINE_AA)

    for points in (track_trails or {}).values():
        if len(points) >= 2:
            polyline = np.asarray([(int(round(x)), int(round(y))) for x, y in points],
                                  dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(frame, [polyline], False, (255, 255, 0), 1, lineType=cv2.LINE_AA)
            cv2.circle(frame, tuple(polyline[-1, 0]), 2, (255, 255, 0), -1)

    cv2.rectangle(frame, (5, 6), (min(frame.shape[1] - 1, 210), 32), (12, 25, 35), -1)
    cv2.putText(frame, f"{label}  {int(total_count):03d}", (10, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, lineType=cv2.LINE_AA)

    pip_x, pip_y, pip_width, pip_height = 220, 8, 92, 70
    cv2.rectangle(frame, (pip_x - 2, pip_y - 2),
                  (pip_x + pip_width + 2, pip_y + pip_height + 17), (12, 25, 35), -1)
    cv2.putText(frame, pip_label, (pip_x + 2, pip_y + 11),
                cv2.FONT_HERSHEY_SIMPLEX, 0.31, (44, 224, 208), 1, lineType=cv2.LINE_AA)
    pip = cv2.resize(image, (pip_width, pip_height), interpolation=cv2.INTER_NEAREST)
    pip = cv2.cvtColor(pip, cv2.COLOR_GRAY2BGR)
    frame[pip_y + 15:pip_y + 15 + pip_height,
          pip_x:pip_x + pip_width] = pip
    cv2.rectangle(frame, (pip_x, pip_y + 15),
                  (pip_x + pip_width - 1, pip_y + 15 + pip_height - 1),
                  (44, 224, 208), 1, lineType=cv2.LINE_8)
    return frame


def render_preview_jpeg(mask: np.ndarray, *, total_count: int, line_y: int,
                        quality: int = 85, count_label: str = "TOTAL COUNT",
                        count_axis: str = "y", line_x: int = 160,
                        source_gray: np.ndarray | None = None, hysteresis: float = 2.0,
                        pip_label: str = "PL DMA MASK",
                        tracks: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
                        track_trails: dict[int, tuple[tuple[float, float], ...]] | None = None
                        ) -> bytes:
    if isinstance(quality, bool) or not isinstance(quality, (int, np.integer)) or not 1 <= quality <= 100:
        raise ValueError("JPEG quality must be an integer in 1..100")
    frame = render_preview_frame(mask, total_count=total_count, line_y=line_y,
                                 label=count_label,
                                 count_axis=count_axis, line_x=line_x,
                                 source_gray=source_gray, hysteresis=hysteresis,
                                 pip_label=pip_label,
                                 tracks=tracks, track_trails=track_trails)
    encoded, payload = cv2.imencode(".jpg", frame,
                                    [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not encoded:
        raise RuntimeError("OpenCV failed to encode the preview JPEG")
    return payload.tobytes()


class PeriodicMjpegPublisher:
    """Copy preview snapshots quickly and encode them off the counting path."""

    def __init__(self, *, hub: MjpegFrameHub, line_y: int,
                 interval: int = PREVIEW_FRAME_INTERVAL,
                 quality: int = PREVIEW_JPEG_QUALITY, count_label: str = "TOTAL COUNT",
                 count_axis: str = "y", line_x: int = 160,
                 hysteresis: float = 2.0, pip_label: str = "PL DMA MASK"):
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
            raise ValueError("preview interval must be a positive integer")
        if isinstance(quality, bool) or not isinstance(quality, int) or not 1 <= quality <= 100:
            raise ValueError("JPEG quality must be an integer in 1..100")
        self.hub = hub
        self.line_y = line_y
        if not isinstance(count_label, str) or not count_label.strip():
            raise ValueError("count_label must be non-empty text")
        self.count_label = count_label
        self.count_axis = count_axis
        self.line_x = line_x
        self.hysteresis = float(hysteresis)
        if not isinstance(pip_label, str) or not pip_label.strip():
            raise ValueError("pip_label must be non-empty text")
        self.pip_label = pip_label
        self.interval = interval
        self.quality = quality
        self.encoded_frames = 0
        self.handoff_timings_ms: list[float] = []
        self.background_encode_timings_ms: list[float] = []
        self.encode_errors: list[str] = []
        self._condition = threading.Condition()
        self._latest_mask = np.empty((240, 320), dtype=np.uint8)
        self._encode_mask = np.empty((240, 320), dtype=np.uint8)
        self._latest_gray = np.empty((240, 320), dtype=np.uint8)
        self._encode_gray = np.empty((240, 320), dtype=np.uint8)
        self._latest_tracks: tuple[dict[str, Any], ...] = ()
        self._track_trails: dict[int, list[tuple[float, float]]] = defaultdict(list)
        self._latest_metadata: tuple[int, int, int] | None = None
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

    def __call__(self, result: Any, mask: np.ndarray,
                 source_gray: np.ndarray | None = None) -> None:
        started = time.perf_counter()
        try:
            frame_id = int(result.frame_id)
            if frame_id % self.interval:
                return
            image = np.asarray(mask)
            if image.dtype != np.uint8 or image.shape != (240, 320):
                raise ValueError("preview snapshot must be a uint8 array with shape (240, 320)")
            gray = image if source_gray is None else np.asarray(source_gray)
            if gray.dtype != np.uint8 or gray.shape != (240, 320):
                raise ValueError("preview Gray8 snapshot must have shape (240, 320)")
            with self._condition:
                if self._closing or self._closed:
                    return
                np.copyto(self._latest_mask, image, casting="no")
                np.copyto(self._latest_gray, gray, casting="no")
                self._latest_tracks = tuple(getattr(result, "tracks", ()))
                for track in self._latest_tracks:
                    if track.get("finished") or track.get("missed", 0):
                        continue
                    detection = track.get("detection", {})
                    track_id = int(track.get("track_id", 0))
                    if "centroid_x" in detection and "centroid_y" in detection:
                        trail = self._track_trails[track_id]
                        trail.append((float(detection["centroid_x"]),
                                      float(detection["centroid_y"])))
                        del trail[:-8]
                self._latest_metadata = (frame_id, int(result.total_count), self.line_y)
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
                np.copyto(self._encode_mask, self._latest_mask, casting="no")
                np.copyto(self._encode_gray, self._latest_gray, casting="no")
                tracks = self._latest_tracks
                trails = {track_id: tuple(points)
                          for track_id, points in self._track_trails.items()}
                self._encoded_sequence = sequence

            started = time.perf_counter()
            try:
                jpeg = render_preview_jpeg(
                    self._encode_mask,
                    total_count=total_count,
                    line_y=line_y,
                    quality=self.quality,
                    count_label=self.count_label,
                    count_axis=self.count_axis, line_x=self.line_x,
                    source_gray=self._encode_gray, hysteresis=self.hysteresis,
                    pip_label=self.pip_label,
                    tracks=tracks, track_trails=trails,
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
