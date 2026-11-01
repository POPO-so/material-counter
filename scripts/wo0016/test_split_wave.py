"""Exactness tests for the wave-based geodesic assignment (OPT2).

The reference implementation below is the previous per-seed dilation loop,
copied verbatim, so the new wave must produce the same labels (or the same
``None``) on real fixture masks and adversarial shapes.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from scripts.wo0016.split_touching import (_enclosed_holes, _geodesic_hole_assignments,
                                           _wave_assign)

FIXTURES = Path(__file__).resolve().parents[1] / "rle" / "fixtures"
CROSSING_JSON = FIXTURES / "crossing-masks-20261031.json"
CROSSING_NPZ = FIXTURES / "crossing-masks-20261031.npz"
M6_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lighting_b2_m6_touch_masks.npz"


def _legacy_wave(pixels, seeds):
    labels = np.full(pixels.shape, -1, dtype=np.int8)
    for index, seed in enumerate(seeds):
        labels[seed] = index
    kernel = np.ones((3, 3), dtype=np.uint8)
    while np.any(pixels & (labels < 0)):
        unassigned = pixels & (labels < 0)
        claims = []
        for index in range(len(seeds)):
            region = (labels == index).astype(np.uint8)
            adjacent = cv2.dilate(region, kernel) != 0
            claims.append(adjacent & unassigned)
        claim_stack = np.stack(claims, axis=0)
        claimed = np.any(claim_stack, axis=0)
        if not np.any(claimed):
            return None
        owners = np.argmax(claim_stack, axis=0)
        labels[claimed] = owners[claimed]
    return labels


def _legacy_assignments(pixels, hole_centers):
    ys, xs = np.nonzero(pixels)
    if not len(xs):
        return None
    seeds = []
    for index, (center_x, center_y) in enumerate(hole_centers):
        distances = ((xs - center_x) ** 2 + (ys - center_y) ** 2)
        for candidate in np.argsort(distances):
            seed = (int(ys[candidate]), int(xs[candidate]))
            if seed not in seeds:
                break
        else:
            return None
        seeds.append(seed)
    distance_maps = []
    for seed_y, seed_x in seeds:
        distance_input = np.ones(pixels.shape, dtype=np.uint8)
        distance_input[seed_y, seed_x] = 0
        distance_maps.append(cv2.distanceTransform(distance_input, cv2.DIST_L2, 5))
    labels = np.argmin(np.stack(distance_maps, axis=0), axis=0).astype(np.int8)
    labels[~pixels] = -1
    if all(_connected(labels == index) for index in range(len(seeds))):
        return labels
    return _legacy_wave(pixels, seeds)


def _connected(mask):
    count, _ = cv2.connectedComponents(np.ascontiguousarray(mask, dtype=np.uint8) if mask.dtype != np.uint8 else mask,
                                       connectivity=8, ltype=cv2.CV_32S)
    return count == 2


def _fixture_masks():
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


def _hole_centers(mask):
    holes, centers, _ = _enclosed_holes(mask, 30, 16)
    return centers[holes] if len(holes) in (2, 3) else None


def _assert_same(left, right, label):
    if left is None or right is None:
        assert left is None and right is None, f"{label}: None mismatch"
    else:
        assert left.shape == right.shape, f"{label}: shape mismatch"
        assert np.array_equal(left, right), f"{label}: labels differ"


@pytest.mark.parametrize("name,mask", _fixture_masks())
def test_full_assignment_matches_legacy(name, mask):
    centers = _hole_centers(mask)
    if centers is None:
        pytest.skip("no 2/3-hole evidence")
    _assert_same(_geodesic_hole_assignments(mask, centers),
                 _legacy_assignments(mask, centers), name)


def _u_shape():
    mask = np.zeros((24, 24), dtype=bool)
    mask[3:21, 3:7] = True
    mask[3:7, 3:21] = True
    mask[17:21, 3:21] = True
    return mask


def _spiral():
    mask = np.zeros((30, 30), dtype=bool)
    mask[2:28, 2:6] = True
    mask[2:6, 2:28] = True
    mask[24:28, 2:28] = True
    mask[8:28, 24:28] = True
    mask[8:12, 10:28] = True
    return mask


def _random_blobs(trials=25):
    rng = np.random.default_rng(20261102)
    out = []
    for trial in range(trials):
        mask = np.zeros((32, 32), dtype=bool)
        for _ in range(6):
            y = int(rng.integers(4, 28))
            x = int(rng.integers(4, 28))
            h = int(rng.integers(3, 12))
            w = int(rng.integers(3, 12))
            mask[y:y + h, x:x + w] = True
        # keep the largest connected component so seeds are reachable
        count, labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
        if count < 2:
            continue
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        mask = labels == sizes.argmax()
        ys, xs = np.nonzero(mask)
        if len(ys) < 40:
            continue
        seed_count = 2 + trial % 2
        picks = rng.choice(len(ys), size=seed_count, replace=False)
        seeds = [(int(ys[i]), int(xs[i])) for i in picks]
        out.append((f"blob-{trial}", mask, seeds))
    return out


@pytest.mark.parametrize("name,mask,seeds", [("u-shape", _u_shape(), [(19, 12), (5, 12)]),
                                             ("spiral", _spiral(), [(25, 15), (4, 4)])]
                         + _random_blobs())
def test_wave_matches_legacy(name, mask, seeds):
    _assert_same(_wave_assign(mask, seeds), _legacy_wave(mask, seeds), name)


def test_wave_returns_none_for_unreachable_foreground():
    mask = np.zeros((20, 20), dtype=bool)
    mask[2:6, 2:6] = True
    mask[12:18, 12:18] = True  # disconnected island without a seed
    assert _wave_assign(mask, [(3, 3)]) is None


def test_wave_tie_break_prefers_lower_index():
    mask = np.zeros((3, 5), dtype=bool)
    mask[1, :] = True
    seeds = [(1, 0), (1, 4)]
    labels = _wave_assign(mask, seeds)
    # the middle pixel is equidistant; the lower seed index must claim it
    assert labels[1, 2] == 0