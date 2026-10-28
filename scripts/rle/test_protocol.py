from __future__ import annotations

import struct

import numpy as np
import pytest

from scripts.rle.protocol import (
    END_MAGIC,
    HEADER_MAGIC,
    HEIGHT,
    MAX_RUNS,
    RX_CAPACITY,
    TRAILER_MAGIC,
    TX_BYTES,
    WIDTH,
    decode_packet,
    encode_gray,
    encode_gray_into,
    encode_mask,
    reconstruct,
)


WORD = struct.Struct("<I")


def _word(packet: bytes, index: int) -> int:
    return WORD.unpack_from(packet, index * 4)[0]


def _rewrite_word(packet: bytes, index: int, value: int) -> bytes:
    changed = bytearray(packet)
    WORD.pack_into(changed, index * 4, value)
    checksum_index = len(changed) // 4 - 8
    checksum_value = sum(_word(changed, i) for i in range(checksum_index)) & 0xFFFFFFFF
    WORD.pack_into(changed, checksum_index * 4, checksum_value)
    return bytes(changed)


def test_gray8_packet_encodes_640x400_using_existing_packet_shape():
    gray = np.arange(HEIGHT * WIDTH, dtype=np.uint32).reshape(HEIGHT, WIDTH).astype(np.uint8)
    packet = encode_gray(gray, frame_id=0x12345678)

    assert len(packet) == TX_BYTES == 256032
    assert _word(packet, 0) == 0x47525938
    assert _word(packet, 1) == (HEIGHT << 16) | WIDTH
    assert _word(packet, 2) == HEIGHT * WIDTH
    assert _word(packet, 3) == 0x12345678
    assert packet[16:-16] == gray.tobytes()
    checksum_index = (16 + HEIGHT * WIDTH) // 4 + 2
    assert _word(packet, checksum_index) == int(gray.sum(dtype=np.uint64)) & 0xFFFFFFFF


def test_gray8_packet_into_reuses_exact_size_writable_buffer():
    gray = np.zeros((HEIGHT, WIDTH), np.uint8)
    output = bytearray(TX_BYTES)
    encode_gray_into(gray, output, frame_id=7)
    assert encode_gray(gray, frame_id=7) == bytes(output)
    with pytest.raises(ValueError, match="exactly"):
        encode_gray_into(gray, bytearray(TX_BYTES - 1))


@pytest.mark.parametrize("gray", [
    np.zeros((240, 320), np.uint8),
    np.zeros((HEIGHT, WIDTH), np.uint16),
])
def test_gray8_packet_rejects_wrong_geometry_or_dtype(gray):
    with pytest.raises(ValueError):
        encode_gray(gray)


@pytest.mark.parametrize("frame_id", [-1, 1 << 32, True, 1.5])
def test_gray8_packet_rejects_invalid_frame_id(frame_id):
    with pytest.raises((TypeError, ValueError)):
        encode_gray(np.zeros((HEIGHT, WIDTH), np.uint8), frame_id=frame_id)


@pytest.mark.parametrize("mask", [
    np.zeros((HEIGHT, WIDTH), np.uint8),
    np.full((HEIGHT, WIDTH), 255, np.uint8),
    np.pad(np.array([[255]], np.uint8), ((12, HEIGHT - 13), (30, WIDTH - 31))),
    np.pad(np.array([[255, 0], [0, 255]], np.uint8), ((12, HEIGHT - 14), (30, WIDTH - 32))),
    np.pad(np.array([[255, 255, 255], [255, 0, 255], [255, 255, 255]], np.uint8),
           ((12, HEIGHT - 15), (30, WIDTH - 33))),
])
def test_rle_round_trip_preserves_empty_full_hole_and_diagonal_topology(mask):
    packet = encode_mask(mask, frame_id=12, params_version=3, timestamp=0x1020304050607080)
    frame = decode_packet(packet, expected_frame_id=12, expected_params_version=3)

    assert frame.valid
    assert frame.frame_id == 12
    assert frame.params_version == 3
    assert frame.hw_timestamp == 0x1020304050607080
    assert frame.total_fg == int(np.count_nonzero(mask))
    assert np.array_equal(reconstruct(frame), mask)


