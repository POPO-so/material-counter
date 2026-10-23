"""Offline Camera-to-PL adapter for the frozen WO-0015 RGB packet contract."""
from __future__ import annotations

import operator

import numpy as np

try:
    from prepare_vectors import HEIGHT, PIXELS, WIDTH, make_packet
except ImportError:  # pragma: no cover - package-style import support
    from .prepare_vectors import HEIGHT, PIXELS, WIDTH, make_packet


def gray320x240_to_rgb_packet(gray: np.ndarray, *, frame_id: int = 0) -> bytes:
    """Copy a Gray8 320x240 image into the existing 230432-byte RGB packet.

    The adapter expands every source sample as R=G=B=Y and delegates packet
    framing to ``prepare_vectors.make_packet``. It performs no capture, resize,
    MMIO or DMA operation; V4L2 ROI/resize remains a separate PS stage.
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

    rgb = np.repeat(image[:, :, None], 3, axis=2).tobytes(order="C")
    beats = make_packet(rgb, WIDTH, HEIGHT, frame_id)
    packet = b"".join(beat.data.to_bytes(4, "little") for beat in beats)
    expected_bytes = 16 + PIXELS * 3 + 16
    if len(packet) != expected_bytes:
        raise AssertionError(f"packet packer emitted {len(packet)} bytes, expected {expected_bytes}")
    return packet
