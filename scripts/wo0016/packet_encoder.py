"""Vectorized WO-0016 writer for the compact native Gray8 MM2S packet."""
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

PAYLOAD_BYTES = PIXELS
TX_BYTES = 16 + PAYLOAD_BYTES + 16
PACKET_MAGIC = MAGIC


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


def gray320x240_to_gray_packet_into(gray: np.ndarray, output: Any, *,
                                    frame_id: int = 0) -> Any:
    """Write one Gray8 frame into a caller-owned 76832-byte TX buffer.

    The packet's wire representation consists of TDATA bytes only. Its header
    and trailer are four little-endian words each. Payload bytes are copied
    unchanged in row-major order, with no channel replication or RGB scratch.
    """
    image = np.asarray(gray)
    if (image.dtype != np.uint8 or image.shape != (HEIGHT, WIDTH)
            or not image.flags.c_contiguous):
        raise ValueError(
            f"expected a C-contiguous uint8 Gray8 matrix with shape {(HEIGHT, WIDTH)}"
        )
    frame_id = _frame_id_value(frame_id)

    packet = np.asarray(output)
    if (packet.dtype != np.uint8 or packet.nbytes != TX_BYTES
            or not packet.flags.c_contiguous or not packet.flags.writeable):
        raise ValueError(f"output must be a writable contiguous uint8 buffer of {TX_BYTES} bytes")
    packet = packet.reshape(-1)

    payload_start = 16
    payload_end = payload_start + PAYLOAD_BYTES
    packet[payload_start:payload_end] = image.reshape(-1)
    checksum = int(np.sum(image, dtype=np.uint64)) & 0xFFFFFFFF
    struct.pack_into("<IIII", packet, 0, MAGIC, (HEIGHT << 16) | WIDTH,
                     PAYLOAD_BYTES, frame_id)
    struct.pack_into("<IIII", packet, payload_end, TRAILER_MAGIC, frame_id,
                     checksum, TRAILER_END)
    return output
