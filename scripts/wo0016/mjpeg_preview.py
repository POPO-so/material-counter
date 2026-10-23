"""Thread-safe latest-frame hub and tiny standard-library MJPEG web preview."""
from __future__ import annotations

import os
import json
import ipaddress
import socket
import select
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator
from urllib.request import urlopen
from urllib.parse import urlsplit

import cv2
import numpy as np

PREVIEW_FRAME_INTERVAL = 4
PREVIEW_JPEG_QUALITY = 70


class MjpegFrameHub:
    """Keep only the newest frame so a slow browser never queues stale video."""

    def __init__(self):
        self._condition = threading.Condition()
        self._sequence = 0
        self._jpeg: bytes | None = None
        self._stream_clients = 0
        self._closed = False
        self._published_times: deque[float] = deque(maxlen=60)
        self._dimensions: tuple[int, int] | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            times = self._published_times
            age = None if not times else max(0.0, time.monotonic() - times[-1])
            span = times[-1] - times[0] if len(times) > 1 else 0
            return {"sequence": self._sequence, "clients": self._stream_clients,
                    "closed": self._closed, "age_seconds": age,
                    "width": None if self._dimensions is None else self._dimensions[0],
                    "height": None if self._dimensions is None else self._dimensions[1],
                    "fps": ((len(times) - 1) / span
                            if span > 0 and age <= 5 and not self._closed else None)}

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

    def publish(self, jpeg: bytes | bytearray | memoryview, *,
                dimensions: tuple[int, int] | None = None) -> int:
        if not isinstance(jpeg, (bytes, bytearray, memoryview)) or not jpeg:
            raise ValueError("published JPEG must be non-empty bytes")
        with self._condition:
            if self._closed:
                raise RuntimeError("frame hub is closed")
            self._jpeg = bytes(jpeg)
            self._published_times.append(time.monotonic())
            self._dimensions = dimensions
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
                         track_trails: dict[int, tuple[tuple[float, float], ...]] | None = None,
                         calibration: dict | None = None
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
    if calibration is not None:
        for contour in calibration.get("glare_contours", ()):
            points = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
            for start, end in zip(points, np.roll(points, -1, axis=0)):
                distance = float(np.linalg.norm(end - start))
                for offset in range(0, int(distance) + 1, 9):
                    a = start + (end-start) * min(offset / max(distance, 1), 1)
                    b = start + (end-start) * min((offset+4) / max(distance, 1), 1)
                    cv2.line(frame, tuple(np.rint(a).astype(int)), tuple(np.rint(b).astype(int)),
                             (0, 0, 255), 1)
        # Add a full-width footer to keep optical diagnostics clear of the image/PIP.
        frame = cv2.copyMakeBorder(frame, 0, 85, 0, 0, cv2.BORDER_CONSTANT, value=(12,25,35))
        bg = calibration.get("bg_mean")
        bg_text = "N/A" if bg is None else f"{bg:.1f}"
        lines = [
            f"BG Mean {bg_text} [20-45]  Pipeline FPS {calibration['fps']:.1f}",
            f"Over-128 Ratio {100*calibration['over_128_ratio']:.2f}% / Glare Area {calibration['glare_area_px']}px",
            f"Candidate Nut Blobs {calibration['candidate_nut_blobs']}  Areas {calibration['candidate_areas_px']}",
            calibration["status"],
        ]
        color = (0, 210, 255) if calibration.get("glare_area_px") else (
            (50, 220, 130) if calibration["status"].startswith("OPTICS READY") else (0, 210, 255))
        for index, text in enumerate(lines):
            scale = 0.35 if index < 3 else 0.36
            text_width = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0]
            scale *= min(1.0, 310 / max(1, text_width))
            cv2.putText(frame, text, (5, 256 + 19*index), cv2.FONT_HERSHEY_SIMPLEX,
                        scale, color, 1, lineType=cv2.LINE_AA)
    return frame