def test_rle_random_mask_round_trip_and_projection_sums():
    rng = np.random.default_rng(0x524C4531)
    mask = np.where(rng.random((HEIGHT, WIDTH)) < 0.007, 255, 0).astype(np.uint8)
    frame = decode_packet(encode_mask(mask))
    assert frame.valid
    assert frame.total_fg == int(np.count_nonzero(mask))
    assert int(frame.row_projection.sum()) == frame.total_fg
    assert int(frame.column_projection.sum()) == frame.total_fg
    assert np.array_equal(reconstruct(frame), mask)


def test_rle_runs_merge_horizontal_pixels_and_keep_eight_connected_diagonals():
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[10, 20:25] = 255
    mask[11, 25:27] = 255
    frame = decode_packet(encode_mask(mask))
    assert frame.run_count == 2
    assert tuple(map(int, frame.runs)) == ((10 << 20) | (20 << 10) | 24,
                                           (11 << 20) | (25 << 10) | 26)
    assert frame.row_projection[10] == 5
    assert frame.column_projection[25] == 1


def test_rle_roi_uses_absolute_coordinates_and_frame_wide_projections():
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[7, 9] = 255
    mask[101, 202:205] = 255
    frame = decode_packet(encode_mask(mask, roi=(200, 100, 220, 110)))
    assert frame.roi == (200, 100, 220, 110)
    assert frame.total_fg == 3
    assert frame.row_projection[101] == 3
    assert frame.row_projection[7] == 0
    assert np.all(reconstruct(frame)[7] == 0)
    assert reconstruct(frame)[101, 202:205].tolist() == [255, 255, 255]


