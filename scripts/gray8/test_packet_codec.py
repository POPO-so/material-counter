import numpy as np
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from packet_codec import (
    binary_golden,
    gray320x240_to_gray_packet,
    gray320x240_to_gray_packet_into,
    validate_packet,
)


def test_gray8_packet_round_trip_has_expected_little_endian_words_and_checksum():
    gray = np.arange(320 * 240, dtype=np.uint32).astype(np.uint8).reshape(240, 320)[:, ::-1]
    packet = gray320x240_to_gray_packet(gray, frame_id=0x12345678)
    assert len(packet) == 76832
    words = np.frombuffer(packet, dtype="<u4")
    assert words[:4].tolist() == [0x47525938, 0x00F00140, 76800, 0x12345678]
    assert words[-4:].tolist() == [0x454E4438, 0x12345678,
                                    int(gray.sum(dtype=np.uint64)), 0xC0DEC018]
    np.testing.assert_array_equal(validate_packet(packet, frame_id=0x12345678), gray)


def test_gray8_packet_into_checks_output_and_packet_validation_rejects_corruption():
    gray = np.zeros((240, 320), dtype=np.uint8)
    output = bytearray(76832)
    assert gray320x240_to_gray_packet_into(gray, output, frame_id=7) is None
    assert bytes(output) == gray320x240_to_gray_packet(gray, frame_id=7)
    corrupt = bytearray(output)
    corrupt[-8] ^= 1
    with pytest.raises(ValueError):
        validate_packet(corrupt)
    with pytest.raises(ValueError):
        gray320x240_to_gray_packet_into(gray, bytearray(76831))


@pytest.mark.parametrize("gray, frame_id", [
    (np.zeros((240, 320, 3), dtype=np.uint8), 0),
    (np.zeros((240, 320), dtype=np.int16), 0),
    (np.zeros((240, 320), dtype=np.uint8), -1),
    (np.zeros((240, 320), dtype=np.uint8), 2**32),
])
def test_gray8_packet_rejects_invalid_image_or_frame_id(gray, frame_id):
    with pytest.raises((TypeError, ValueError)):
        gray320x240_to_gray_packet(gray, frame_id=frame_id)


def test_binary_golden_threshold_is_independent_and_inclusive():
    gray = np.zeros((240, 320), dtype=np.uint8)
    gray[0, :3] = [127, 128, 255]
    result = binary_golden(gray)
    np.testing.assert_array_equal(result[0, :3], [0, 255, 255])
    assert not result[1:].any()

