"""Gray8 AXI-stream packet codec for the experimental 320x240 path."""

from __future__ import annotations

import struct
from typing import Any

import numpy as np


WIDTH = 320
HEIGHT = 240
PIXELS = WIDTH * HEIGHT
HEADER_BYTES = 16
TRAILER_BYTES = 16
TX_BYTES = HEADER_BYTES + PIXELS + TRAILER_BYTES
HEADER_MAGIC = 0x47525938
TRAILER_MAGIC = 0x454E4438
END_MAGIC = 0xC0DEC018
_WORD = struct.Struct("<4I")


def _gray_array(gray: Any) -> np.ndarray:
    array = np.asarray(gray)
    if array.dtype != np.uint8 or array.shape != (HEIGHT, WIDTH):
        raise ValueError(f"gray must be uint8 with shape {(HEIGHT, WIDTH)}")
    return array


def _frame_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError("frame_id must be an integer")
    value = int(value)
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("frame_id must be in uint32 range")
    return value


def gray320x240_to_gray_packet_into(gray: Any, output: Any, *, frame_id: int = 0) -> None:
    array = _gray_array(gray)
    frame_id = _frame_id(frame_id)
    try:
        packet = np.asarray(output)
    except Exception as exc:
        raise TypeError("output must expose writable contiguous uint8 storage") from exc
    if (packet.dtype != np.uint8 or packet.nbytes != TX_BYTES
            or not packet.flags.c_contiguous or not packet.flags.writeable):
        raise ValueError(
            f"output must be writable contiguous uint8 storage of {TX_BYTES} bytes")
    view = memoryview(packet.reshape(-1)).cast("B")
    _WORD.pack_into(view, 0, HEADER_MAGIC, (HEIGHT << 16) | WIDTH, PIXELS, frame_id)
    payload = memoryview(np.ascontiguousarray(array)).cast("B")
    view[HEADER_BYTES:HEADER_BYTES + PIXELS] = payload
    checksum = int(array.sum(dtype=np.uint64)) & 0xFFFFFFFF
    _WORD.pack_into(view, HEADER_BYTES + PIXELS,
                    TRAILER_MAGIC, frame_id, checksum, END_MAGIC)


def gray320x240_to_gray_packet(gray: Any, *, frame_id: int = 0) -> bytes:
    output = bytearray(TX_BYTES)
    gray320x240_to_gray_packet_into(gray, output, frame_id=frame_id)
    return bytes(output)


def validate_packet(packet: Any, frame_id: int | None = None) -> np.ndarray:
    try:
        view = memoryview(packet).cast("B")
    except (TypeError, ValueError) as exc:
        raise TypeError("packet must expose a contiguous byte buffer") from exc
    if view.nbytes != TX_BYTES:
        raise ValueError(f"packet must be exactly {TX_BYTES} bytes")
    header = _WORD.unpack_from(view, 0)
    if header[:3] != (HEADER_MAGIC, (HEIGHT << 16) | WIDTH, PIXELS):
        raise ValueError("Gray8 packet header is invalid")
    packet_id = header[3]
    if frame_id is not None and packet_id != _frame_id(frame_id):
        raise ValueError("Gray8 packet frame ID does not match")
    trailer = _WORD.unpack_from(view, HEADER_BYTES + PIXELS)
    if trailer[0] != TRAILER_MAGIC or trailer[1] != packet_id or trailer[3] != END_MAGIC:
        raise ValueError("Gray8 packet trailer is invalid")
    payload = np.frombuffer(view[HEADER_BYTES:HEADER_BYTES + PIXELS], dtype=np.uint8)
    if int(payload.sum(dtype=np.uint64)) & 0xFFFFFFFF != trailer[2]:
        raise ValueError("Gray8 packet payload checksum is invalid")
    return payload.reshape(HEIGHT, WIDTH).copy()


def binary_golden(gray: Any, threshold: int = 128) -> np.ndarray:
    array = _gray_array(gray)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, np.integer)):
        raise TypeError("threshold must be an integer")
    threshold = int(threshold)
    if not 0 <= threshold <= 255:
        raise ValueError("threshold must be in uint8 range")
    return np.where(array >= threshold, 255, 0).astype(np.uint8)
