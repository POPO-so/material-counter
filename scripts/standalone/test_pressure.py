from __future__ import annotations

import numpy as np
import pytest

from scripts.rle.protocol import decode_packet, encode_mask
from scripts.rle.run_counter import synthetic_frames
from scripts.standalone.pressure import counter_delta, make_input, validate_transfers


def _snapshot(sequence, **overrides):
    counters = {
        "total_cycles": 1000,
        "active_cycles": 800,
        "input_beats": 10,
        "output_beats": 20,
        "input_stall_cycles": 3,
        "output_stall_cycles": 4,
    }
    counters.update(overrides)
    return {"sequence": sequence, "clock_hz": 50_000_000, "counters": counters}


def test_high_runs_is_valid_16000_single_pixel_runs():
    mask = make_input("high_runs", 0)
    packet = encode_mask(mask, frame_id=0)
    decoded = decode_packet(packet, expected_frame_id=0)

    assert mask.shape == (400, 640)
    assert mask.dtype == np.uint8
    assert decoded.valid
    assert decoded.total_run_count == 16_000
    assert decoded.run_count == 16_000


@pytest.mark.parametrize("index", [0, 1, 11, 12, 37])
def test_moving_input_matches_existing_synthetic_generator(index):
    assert np.array_equal(make_input("moving", index), list(synthetic_frames(index + 1))[-1])


def test_counter_delta_uses_64_bit_modular_difference_and_records_sequences():
    before = _snapshot(0xFFFFFFFF, total_cycles=(1 << 64) - 3, input_beats=(1 << 64) - 4)
    after = _snapshot(0, total_cycles=5, input_beats=6)

    result = counter_delta(before, after)

    assert result["sequence_before"] == 0xFFFFFFFF
    assert result["sequence_after"] == 0
    assert result["counters"]["total_cycles"] == 8
    assert result["counters"]["input_beats"] == 10


def test_validate_transfers_accepts_exact_beats_and_reports_backpressure():
    delta = counter_delta(_snapshot(4, input_beats=0, output_beats=0,
                                   input_stall_cycles=0, output_stall_cycles=0),
                          _snapshot(5, input_beats=2 * 64_008,
                                    output_beats=12, input_stall_cycles=30,
                                    output_stall_cycles=50))

    result = validate_transfers(delta, frames=2, rx_bytes=[20, 28])

    assert result["valid"] is True
    assert result["expected_input_beats"] == 2 * 64_008
    assert result["observed_input_beats"] == 2 * 64_008
    assert result["expected_output_beats"] == 12
    assert result["observed_output_beats"] == 12
    assert result["input_stall_cycles"] == 30
    assert result["output_stall_cycles"] == 50


def test_validate_transfers_rejects_corrupted_input_beat_count():
    delta = counter_delta(_snapshot(1, input_beats=0), _snapshot(2, input_beats=64_007))

    with pytest.raises(ValueError, match="input_beats"):
        validate_transfers(delta, frames=1, rx_bytes=[20])


def test_validate_transfers_rejects_misaligned_rx_length():
    delta = counter_delta(_snapshot(1), _snapshot(2))

    with pytest.raises(ValueError, match="4-byte aligned"):
        validate_transfers(delta, frames=1, rx_bytes=[21])


def test_validate_transfers_rejects_corrupted_output_beat_count():
    delta = counter_delta(_snapshot(1, input_beats=0, output_beats=0),
                          _snapshot(2, input_beats=64_008, output_beats=4))

    with pytest.raises(ValueError, match="output_beats"):
        validate_transfers(delta, frames=1, rx_bytes=[20])


@pytest.mark.parametrize("bad_count", [True, 1.25, -1, 1 << 64])
def test_counter_delta_rejects_invalid_counter_values(bad_count):
    with pytest.raises((TypeError, ValueError)):
        counter_delta(_snapshot(1), _snapshot(2, input_beats=bad_count))


@pytest.mark.parametrize("rx_bytes", [[20, 24], [True], [1.5], [-4]])
def test_validate_transfers_rejects_invalid_rx_lengths(rx_bytes):
    delta = counter_delta(_snapshot(1), _snapshot(2))
    with pytest.raises((TypeError, ValueError)):
        validate_transfers(delta, frames=1, rx_bytes=rx_bytes)


@pytest.mark.parametrize("frames", [True, 1.5, -1])
def test_validate_transfers_rejects_invalid_frame_count(frames):
    delta = counter_delta(_snapshot(1), _snapshot(2))
    with pytest.raises((TypeError, ValueError)):
        validate_transfers(delta, frames=frames, rx_bytes=[20])
