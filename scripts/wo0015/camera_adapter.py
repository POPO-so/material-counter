"""Offline camera-to-PL Gray8 packet adapter."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from gray8.packet_codec import (  # noqa: E402
    HEIGHT,
    WIDTH,
    gray320x240_to_gray_packet as _encode_packet,
    gray320x240_to_gray_packet_into as _encode_packet_into,
)


def gray320x240_to_gray_packet(gray: Any, *, frame_id: int = 0) -> bytes:
    """Encode a uint8 320x240 grayscale image using the Gray8 DMA packet."""
    return _encode_packet(gray, frame_id=frame_id)


def gray320x240_to_gray_packet_into(gray: Any, output: Any, *, frame_id: int = 0) -> None:
    """Encode directly into a reusable Gray8 packet buffer."""
    _encode_packet_into(gray, output, frame_id=frame_id)
