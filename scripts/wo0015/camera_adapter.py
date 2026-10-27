"""Offline Camera-to-PL adapter for the compact WO-0015 Gray8 packet."""
from __future__ import annotations

import operator
import struct

import numpy as np

try:
    from prepare_vectors import HEIGHT, PIXELS, WIDTH, MAGIC, TRAILER_MAGIC, TRAILER_END
except ImportError:  # pragma: no cover - package-style import support
    from .prepare_vectors import HEIGHT, PIXELS, WIDTH, MAGIC, TRAILER_MAGIC, TRAILER_END


def gray320x240_to_gray_packet(gray: np.ndarray, *, frame_id: int = 0) -> bytes:
    """Copy a Gray8 320x240 image into a compact 76832-byte DMA packet.

    Payload order is the source matrix's row-major byte order. This offline
    helper performs no capture, resize, MMIO or DMA operation.
    """
    image = np.asarray(gray)
    if image.dtype != np.uint8 or image.shape != (HEIGHT, WIDTH):
        raise ValueError(f"expected a uint8 Gray8 matrix with shape {(HEIGHT, WIDTH)}")
    if isinstance(frame_id, bool):
        raise ValueError("frame_id must be an unsigned 32-bit integer")
    try:
        frame_id = operator.index(frame_id)
    except TypeError as exc:
        raise ValueError("frame_id must be an unsigned 32-bit integer") from exc
    if not 0 <= frame_id <= 0xFFFFFFFF:
        raise ValueError("frame_id must be an unsigned 32-bit integer")

    gray8 = image.tobytes(order="C")
    words = struct.pack("<IIII", MAGIC, (HEIGHT << 16) | WIDTH,
                        PIXELS, frame_id)
    trailer = struct.pack("<IIII", TRAILER_MAGIC, frame_id,
                          sum(gray8) & 0xFFFFFFFF, TRAILER_END)
    packet = words + gray8 + trailer
    if len(packet) != 16 + PIXELS + 16:
        raise AssertionError(f"packet packer emitted {len(packet)} bytes, expected 76832")
    return packet
