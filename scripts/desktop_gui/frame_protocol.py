"""Versioned, bounded transport for one desktop gray/mask frame pair.

The legacy v1 format carries two raw 320x240 arrays. Full-resolution sessions
use v3: a JPEG gray preview and a lossless one-bit mask, with dynamic dimensions.
Raw dynamic v2 remains readable. Counting uses the uncompressed camera data.
"""

from __future__ import annotations

import json
import io
import struct
import threading
import time
from contextlib import contextmanager

import numpy as np


WIDTH = 320
HEIGHT = 240
PIXELS = WIDTH * HEIGHT
MAGIC = b"MCF1"
LEGACY_VERSION = 1
RAW_DYNAMIC_VERSION = 2
COMPRESSED_VERSION = 3
FLAG_JPEG_GRAY = 1
FLAG_PACKED_MASK = 2
MAX_METADATA_SIZE = 64 * 1024
MAX_MESSAGE_SIZE = 1024 * 1024
MAX_PIXELS = 640 * 400
_LEGACY_HEADER = struct.Struct("!4sHHIII")
_DYNAMIC_HEADER = struct.Struct("!4sHHHHIII")
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


def _image_shape(image, name):
    if not isinstance(image, np.ndarray):
        raise FrameProtocolError(f"{name} must be a numpy ndarray")
    if image.ndim != 2 or image.dtype != np.uint8:
        raise FrameProtocolError(f"{name} must be a two-dimensional uint8 array")
    height, width = image.shape
    if not 1 <= width <= 0xFFFF or not 1 <= height <= 0xFFFF:
        raise FrameProtocolError(f"{name} dimensions must fit unsigned 16-bit fields")
    if width * height > MAX_PIXELS:
        raise FrameProtocolError(f"{name} dimensions exceed the preview pixel limit")
    return height, width


