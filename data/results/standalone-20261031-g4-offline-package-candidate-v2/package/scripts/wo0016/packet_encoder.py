"""WO-0016 import surface for the canonical experimental Gray8 packet codec."""
from __future__ import annotations

import sys
from pathlib import Path

_script_dir = Path(__file__).resolve().parent
for _gray8_dir in (_script_dir.parent / "gray8", _script_dir / "gray8"):
    if (_gray8_dir / "packet_codec.py").is_file() and str(_gray8_dir) not in sys.path:
        sys.path.insert(0, str(_gray8_dir))

from packet_codec import (  # noqa: E402
    TX_BYTES,
    gray320x240_to_gray_packet,
    gray320x240_to_gray_packet_into,
)

__all__ = ["TX_BYTES", "gray320x240_to_gray_packet",
           "gray320x240_to_gray_packet_into"]
