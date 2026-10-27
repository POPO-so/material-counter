"""Offline tests for the Camera-to-PL compact Gray8 packet contract."""
from __future__ import annotations

import struct
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board_preflight as preflight
from camera_adapter import gray320x240_to_gray_packet


def test_synthetic_gray_frame_is_packetized_without_channel_expansion():
    gray = np.full((240, 320), 32, dtype=np.uint8)
    gray[80:120, 130:190] = 195
    assert float(gray[:80].mean()) == 32.0
    assert float(gray[80:120, 130:190].mean()) == 195.0

    packet = gray320x240_to_gray_packet(gray, frame_id=17)

    assert len(packet) == preflight.TX_BYTES == 76832
    assert struct.unpack_from('<IIII', packet) == (preflight.PACKET_MAGIC,
        (240 << 16) | 320, 320 * 240, 17)
    payload = np.frombuffer(packet[16:16 + preflight.PAYLOAD_BYTES], dtype=np.uint8)
    assert np.array_equal(payload.reshape(240, 320), gray)

    actual_golden = preflight._expected_binary(packet)
    expected_golden = np.where(gray >= 128, 0xFF, 0x00).astype(np.uint8).tobytes()
    assert len(actual_golden) == 76800
    assert actual_golden == expected_golden
    assert set(actual_golden) == {0x00, 0xFF}
    assert struct.unpack_from('<IIII', packet, 16 + preflight.PAYLOAD_BYTES) == (
        preflight.TRAILER_MAGIC, 17, int(gray.sum(dtype=np.uint64)), preflight.TRAILER_END)


@pytest.mark.parametrize('bad_frame', [
    np.zeros((240, 320), dtype=np.uint16),
    np.zeros((240, 319), dtype=np.uint8),
    np.zeros((240, 320, 1), dtype=np.uint8),
])
def test_gray_adapter_rejects_non_gray8_or_wrong_geometry(bad_frame):
    with pytest.raises(ValueError):
        gray320x240_to_gray_packet(bad_frame)


def test_gray_adapter_rejects_out_of_range_frame_id():
    with pytest.raises(ValueError):
        gray320x240_to_gray_packet(np.zeros((240, 320), dtype=np.uint8), frame_id=-1)
