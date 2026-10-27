"""Regression coverage for per-run lighting TX scratch encoders."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "gray8"))
sys.path.insert(0, str(SCRIPT_DIR))

import lighting_profile as profile
import packet_encoder
from packet_encoder import gray320x240_to_gray_packet


def _reference_gray(gray: np.ndarray, threshold: int) -> np.ndarray:
    offset = 128 - (threshold + 1)
    prepared = np.asarray(gray).copy()
    prepared[:profile.OUTPUT_ROI[0]].fill(0)
    prepared[profile.OUTPUT_ROI[1]:].fill(0)
    return np.clip(prepared.astype(np.int16) + offset, 0, 255).astype(np.uint8)


def _reference_packet(gray: np.ndarray, threshold: int, frame_id: int) -> bytes:
    return gray320x240_to_gray_packet(_reference_gray(gray, threshold), frame_id=frame_id)


@pytest.mark.parametrize("frame_id", [0, 1, 0x12345678, 0xFFFFFFFF])
def test_factory_packet_matches_gray8_reference_including_header_trailer_and_checksum(frame_id):
    gray = np.random.default_rng(frame_id).integers(0, 256, (240, 320), dtype=np.uint8)
    original = gray.copy()
    encode, _encode_into = profile.make_packet_encoders(threshold=100)

    packet = encode(gray, frame_id=frame_id)

    assert packet.dtype == np.uint8
    assert packet.nbytes == 76832
    assert packet.tobytes() == _reference_packet(gray, 100, frame_id)
    np.testing.assert_array_equal(packet[16:-16], _reference_gray(gray, 100).reshape(-1))
    assert np.array_equal(gray, original)


def test_every_threshold_and_intensity_matches_reference_packet_bytes():
    values = np.arange(256, dtype=np.uint8)
    gray = np.resize(values, (240, 320))
    original = gray.copy()
    frame_ids = (0, 1, 0x12345678, 0xFFFFFFFF)

    for threshold in range(256):
        frame_id = frame_ids[threshold % len(frame_ids)]
        expected_gray = _reference_gray(gray, threshold)
        expected = np.empty(packet_encoder.TX_BYTES, dtype=np.uint8)
        packet_encoder.gray320x240_to_gray_packet_into(
            expected_gray, expected, frame_id=frame_id
        )
        encode, encode_into = profile.make_packet_encoders(threshold=threshold)

        actual = encode(gray, frame_id=frame_id)
        assert actual.tobytes() == expected.tobytes(), f"threshold={threshold}"

        into = np.empty(packet_encoder.TX_BYTES, dtype=np.uint8)
        assert encode_into(gray, into, frame_id=frame_id) is None
        assert into.tobytes() == expected.tobytes(), f"threshold={threshold} encode_into"

    assert np.array_equal(gray, original)


def test_noncontiguous_gray_is_read_only_and_matches_contiguous_reference():
    backing = np.random.default_rng(20261026).integers(
        0, 256, (240, 640), dtype=np.uint8
    )
    gray = backing[:, ::2]
    original = gray.copy()
    encode, _encode_into = profile.make_packet_encoders(threshold=254)

    packet = encode(gray, frame_id=0xABCDEF01)

    assert not gray.flags.c_contiguous
    assert packet.tobytes() == _reference_packet(gray, 254, 0xABCDEF01)
    assert np.array_equal(gray, original)


def test_factory_rejects_gray_output_alias_before_mutating_either_buffer():
    backing = np.arange(packet_encoder.TX_BYTES, dtype=np.uint8)
    gray = backing[:240 * 320].reshape(240, 320)
    original = backing.copy()
    _encode, encode_into = profile.make_packet_encoders()

    with pytest.raises(ValueError, match="must not overlap"):
        encode_into(gray, backing, frame_id=4)

    assert np.array_equal(backing, original)


def test_factory_instances_own_independent_scratch_and_packets():
    real_lut = profile.cv2.LUT
    destinations = []

    def capture_lut(source, lut, *, dst=None):
        destinations.append(dst)
        return real_lut(source, lut, dst=dst)

    profile.cv2.LUT = capture_lut
    try:
        encode_a, into_a = profile.make_packet_encoders(threshold=100)
        encode_b, into_b = profile.make_packet_encoders(threshold=100)
        gray_a = np.full((240, 320), 101, dtype=np.uint8)
        gray_b = np.full((240, 320), 200, dtype=np.uint8)
        source_a, source_b = gray_a.copy(), gray_b.copy()
        slot_a = np.empty(packet_encoder.TX_BYTES, dtype=np.uint8)
        slot_b = np.empty(packet_encoder.TX_BYTES, dtype=np.uint8)

        into_a(gray_a, slot_a, frame_id=10)
        first_scratch = destinations[0].copy()
        into_b(gray_b, slot_b, frame_id=20)
        packet_a_after_b = slot_a.copy()
        packet_b = encode_b(gray_b, frame_id=21)
        packet_a = encode_a(gray_a, frame_id=11)
    finally:
        profile.cv2.LUT = real_lut

    assert len(destinations) == 4
    assert destinations[0] is not destinations[1]
    assert not np.shares_memory(destinations[0], destinations[1])
    assert np.array_equal(destinations[0], first_scratch)
    assert np.array_equal(slot_a, packet_a_after_b)
    assert packet_a.tobytes() == _reference_packet(gray_a, 100, 11)
    assert packet_b.tobytes() == _reference_packet(gray_b, 100, 21)
    assert np.array_equal(gray_a, source_a)
    assert np.array_equal(gray_b, source_b)
