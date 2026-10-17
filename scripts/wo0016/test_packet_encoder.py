"""Regression tests for the WO-0016 in-place vectorized packet writer."""
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "wo0015"))

from camera_adapter import gray320x240_to_rgb_packet as legacy_encoder
from packet_encoder import gray320x240_to_rgb_packet_into


@pytest.mark.parametrize("frame_id", [0, 1, 0x12345678, 0xFFFFFFFF])
@pytest.mark.parametrize("seed", [0, 17, 20261018])
def test_vectorized_packet_matches_frozen_wo0015_encoder_byte_for_byte(seed, frame_id):
    gray = np.random.default_rng(seed).integers(0, 256, (240, 320), dtype=np.uint8)
    expected = legacy_encoder(gray, frame_id=frame_id)
    output = np.empty((230432,), dtype=np.uint8)

    result = gray320x240_to_rgb_packet_into(gray, output, frame_id=frame_id)

    assert result is None
    assert output.nbytes == len(expected) == 230432
    assert output.tobytes() == expected


def test_vectorized_packet_validates_output_geometry_and_writability():
    gray = np.zeros((240, 320), dtype=np.uint8)
    with pytest.raises(ValueError, match="230432 bytes"):
        gray320x240_to_rgb_packet_into(gray, np.empty((230431,), dtype=np.uint8))
    with pytest.raises(ValueError, match="writable contiguous uint8"):
        gray320x240_to_rgb_packet_into(gray, np.zeros((230432,), dtype=np.uint8)[::2])
    with pytest.raises(ValueError, match="unsigned 32-bit"):
        gray320x240_to_rgb_packet_into(gray, np.empty((230432,), dtype=np.uint8), frame_id=-1)
