"""Reuse-of-validation tests for OPT3A (verified partitions only)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from scripts.rle.counter import _partition_pieces, _validated_partitions
from scripts.wo0016.split_touching import (SplitPartition, is_verified_partition,
                                           split_touching_component,
                                           split_touching_component_verified)

FIXTURES = Path(__file__).resolve().parents[1] / "rle" / "fixtures"
CROSSING_JSON = FIXTURES / "crossing-masks-20261031.json"
CROSSING_NPZ = FIXTURES / "crossing-masks-20261031.npz"
M6_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lighting_b2_m6_touch_masks.npz"

PARAMS = dict(min_child_area=80, min_hole_area=30, min_hole_distance=10,
              hole_area_cap=16, recursive_item_area=None)


def _masks():
    data = json.loads(CROSSING_JSON.read_text(encoding="utf-8"))
    arrays = np.load(CROSSING_NPZ, allow_pickle=False)
    masks = []
    for name, sequence in data["sequences"].items():
        for item in sequence:
            masks.append((f"{name}-{item['frame_id']}", np.asarray(arrays[item["array"]]) != 0))
    for item in data["single_masks"]:
        masks.append((f"single-{item['frame_id']}-{item['array']}",
                      np.asarray(arrays[item["array"]]) != 0))
    m6 = np.load(M6_FIXTURE, allow_pickle=False)
    masks.extend((f"m6-{fid}", np.asarray(mask) != 0)
                 for fid, mask in zip(m6["frame_ids"], m6["masks"]))
    return masks


ELIGIBLE = [entry for entry in _masks() if int(entry[1].sum()) >= 2 * PARAMS["min_child_area"]]


def _connected(mask):
    import cv2
    count, _ = cv2.connectedComponents(np.ascontiguousarray(mask, dtype=np.uint8),
                                       connectivity=8, ltype=cv2.CV_32S)
    return count == 2


@pytest.mark.parametrize("name,mask", ELIGIBLE)
def test_verified_matches_legacy_api(name, mask):
    partition = split_touching_component_verified(mask, assume_connected=True, **PARAMS)
    legacy = split_touching_component(mask, assume_connected=True, **PARAMS)
    assert len(partition.pieces) == len(legacy)
    for left, right in zip(partition.pieces, legacy):
        assert np.array_equal(left, right)
    assert partition.verified is (len(partition.pieces) > 1)
    assert partition.shape == mask.shape
    assert partition.area == int(mask.sum())
    assert is_verified_partition(partition) is partition.verified


@pytest.mark.parametrize("name,mask", ELIGIBLE)
def test_verified_pieces_are_connected_and_cover_parent(name, mask):
    partition = split_touching_component_verified(mask, assume_connected=True, **PARAMS)
    if not partition.verified:
        pytest.skip("no multi-piece partition")
    pieces = partition.pieces
    assert 2 <= len(pieces) <= 3
    assert all(piece.shape == mask.shape and _connected(piece) for piece in pieces)
    coverage = np.sum(np.stack(pieces), axis=0)
    assert np.array_equal(coverage > 0, mask)
    assert not np.any(coverage > 1)


def test_fabricated_partition_is_not_verified():
    mask = ELIGIBLE[0][1]
    fabricated = SplitPartition((mask, mask), mask.shape, int(mask.sum()), True)
    assert not is_verified_partition(fabricated)
    assert not is_verified_partition(object())


def _split_fixture():
    for _, mask in ELIGIBLE:
        partition = split_touching_component_verified(mask, assume_connected=True, **PARAMS)
        if len(partition.pieces) > 1:
            return mask, partition
    pytest.skip("no multi-piece fixture")


def test_verified_partition_is_trusted():
    mask, partition = _split_fixture()
    pieces = _partition_pieces(mask, int(mask.sum()), partition)
    assert pieces is not None and len(pieces) == len(partition.pieces)


def test_fabricated_partition_falls_back_to_validation():
    mask, partition = _split_fixture()
    fabricated = SplitPartition(partition.pieces, mask.shape, int(mask.sum()), True)
    # no token -> untrusted -> full validation, which passes for valid pieces
    assert _partition_pieces(mask, int(mask.sum()), fabricated) is not None


def _valid_pair(mask):
    half = mask.copy()
    ys, xs = np.nonzero(mask)
    cut = int(np.median(xs))
    left = np.zeros_like(mask)
    right = np.zeros_like(mask)
    left[:, :cut] = mask[:, :cut]
    right[:, cut:] = mask[:, cut:]
    return left, right


def _rect():
    mask = np.zeros((10, 12), dtype=bool)
    mask[3:7, 2:10] = True
    return mask


def test_validated_partitions_accepts_valid_pair():
    mask = _rect()
    assert _validated_partitions(mask, _valid_pair(mask)) is not None


def test_validated_partitions_rejects_disconnected_piece():
    mask = np.zeros((10, 12), dtype=bool)
    mask[1:3, 1:3] = True
    mask[1:3, 8:10] = True
    broken = mask.copy()
    broken[2, 2] = False
    assert _validated_partitions(mask, (broken,)) is None


def test_validated_partitions_rejects_overlap():
    mask = _rect()
    left, right = _valid_pair(mask)
    overlap = right.copy()
    overlap[:, 1:3] |= left[:, 1:3]
    assert _validated_partitions(mask, (left, overlap)) is None


def test_validated_partitions_rejects_missing_pixels():
    mask = _rect()
    left, right = _valid_pair(mask)
    missing = right.copy()
    ys, xs = np.nonzero(missing)
    missing[ys[0], xs[0]] = False
    assert _validated_partitions(mask, (left, missing)) is None


def test_validated_partitions_rejects_shape_mismatch():
    mask = _rect()
    left, right = _valid_pair(mask)
    assert _validated_partitions(mask, (left, right[:, :-1])) is None


def test_partition_pieces_rejects_mutated_fabricated_partition():
    mask = _rect()
    left, right = _valid_pair(mask)
    mutated = right.copy()
    ys, xs = np.nonzero(mutated)
    mutated[ys[0], xs[0]] = False
    fabricated = SplitPartition((left, mutated), mask.shape, int(mask.sum()), True)
    assert _partition_pieces(mask, int(mask.sum()), fabricated) is None