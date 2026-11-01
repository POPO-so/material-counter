import types

import numpy as np
import pytest

from scripts.standalone.integrated_run import rasterize_into


HEIGHT, WIDTH = 400, 640


def _runs(count, seed=7):
    rng = np.random.default_rng(seed)
    rows = rng.integers(0, HEIGHT, count)
    starts = rng.integers(0, WIDTH - 1, count)
    widths = rng.integers(1, 10, count)
    ends = np.minimum(WIDTH - 1, starts + widths - 1)
    return ((rows << 20) | (starts << 10) | ends).astype(np.uint32)


def _packet(runs, *, valid=True, height=HEIGHT, width=WIDTH):
    return types.SimpleNamespace(valid=valid, height=height, width=width, runs=runs)


def _reference(packet, output):
    """Original per-run implementation kept verbatim for equivalence checks."""
    output.fill(0)
    for word in packet.runs:
        value = int(word)
        output[value >> 20, (value >> 10) & 1023:(value & 1023) + 1] = 255
    return output


@pytest.mark.parametrize("count", [0, 1, 16, 63, 64, 65, 500, 2000, 16000])
def test_bounded_rasterize_matches_original_output(count):
    packet = _packet(_runs(count))
    expected = _reference(packet, np.zeros((HEIGHT, WIDTH), dtype=np.uint8))
    actual = rasterize_into(packet, np.zeros((HEIGHT, WIDTH), dtype=np.uint8))
    assert np.array_equal(actual, expected)
    assert actual.dtype == np.uint8


def test_bounded_rasterize_does_not_mutate_runs():
    packet = _packet(_runs(2000))
    before = packet.runs.copy()
    rasterize_into(packet, np.zeros((HEIGHT, WIDTH), dtype=np.uint8))
    assert np.array_equal(packet.runs, before)


def test_bounded_rasterize_accepts_a_plain_list():
    words = [int(word) for word in _runs(100)]
    packet = _packet(words)
    expected = _reference(packet, np.zeros((HEIGHT, WIDTH), dtype=np.uint8))
    actual = rasterize_into(packet, np.zeros((HEIGHT, WIDTH), dtype=np.uint8))
    assert np.array_equal(actual, expected)


def test_invalid_packet_is_rejected():
    with pytest.raises(ValueError, match="valid RLE"):
        rasterize_into(_packet(_runs(1), valid=False),
                       np.zeros((HEIGHT, WIDTH), dtype=np.uint8))


def test_geometry_mismatch_is_rejected():
    with pytest.raises(ValueError, match="geometry"):
        rasterize_into(_packet(_runs(1)), np.zeros((HEIGHT, WIDTH + 1), dtype=np.uint8))