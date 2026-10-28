"""Versioned, bounded transport for one desktop gray/mask frame pair.

Wire layout is a 20-byte network-order header (magic, version, flags, metadata,
Gray8, Binary8 lengths), followed by UTF-8 JSON and the two tightly packed
320x240 images. Reading consumes exactly one length-described message.
"""

from __future__ import annotations

import json
import struct
import threading
import time
from contextlib import contextmanager

import numpy as np


WIDTH = 320
HEIGHT = 240
PIXELS = WIDTH * HEIGHT
MAGIC = b"MCF1"
VERSION = 1
MAX_METADATA_SIZE = 64 * 1024
MAX_MESSAGE_SIZE = 256 * 1024
_HEADER = struct.Struct("!4sHHIII")
_REQUIRED_METADATA = ("frame_id", "session_id", "source", "result", "config")
_OPTIONAL_METADATA = ("capture_time_ns",)


class FrameProtocolError(ValueError):
    """The input frame or encoded message violates the desktop frame protocol."""


def _validate_metadata(metadata):
    if not isinstance(metadata, dict):
        raise FrameProtocolError("frame metadata must be a JSON object")
    missing = [key for key in _REQUIRED_METADATA if key not in metadata]
    if missing:
        raise FrameProtocolError(f"missing metadata fields: {', '.join(missing)}")
    frame_id = metadata["frame_id"]
    if isinstance(frame_id, bool) or not isinstance(frame_id, int) or not 0 <= frame_id < 2**64:
        raise FrameProtocolError("frame_id must be an unsigned 64-bit integer")
    if "capture_time_ns" in metadata:
        capture_time_ns = metadata["capture_time_ns"]
        if (
            isinstance(capture_time_ns, bool)
            or not isinstance(capture_time_ns, int)
            or not 0 <= capture_time_ns < 2**64
        ):
            raise FrameProtocolError("capture_time_ns must be an unsigned 64-bit integer")
    for key in ("session_id", "source"):
        if not isinstance(metadata[key], str) or not metadata[key]:
            raise FrameProtocolError(f"{key} must be a non-empty string")


def _image_bytes(image, name):
    if not isinstance(image, np.ndarray):
        raise FrameProtocolError(f"{name} must be a numpy ndarray")
    if image.shape != (HEIGHT, WIDTH) or image.dtype != np.uint8:
        raise FrameProtocolError(f"{name} must have shape (240, 320) and dtype uint8")
    return np.ascontiguousarray(image).tobytes()


def pack_frame(frame: dict) -> bytes:
    """Encode one frame as a bounded header, JSON metadata, Gray8, and Binary8."""
    if not isinstance(frame, dict):
        raise FrameProtocolError("frame must be a dict")
    try:
        metadata = {key: frame[key] for key in _REQUIRED_METADATA}
    except KeyError as exc:
        raise FrameProtocolError(f"missing frame field: {exc.args[0]}") from exc
    metadata.update({key: frame[key] for key in _OPTIONAL_METADATA if key in frame})
    _validate_metadata(metadata)
    try:
        metadata_bytes = json.dumps(
            metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FrameProtocolError("frame metadata must be JSON serializable") from exc
    if not metadata_bytes or len(metadata_bytes) > MAX_METADATA_SIZE:
        raise FrameProtocolError("metadata exceeds the protocol size limit")
    gray = _image_bytes(frame.get("gray"), "gray")
    mask = _image_bytes(frame.get("mask"), "mask")
    message_size = _HEADER.size + len(metadata_bytes) + len(gray) + len(mask)
    if message_size > MAX_MESSAGE_SIZE:
        raise FrameProtocolError("frame exceeds the protocol size limit")
    header = _HEADER.pack(MAGIC, VERSION, 0, len(metadata_bytes), len(gray), len(mask))
    return header + metadata_bytes + gray + mask


def _read_exact(stream, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise FrameProtocolError("stream.read() must return bytes")
        if not chunk:
            raise FrameProtocolError("truncated frame")
        chunk = bytes(chunk)
        if len(chunk) > remaining:
            raise FrameProtocolError("stream returned more bytes than requested")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _reject_json_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def read_frame(stream) -> dict:
    """Read exactly one frame from a file-like stream, tolerating short reads."""
    header_bytes = _read_exact(stream, _HEADER.size)
    try:
        magic, version, flags, metadata_size, gray_size, mask_size = _HEADER.unpack(header_bytes)
    except struct.error as exc:  # defensive; _read_exact normally guarantees full header
        raise FrameProtocolError("invalid frame header") from exc
    if magic != MAGIC or version != VERSION or flags != 0:
        raise FrameProtocolError("unsupported frame magic, version, or flags")
    if metadata_size == 0 or metadata_size > MAX_METADATA_SIZE:
        raise FrameProtocolError("invalid metadata length")
    if gray_size != PIXELS or mask_size != PIXELS:
        raise FrameProtocolError("gray and mask lengths must each be 320x240 bytes")
    message_size = _HEADER.size + metadata_size + gray_size + mask_size
    if message_size > MAX_MESSAGE_SIZE:
        raise FrameProtocolError("frame exceeds the protocol size limit")

    metadata_bytes = _read_exact(stream, metadata_size)
    try:
        metadata = json.loads(
            metadata_bytes.decode("utf-8"), parse_constant=_reject_json_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise FrameProtocolError("invalid UTF-8 JSON metadata") from exc
    _validate_metadata(metadata)
    gray_bytes = _read_exact(stream, gray_size)
    mask_bytes = _read_exact(stream, mask_size)
    metadata["gray"] = np.frombuffer(gray_bytes, dtype=np.uint8).reshape(HEIGHT, WIDTH).copy()
    metadata["mask"] = np.frombuffer(mask_bytes, dtype=np.uint8).reshape(HEIGHT, WIDTH).copy()
    return metadata


class LatestFrameHub:
    """Thread-safe single-slot broadcaster; slow readers observe the newest frame."""

    def __init__(self):
        self._condition = threading.Condition()
        self._seq = 0
        self._encoded = None
        self._client_count = 0
        self._closed = False

    @property
    def client_count(self):
        with self._condition:
            return self._client_count

    def publish(self, frame):
        """Serialize immediately, then replace the only retained frame snapshot."""
        encoded = pack_frame(frame)
        with self._condition:
            if self._closed:
                raise RuntimeError("frame hub is closed")
            self._seq += 1
            self._encoded = encoded
            self._condition.notify_all()
            return self._seq

    def wait_next(self, after_seq, timeout_s):
        """Wait for a sequence newer than ``after_seq``; return None on timeout/close."""
        if isinstance(after_seq, bool) or not isinstance(after_seq, int) or after_seq < 0:
            raise ValueError("after_seq must be a non-negative integer")
        timeout = None if timeout_s is None else max(0.0, float(timeout_s))
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._seq <= after_seq and not self._closed:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._condition.wait(remaining)
            if self._seq <= after_seq:
                return None
            # bytes are immutable; callers may retain their independent reference.
            return self._seq, self._encoded

    @contextmanager
    def client(self):
        """Track one connected consumer for the duration of its context."""
        with self._condition:
            self._client_count += 1
        try:
            yield self
        finally:
            with self._condition:
                self._client_count -= 1

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
