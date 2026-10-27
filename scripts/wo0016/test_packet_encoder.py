"""Regression tests for the canonical Gray8 packet writer."""
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "gray8"))

from packet_encoder import (TX_BYTES, gray320x240_to_gray_packet,
                            gray320x240_to_gray_packet_into)


@pytest.mark.parametrize("frame_id", [0, 1, 0x12345678, 0xFFFFFFFF])
@pytest.mark.parametrize("seed", [0, 17, 20261018])
def test_packet_is_exact_gray8_payload_without_rgb_channel_replication(seed, frame_id):
    gray = np.random.default_rng(seed).integers(0, 256, (240, 320), dtype=np.uint8)
    packet = gray320x240_to_gray_packet(gray, frame_id=frame_id)

    assert TX_BYTES == 76832
    assert len(packet) == TX_BYTES
    header = struct.unpack_from("<4I", packet)
    trailer = struct.unpack_from("<4I", packet, len(packet) - 16)
    assert header == (0x47525938, (240 << 16) | 320, 320 * 240, frame_id)
    assert packet[16:-16] == gray.tobytes()
    assert trailer == (0x454E4438, frame_id,
                       int(gray.sum(dtype=np.uint64)) & 0xFFFFFFFF, 0xC0DEC018)

    output = np.empty(TX_BYTES, dtype=np.uint8)
    result = gray320x240_to_gray_packet_into(gray, output, frame_id=frame_id)
    assert result is None
    assert output.tobytes() == packet


def test_gray_packet_writer_validates_output_geometry_and_writability():
    gray = np.zeros((240, 320), dtype=np.uint8)
    with pytest.raises(ValueError, match="76832 bytes"):
        gray320x240_to_gray_packet_into(gray, np.empty(76831, dtype=np.uint8))
    with pytest.raises(TypeError, match="contiguous writable byte buffer"):
        gray320x240_to_gray_packet_into(gray, np.zeros(153664, dtype=np.uint8)[::2])
    with pytest.raises(ValueError, match="uint32 range"):
        gray320x240_to_gray_packet_into(gray, np.empty(76832, dtype=np.uint8), frame_id=-1)
