"""Gray8 MM2S packet contract for the native single-channel protocol."""
from __future__ import annotations

import struct
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wo0015"))
import packet_encoder


def test_gray8_encoder_writes_compact_packet_into_caller_buffer():
    gray = np.arange(packet_encoder.PIXELS, dtype=np.uint32).astype(np.uint8)
    gray = gray.reshape(packet_encoder.HEIGHT, packet_encoder.WIDTH)
    output = np.full(packet_encoder.TX_BYTES, 0xA5, dtype=np.uint8)

    result = packet_encoder.gray320x240_to_gray_packet_into(
        gray, output, frame_id=0x12345678
    )

    assert result is output
    assert packet_encoder.PAYLOAD_BYTES == 76_800
    assert packet_encoder.TX_BYTES == 76_832
    assert output.nbytes == 76_832
    assert struct.unpack_from("<IIII", output, 0) == (
        packet_encoder.PACKET_MAGIC,
        (packet_encoder.HEIGHT << 16) | packet_encoder.WIDTH,
        76_800,
        0x12345678,
    )
    assert output[16:16 + 76_800].tobytes() == gray.tobytes(order="C")
    assert struct.unpack_from("<IIII", output, 16 + 76_800) == (
        packet_encoder.TRAILER_MAGIC,
        0x12345678,
        int(gray.sum(dtype=np.uint64)) & 0xFFFFFFFF,
        packet_encoder.TRAILER_END,
    )


def test_gray8_encoder_rejects_rgb_frame_and_old_rgb_sized_tx_buffer():
    gray = np.zeros((packet_encoder.HEIGHT, packet_encoder.WIDTH), dtype=np.uint8)
    output = np.empty(packet_encoder.TX_BYTES, dtype=np.uint8)

    with pytest.raises(ValueError, match="Gray8"):
        packet_encoder.gray320x240_to_gray_packet_into(
            np.zeros((packet_encoder.HEIGHT, packet_encoder.WIDTH, 3), dtype=np.uint8),
            output,
        )
    with pytest.raises(ValueError, match="76832 bytes"):
        packet_encoder.gray320x240_to_gray_packet_into(
            gray, np.empty((230_432,), dtype=np.uint8)
        )


def test_gray8_encoder_rejects_strided_input_to_avoid_hidden_copy():
    gray = np.zeros((packet_encoder.HEIGHT, packet_encoder.WIDTH * 2), dtype=np.uint8)
    strided = gray[:, ::2]
    output = np.empty(packet_encoder.TX_BYTES, dtype=np.uint8)

    assert strided.shape == (packet_encoder.HEIGHT, packet_encoder.WIDTH)
    assert not strided.flags.c_contiguous
    with pytest.raises(ValueError, match="C-contiguous"):
        packet_encoder.gray320x240_to_gray_packet_into(strided, output)
