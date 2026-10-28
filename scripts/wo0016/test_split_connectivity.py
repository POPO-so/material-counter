from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import split_touching


def _reference_old(mask):
    pixels = np.asarray(mask)
    if pixels.ndim != 2 or not np.any(pixels):
        return False
    count, _, _, _ = cv2.connectedComponentsWithStats(
        (pixels != 0).astype(np.uint8), connectivity=8
    )
    return count == 2


def test_connectivity_helper_does_not_compute_unused_stats_or_centroids(monkeypatch):
    def unexpected_stats(*args, **kwargs):
        raise AssertionError("WithStats computes data unused by the connectivity check")

    monkeypatch.setattr(split_touching.cv2, "connectedComponentsWithStats", unexpected_stats)
    mask = np.zeros((12, 16), dtype=np.uint8)
    mask[1, 1] = mask[2, 2] = 1
    assert split_touching.is_single_connected_component(mask)


def test_connectivity_helper_preserves_empty_1d_and_8_connected_semantics():
    assert not split_touching.is_single_connected_component(np.zeros((0, 8), np.uint8))
    assert not split_touching.is_single_connected_component(np.zeros((8, 0), np.uint8))
    assert not split_touching.is_single_connected_component(np.zeros((8, 8), np.uint8))
    assert not split_touching.is_single_connected_component(np.zeros(8, np.uint8))

    diagonal = np.eye(8, dtype=np.uint8)
    separate = np.zeros((8, 8), dtype=np.uint8)
    separate[0, 0] = 1
    separate[2, 2] = 1
    assert split_touching.is_single_connected_component(diagonal)
    assert not split_touching.is_single_connected_component(separate)


def test_connectivity_helper_accepts_float_readonly_and_noncontiguous_masks():
    storage = np.zeros((20, 24), dtype=np.float32)
    view = storage[::2, ::3]
    view[1, 1] = -2.5
    view[2, 2] = 1.25
    view.flags.writeable = False
    assert not view.flags.c_contiguous
    assert split_touching.is_single_connected_component(view)


def test_connectivity_helper_matches_old_expression_for_seeded_random_masks():
    rng = np.random.default_rng(20261028)
    for shape in ((1, 1), (2, 7), (9, 13), (24, 16)):
        for probability in (0.0, 0.05, 0.3, 0.8, 1.0):
            mask = (rng.random(shape) < probability).astype(np.uint8)
            assert split_touching.is_single_connected_component(mask) == _reference_old(mask)

    hollow = np.ones((18, 20), np.uint8)
    hollow[5:13, 5:15] = 0
    assert split_touching.is_single_connected_component(hollow) == _reference_old(hollow)
