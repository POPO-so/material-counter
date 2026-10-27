"""Offline tests for the Camera-to-PL Gray8 packet contract."""
from __future__ import annotations

import struct
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board_preflight as preflight
from camera_adapter import gray320x240_to_gray_packet, gray320x240_to_gray_packet_into


def test_gray_camera_frame_uses_single_channel_gray8_packet_and_exact_binary8_golden():
    gray = np.full((240, 320), 32, dtype=np.uint8)
    gray[80:120, 130:190] = 195
    packet = gray320x240_to_gray_packet(gray, frame_id=17)
    assert len(packet) == preflight.TX_BYTES == 76832
    assert struct.unpack_from('<IIII', packet) == (0x47525938, (240 << 16) | 320, 76800, 17)
    payload = np.frombuffer(packet[16:16 + 76800], dtype=np.uint8)
    assert np.array_equal(payload.reshape(240, 320), gray)
    assert struct.unpack_from('<IIII', packet, 16 + 76800) == (
        0x454e4438, 17, int(gray.sum(dtype=np.uint64)), 0xc0dec018)
    assert preflight._expected_binary(packet) == np.where(gray >= 128, 0xff, 0).astype(np.uint8).tobytes()
    into = bytearray(preflight.TX_BYTES)
    gray320x240_to_gray_packet_into(gray, into, frame_id=17)
    assert into == packet


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