def test_rle_packet_has_fixed_capacity_and_complete_header_trailer_metadata():
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[4, 7:10] = 255
    packet = encode_mask(mask, frame_id=5, params_version=6, timestamp=9,
                         threshold=100, intensity_offset=-4, morph_mode=1)
    frame = decode_packet(packet)
    assert len(packet) <= RX_CAPACITY == 67744
    assert _word(packet, 0) == HEADER_MAGIC
    assert _word(packet, 1) == 1
    assert _word(packet, 15) == 0
    trailer_index = 16 + frame.run_count + _word(packet, 10)
    assert _word(packet, trailer_index) == TRAILER_MAGIC
    assert _word(packet, len(packet) // 4 - 1) == END_MAGIC
    assert frame.threshold == 100
    assert frame.intensity_offset == -4
    assert frame.morph_mode == 1
    assert not frame.runs.flags.writeable
    assert not frame.row_projection.flags.writeable


def test_rle_overflow_is_a_complete_invalid_frame_and_cannot_be_reconstructed():
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[::2, ::2] = 255
    packet = encode_mask(mask, max_runs=10)
    frame = decode_packet(packet)
    assert not frame.valid
    assert frame.status == 1
    assert frame.total_run_count > frame.run_count == 10
    assert frame.total_fg == HEIGHT * WIDTH // 4
    assert frame.actual_bytes == len(packet)
    with pytest.raises(ValueError, match="overflow"):
        reconstruct(frame)


def test_rle_detects_tampered_header_run_projection_trailer_and_truncation():
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[1, 2:4] = 255
    packet = encode_mask(mask)
    offsets = [0, 1, 16, 16 + 1, len(packet) // 4 - 1]
    for offset in offsets:
        changed = bytearray(packet)
        WORD.pack_into(changed, offset * 4, _word(packet, offset) ^ 1)
        with pytest.raises(ValueError):
            decode_packet(changed)
    with pytest.raises(ValueError):
        decode_packet(packet[:-4])


@pytest.mark.parametrize("roi", [(-1, 0, 1, 1), (1, 1, 1, 2), (0, 0, WIDTH + 1, HEIGHT)])
def test_rle_rejects_invalid_roi(roi):
    with pytest.raises(ValueError):
        encode_mask(np.zeros((HEIGHT, WIDTH), np.uint8), roi=roi)


def test_rle_rejects_nonbinary_masks_and_limits():
    invalid = np.zeros((HEIGHT, WIDTH), np.uint8)
    invalid[0, 0] = 1
    with pytest.raises(ValueError, match="0 or 255"):
        encode_mask(invalid)
    with pytest.raises(ValueError, match="max_runs"):
        encode_mask(np.zeros((HEIGHT, WIDTH), np.uint8), max_runs=MAX_RUNS + 1)


def test_rle_rejects_nonmaximal_or_unsorted_runs_even_with_recomputed_checksum():
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[8, 10:13] = 255
    mask[8, 14:16] = 255
    packet = encode_mask(mask)
    run_word_offset = 17
    adjacent = (8 << 20) | (13 << 10) | 15
    malformed = bytearray(packet)
    WORD.pack_into(malformed, run_word_offset * 4, adjacent)
    emitted = _word(packet, len(packet) // 4 - 16 + 5)
    checksum_index = 16 + emitted + _word(packet, 10)
    checksum = sum(_word(malformed, i) for i in range(checksum_index)) & 0xFFFFFFFF
    WORD.pack_into(malformed, (checksum_index + 8) * 4, checksum)
    with pytest.raises(ValueError, match="maximal"):
        decode_packet(malformed)


@pytest.mark.parametrize("index,value,error", [
    (2, (400 << 16) | 641, "geometry"),
    (13, 256, "offset"),
    (15, 1, "reserved header"),
    (-5, 1, "reserved trailer"),
])
def test_rle_rejects_invalid_geometry_configuration_and_reserved_words(index, value, error):
    packet = encode_mask(np.zeros((HEIGHT, WIDTH), np.uint8))
    offset = index if index >= 0 else len(packet) // 4 + index
    changed = _rewrite_word(packet, offset, value)
    with pytest.raises(ValueError, match=error):
        decode_packet(changed)


def test_rle_rejects_nonzero_reserved_run_bits_with_valid_checksum():
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[4, 5:7] = 255
    packet = encode_mask(mask)
    changed = bytearray(packet)
    WORD.pack_into(changed, 16 * 4, _word(packet, 16) | 0x20000000)
    emitted = _word(packet, len(packet) // 4 - 16 + 5)
    checksum_index = 16 + emitted + _word(packet, 10)
    checksum = sum(_word(changed, i) for i in range(checksum_index)) & 0xFFFFFFFF
    WORD.pack_into(changed, (checksum_index + 8) * 4, checksum)
    with pytest.raises(ValueError, match="reserved run bits"):
        decode_packet(changed)


def test_rle_input_error_frame_can_report_partial_accepted_pixels():
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[3, 2:5] = 255
    packet = bytearray(encode_mask(mask))
    trailer = len(packet) // 4 - 16
    WORD.pack_into(packet, (trailer + 6) * 4, 2)
    WORD.pack_into(packet, (trailer + 9) * 4, 7)
    WORD.pack_into(packet, (trailer + 10) * 4, 200)
    emitted = _word(packet, len(packet) // 4 - 16 + 5)
    checksum_index = 16 + emitted + _word(packet, 10)
    checksum = sum(_word(packet, i) for i in range(checksum_index)) & 0xFFFFFFFF
    WORD.pack_into(packet, (checksum_index + 8) * 4, checksum)
    frame = decode_packet(packet)
    assert not frame.valid
    assert frame.status == 2
    assert frame.input_error_code == 7
    assert frame.accepted_pixels == 200
    with pytest.raises(ValueError, match="input-error"):
        reconstruct(frame)