def render_preview_jpeg(mask: np.ndarray, *, total_count: int, line_y: int,
                        quality: int = 85, count_label: str = "TOTAL COUNT",
                        count_axis: str = "y", line_x: int = 160,
                        source_gray: np.ndarray | None = None, hysteresis: float = 2.0,
                        pip_label: str = "PL DMA MASK",
                        tracks: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
                        track_trails: dict[int, tuple[tuple[float, float], ...]] | None = None,
                        calibration: dict | None = None
                        ) -> bytes:
    if isinstance(quality, bool) or not isinstance(quality, (int, np.integer)) or not 1 <= quality <= 100:
        raise ValueError("JPEG quality must be an integer in 1..100")
    frame = render_preview_frame(mask, total_count=total_count, line_y=line_y,
                                 label=count_label,
                                 count_axis=count_axis, line_x=line_x,
                                 source_gray=source_gray, hysteresis=hysteresis,
                                 pip_label=pip_label,
                                 tracks=tracks, track_trails=track_trails,
                                 **({"calibration": calibration} if calibration is not None else {}))
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
        self._latest_calibration: dict | None = None
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
                self._latest_calibration = getattr(result, "calibration", None)
                visible_ids = {int(track.get("track_id", 0)) for track in self._latest_tracks
                               if not track.get("finished") and not track.get("missed", 0)}
                self._track_trails = defaultdict(list, {
                    track_id: points for track_id, points in self._track_trails.items()
                    if track_id in visible_ids})
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
                calibration = self._latest_calibration
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
                    **({"calibration": calibration} if calibration is not None else {}),
                )
                self.hub.publish(jpeg, dimensions=(self._encode_mask.shape[1],
                                 self._encode_mask.shape[0] + (85 if calibration is not None else 0)))
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
                 host: str = "0.0.0.0", port: int = 8080,
                 state_provider=None, control_callback=None):
        if not isinstance(host, str) or not host:
            raise ValueError("host must be non-empty text")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("port must be an integer in 0..65535")
        self.hub = hub or MjpegFrameHub()
        self.host = host
        self.requested_port = port
        self.state_provider = state_provider
        self.control_callback = control_callback
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
        state_provider = self.state_provider
        control_callback = self.control_callback
        page = Path(__file__).with_name("dashboard.html").read_bytes()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _json(self, code: int, payload: Any) -> None:
                body = json.dumps(payload, allow_nan=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                self.close_connection = True
                if self.path != "/api/calibration" or control_callback is None:
                    self._json(403, {"error": "calibration controls disabled"})
                    return
                if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                    self._json(403, {"error": "local API only: use an SSH tunnel"})
                    return
                origin = self.headers.get("Origin")
                expected_origin = "http://" + self.headers.get("Host", "")
                host = urlsplit(expected_origin).hostname
                if host not in ("127.0.0.1", "localhost", "::1") or (origin and origin != expected_origin):
                    self._json(403, {"error": "local same-origin access required"})
                    return
                if self.headers.get_content_type() != "application/json":
                    self._json(415, {"error": "application/json required"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 512:
                        raise ValueError("JSON body must be 1..512 bytes")
                    self.connection.settimeout(2.0)
                    payload = json.loads(self.rfile.read(length))
                    self._json(200, control_callback(payload))
                except PermissionError as exc:
                    self._json(403, {"error": str(exc)})
                except (ValueError, TypeError) as exc:
                    self._json(400, {"error": str(exc)})
                except Exception as exc:
                    self._json(503, {"error": str(exc)})

            def do_GET(self) -> None:
                if self.path == "/status.json":
                    try:
                        raw = state_provider() if state_provider is not None else {}
                        if not isinstance(raw, dict):
                            raise TypeError("state provider must return a dictionary")
                        self._json(200, {
                            "schema": 1,
                            "telemetry": raw.get("dashboard"),
                            "preview": hub.snapshot(),
                            "runtime_status": raw.get("status"),
                            "camera_controls": raw.get("camera_controls"),
                            "capabilities": {
                                "start": False, "stop": False, "reset": False,
                                "calibration": (control_callback is not None
                                                and raw.get("calibration_mode") is True),
                            },
                        })
                    except Exception:
                        self._json(503, {"error": "status unavailable"})
                    return
                if self.path == "/state.json" and state_provider is not None:
                    try:
                        self._json(200, state_provider())
                    except Exception:
                        self._json(503, {"error": "state unavailable"})
                    return
                if self.path in ("/", "/index.html"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(page)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(page)
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
                self.connection.settimeout(2.0)
                try:
                    with hub.streaming_client():
                        while not hub.closed:
                            item = hub.wait_for_next(after_sequence=sequence, timeout_s=1.0)
                            if item is None:
                                # Detect a disconnected/hidden browser even when
                                # the producer is idle; release its client lease.
                                if select.select([self.connection], [], [], 0)[0]:
                                    if not self.connection.recv(1, socket.MSG_PEEK):
                                        return
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
