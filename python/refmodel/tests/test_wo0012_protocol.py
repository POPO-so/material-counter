"""Proposed wire-format and quantization contracts, not production interfaces."""
import sys
from pathlib import Path
import struct
import inspect
import pytest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from refmodel.ps_reference.regions import Region
from refmodel.ps_reference.tracking import Tracker


def api():
    try:
        from refmodel.experiments import wo0012_protocol
    except ImportError:
        pytest.fail('WO-0012 proposed packet model is not implemented')
    return wo0012_protocol


def region():
    return Region(4, 11, 3, 16, 112, 7.5, 9.5)


def test_nearest_half_up_and_floor_have_explicit_ties():
    a = api()
    assert a.quantize(7.5) == 8
    assert a.quantize(7.49) == 7
    assert a.quantize(7.5, 'floor') == 7
    with pytest.raises(ValueError):
        a.quantize(float('nan'))


def test_packet_roundtrip_and_eof_only_at_final_metadata_word():
    a = api()
    packet = a.encode_packet([region()], 41, 123456, 112)
    assert len(packet) == 32
    meta, records = a.decode_packet(packet, expected_frame_id=41)
    assert meta['seg_count'] == 1 and meta['total_fg'] == 112
    assert records[0].centroid_x == 8 and records[0].centroid_y == 10
    assert records[0].bbox == region().bbox
    beats = a.axis32_beats(packet)
    assert len(beats) == 8
    assert all(b['tkeep'] == 15 for b in beats)
    assert [b['tlast'] for b in beats] == [False] * 7 + [True]


def test_empty_frame_is_metadata_only():
    a = api()
    packet = a.encode_packet([], 0, 0, 0)
    assert len(packet) == 16
    meta, records = a.decode_packet(packet)
    assert meta['seg_count'] == 0 and records == []


def test_capacity_area_and_reserved_bits_are_rejected():
    a = api()
    with pytest.raises(ValueError):
        a.encode_packet([region()] * 65, 0, 0, 0)
    with pytest.raises(ValueError):
        a.encode_packet([Region(0, 319, 0, 239, 65536, 100, 100)], 0, 0, 65536)
    packet = bytearray(a.encode_packet([region()], 0, 0, 112))
    packet[3] |= 0x40
    with pytest.raises(ValueError, match='reserved'):
        a.decode_packet(packet)


def test_partial_wrong_count_stale_and_error_frame_never_deliver_features():
    a = api()
    packet = a.encode_packet([region()], 41, 0, 112)
    for bad in (packet[:-1], packet[:-16]):
        with pytest.raises(ValueError):
            a.decode_packet(bad)
    with pytest.raises(ValueError):
        a.decode_packet(packet, expected_frame_id=42)
    poisoned = bytearray(packet)
    poisoned[-16:] = struct.pack('<IIII', 41, 0, 112, 2)
    with pytest.raises(ValueError):
        a.decode_packet(poisoned)
    failed = a.encode_packet([], 41, 0, 112, frame_flags=8)
    with pytest.raises(ValueError, match='frame error'):
        a.decode_packet(failed)


def test_coordinate_geometry_and_total_foreground_validation():
    a = api()
    with pytest.raises(ValueError):
        a.encode_packet([Region(315, 325, 0, 2, 33, 320, 1)], 0, 0, 33)
    with pytest.raises(ValueError):
        a.encode_packet([region()], 0, 0, 1)
    with pytest.raises(ValueError):
        a.encode_packet([Region(4, 11, 3, 16, 113, 7.5, 9.5)], 0, 0, 113)


def test_nearest_rounding_has_a_remaining_strict_event_timing_boundary():
    a = api()
    exact, wire = Tracker(), Tracker()
    exact_frames, wire_frames = [], []
    for fid, cx in enumerate((154.0, 156.0, 162.2, 163.0)):
        left = int(cx) - 3
        r = Region(left, left+7, 47, 54, 20, cx, 50.0)
        _, decoded = a.decode_packet(a.encode_packet([r], fid, 0, 20))
        exact_frames.extend(e.frame_id for e in exact.update(fid, [r]))
        wire_frames.extend(e.frame_id for e in wire.update(fid, decoded))
    assert exact_frames == [2] and wire_frames == [3]
    assert exact.count == wire.count == 1


def test_configured_packet_capacity_is_not_assumed_from_the_byte_count_field():
    a = api()
    records = [region()] * 33
    with pytest.raises(ValueError):
        a.encode_packet(records, 0, 0, 3696)
    packet = a.encode_packet(records, 0, 0, 3696, record_capacity=64)
    with pytest.raises(ValueError):
        a.decode_packet(packet)
    assert len(a.decode_packet(packet, record_capacity=64)[1]) == 33


def test_binary_debug_packet_is_separate_from_feature_packet_and_checks_trailer():
    a = api()
    assert hasattr(a, 'encode_binary_packet'), 'Binary8 draft codec is not implemented'
    image = np.zeros((240, 320), np.uint8)
    image[11:24, 20:40] = 255
    packet = a.encode_binary_packet(image, 7, 100)
    assert len(packet) == 76816
    meta, decoded = a.decode_binary_packet(packet, expected_frame_id=7)
    assert meta['total_fg'] == 260 and np.array_equal(image, decoded)
    with pytest.raises(ValueError):
        a.decode_packet(packet)
    with pytest.raises(ValueError):
        a.decode_binary_packet(packet[:-1])
    damaged = bytearray(packet)
    damaged[0] = 1
    with pytest.raises(ValueError):
        a.decode_binary_packet(damaged)


def test_proposed_epoch_rejects_stale_packet_even_when_frame_id_is_reused():
    a = api()
    assert 'epoch' in inspect.signature(a.encode_packet).parameters, 'epoch draft is not implemented'
    packet = a.encode_packet([region()], 0, 0, 112, epoch=7)
    with pytest.raises(ValueError, match='epoch'):
        a.decode_packet(packet, expected_frame_id=0, expected_epoch=8)
    meta, _ = a.decode_packet(packet, expected_frame_id=0, expected_epoch=7)
    assert meta['epoch'] == 7