def _image_bytes(image, name):
    _image_shape(image, name)
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
    gray_image, mask_image = frame.get("gray"), frame.get("mask")
    gray_shape = _image_shape(gray_image, "gray")
    mask_shape = _image_shape(mask_image, "mask")
    if gray_shape != mask_shape:
        raise FrameProtocolError("gray and mask dimensions must match")
    height, width = gray_shape
    legacy = gray_shape == (HEIGHT, WIDTH)
    if legacy:
        gray = _image_bytes(gray_image, "gray")
        mask = _image_bytes(mask_image, "mask")
    else:
        if np.any((mask_image != 0) & (mask_image != 255)):
            raise FrameProtocolError("compressed preview requires a binary 0/255 mask")
        # Reuse the original MJPEG camera packet when available. This avoids
        # doing a second JPEG encode on the board just for the desktop viewer.
        gray = frame.get("preview_jpeg")
        if gray is not None:
            if (not isinstance(gray, bytes) or not 4 <= len(gray) <= MAX_MESSAGE_SIZE
                    or not gray.startswith(b"\xff\xd8")):
                raise FrameProtocolError("invalid camera JPEG preview")
        else:
            import cv2

            ok, encoded = cv2.imencode(
                ".jpg", np.ascontiguousarray(gray_image), [cv2.IMWRITE_JPEG_QUALITY, 85]
            )
            if not ok:
                raise FrameProtocolError("gray preview JPEG encoding failed")
            gray = np.asarray(encoded, dtype=np.uint8).tobytes()
        packed = np.packbits(
            (np.asarray(mask_image) != 0).reshape(-1), bitorder="big"
        )
        mask = packed.tobytes()
    header_size = (_LEGACY_HEADER if legacy else _DYNAMIC_HEADER).size
    message_size = header_size + len(metadata_bytes) + len(gray) + len(mask)
    if message_size > MAX_MESSAGE_SIZE:
        raise FrameProtocolError("frame exceeds the protocol size limit")
    if legacy:
        header = _LEGACY_HEADER.pack(
            MAGIC, LEGACY_VERSION, 0, len(metadata_bytes), len(gray), len(mask)
        )
    else:
        header = _DYNAMIC_HEADER.pack(
            MAGIC, COMPRESSED_VERSION, FLAG_JPEG_GRAY | FLAG_PACKED_MASK,
            width, height,
            len(metadata_bytes), len(gray), len(mask),
        )
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
    prefix = _read_exact(stream, 6)
    magic, version = struct.unpack("!4sH", prefix)
    if magic != MAGIC or version not in (
            LEGACY_VERSION, RAW_DYNAMIC_VERSION, COMPRESSED_VERSION):
        raise FrameProtocolError("unsupported frame magic or version")
    legacy = version == LEGACY_VERSION
    header_struct = _LEGACY_HEADER if legacy else _DYNAMIC_HEADER
    header_bytes = prefix + _read_exact(stream, header_struct.size - len(prefix))
    try:
        fields = header_struct.unpack(header_bytes)
    except struct.error as exc:  # defensive; _read_exact normally guarantees full header
        raise FrameProtocolError("invalid frame header") from exc
    if legacy:
        _magic, _version, flags, metadata_size, gray_size, mask_size = fields
        width, height = WIDTH, HEIGHT
    else:
        _magic, _version, flags, width, height, metadata_size, gray_size, mask_size = fields
    if legacy and flags != 0:
        raise FrameProtocolError("unsupported frame flags")
    if version == RAW_DYNAMIC_VERSION and flags != 0:
        raise FrameProtocolError("unsupported raw dynamic frame flags")
    if version == COMPRESSED_VERSION and flags != (
            FLAG_JPEG_GRAY | FLAG_PACKED_MASK):
        raise FrameProtocolError("unsupported compressed frame flags")
    if not 1 <= width <= 0xFFFF or not 1 <= height <= 0xFFFF:
        raise FrameProtocolError("invalid frame dimensions")
    if width * height > MAX_PIXELS:
        raise FrameProtocolError("frame dimensions exceed the preview pixel limit")
    if metadata_size == 0 or metadata_size > MAX_METADATA_SIZE:
        raise FrameProtocolError("invalid metadata length")
    pixels = width * height
    compressed = version == COMPRESSED_VERSION
    if compressed:
        if not 0 < gray_size <= MAX_MESSAGE_SIZE:
            raise FrameProtocolError("invalid compressed gray length")
        if mask_size != (pixels + 7) // 8:
            raise FrameProtocolError("packed mask length must match frame dimensions")
    elif gray_size != pixels or mask_size != pixels:
        raise FrameProtocolError("gray and mask lengths must match the frame dimensions")
    message_size = header_struct.size + metadata_size + gray_size + mask_size
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
    if compressed:
        from PIL import Image

        try:
            image = Image.open(io.BytesIO(gray_bytes))
            if image.format != "JPEG" or image.size != (width, height):
                raise FrameProtocolError("compressed gray dimensions or format differ from the header")
            image = image.convert("L")
        except (OSError, ValueError) as exc:
            raise FrameProtocolError("compressed gray preview is not a valid image") from exc
        metadata["gray"] = np.asarray(image, dtype=np.uint8).copy()
        bits = np.unpackbits(
            np.frombuffer(mask_bytes, dtype=np.uint8), bitorder="big"
        )[:pixels].reshape(height, width)
        metadata["mask"] = (bits * 255).astype(np.uint8)
    else:
        metadata["gray"] = np.frombuffer(gray_bytes, dtype=np.uint8).reshape(height, width).copy()
        metadata["mask"] = np.frombuffer(mask_bytes, dtype=np.uint8).reshape(height, width).copy()
    return metadata


class LatestFrameHub:
    """Thread-safe single-slot broadcaster; slow readers observe the newest frame."""

    def __init__(self):
        self._condition = threading.Condition()
        self._seq = 0
        self._encoded = None
        self._metadata_size = None
        self._client_count = 0
        self._closed = False

    @property
    def client_count(self):
        with self._condition:
            return self._client_count

    @property
    def latest_metadata_size(self):
        """Byte length of the most recently published JSON metadata block."""
        with self._condition:
            return self._metadata_size

    def publish(self, frame):
        """Serialize immediately, then replace the only retained frame snapshot."""
        encoded = pack_frame(frame)
        version = struct.unpack_from("!H", encoded, 4)[0]
        metadata_offset = 8 if version == LEGACY_VERSION else 12
        metadata_size = struct.unpack_from("!I", encoded, metadata_offset)[0]
        with self._condition:
            if self._closed:
                raise RuntimeError("frame hub is closed")
            self._seq += 1
            self._encoded = encoded
            self._metadata_size = metadata_size
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
