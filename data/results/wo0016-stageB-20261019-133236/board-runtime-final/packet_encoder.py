"""Vectorized WO-0016 packet writer; the frozen WO-0015 dependency stays intact."""
from __future__ import annotations

import operator
import struct
from typing import Any

import numpy as np

try:
    from prepare_vectors import HEIGHT, PIXELS, WIDTH, MAGIC, TRAILER_MAGIC, TRAILER_END
except ImportError:  # pragma: no cover - package-style import support
    from ..wo0015.prepare_vectors import (
        HEIGHT, PIXELS, WIDTH, MAGIC, TRAILER_MAGIC, TRAILER_END,
    )

TX_BYTES = 16 + PIXELS * 3 + 16


def _frame_id_value(frame_id: int) -> int:
    if isinstance(frame_id, bool):
        raise ValueError("frame_id must be an unsigned 32-bit integer")
    try:
        value = operator.index(frame_id)
    except TypeError as exc:
        raise ValueError("frame_id must be an unsigned 32-bit integer") from exc
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("frame_id must be an unsigned 32-bit integer")
    return value


def gray320x240_to_rgb_packet_into(gray: np.ndarray, output: Any, *,
                                   frame_id: int = 0) -> None:
    """Fill a caller-owned 230432-byte TX buffer using native NumPy operations.

    The packet's wire representation consists of TDATA bytes only. Its header
    and trailer are four little-endian words each; payload triplets are R=G=B.
    """
    image = np.asarray(gray)
    if image.dtype != np.uint8 or image.shape != (HEIGHT, WIDTH):
        raise ValueError(f"expected a uint8 Gray8 matrix with shape {(HEIGHT, WIDTH)}")
    frame_id = _frame_id_value(frame_id)

    packet = np.asarray(output)
    if (packet.dtype != np.uint8 or packet.nbytes != TX_BYTES
            or not packet.flags.c_contiguous or not packet.flags.writeable):
        raise ValueError(f"output must be a writable contiguous uint8 buffer of {TX_BYTES} bytes")
    packet = packet.reshape(-1)

    payload_start = 16
    payload_end = payload_start + PIXELS * 3
    payload = packet[payload_start:payload_end].reshape(PIXELS, 3)
    payload[:] = image.reshape(PIXELS, 1)
    checksum = (3 * int(np.sum(image, dtype=np.uint64))) & 0xFFFFFFFF
    struct.pack_into("<IIII", packet, 0, MAGIC, (HEIGHT << 16) | WIDTH,
                     PIXELS * 3, frame_id)
    struct.pack_into("<IIII", packet, payload_end, TRAILER_MAGIC, frame_id,
                     checksum, TRAILER_END)
