"""Equivalence coverage for the bounded split-touching hot-path optimizations."""
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import split_touching


def _legacy_hole_area_threshold(area: int, min_hole_area: int) -> int:
    relative = int(np.ceil(0.06 * area))
    return max(min_hole_area, min(relative, 16))


def test_hole_area_threshold_matches_legacy_below_sixteen_at_all_area_boundaries():
    # Exhausting this range crosses every ceil(0.06 * area) transition before
    # the legacy cap is reached, including both sides of each transition.
    for min_hole_area in range(1, 16):
        for area in range(0, 501):
            assert split_touching._hole_area_threshold(area, min_hole_area) == (
                _legacy_hole_area_threshold(area, min_hole_area)
            )


def test_hole_area_threshold_matches_legacy_at_or_above_sixteen():
    for min_hole_area in (16, 17, 30, 256):
        for area in (0, 1, 251, 540, 10000):
            assert split_touching._hole_area_threshold(area, min_hole_area) == (
                _legacy_hole_area_threshold(area, min_hole_area)
            )


def _legacy_split_by_projection_neck(
    pixels: np.ndarray, min_child_area: int
) -> tuple[np.ndarray, np.ndarray] | None:
    """Reference implementation with per-cut max, before prefix/suffix caching."""
    area = int(np.count_nonzero(pixels))
    candidates = []
    for axis in (0, 1):
        projection = np.count_nonzero(pixels, axis=axis)
        length = len(projection)
        if length < 7:
            continue
        cumulative = np.cumsum(projection)
        for cut in range(3, length - 3):
            left_area = int(cumulative[cut - 1])
            right_area = area - left_area
            if min(left_area, right_area) < min_child_area:
                continue
            neck = min(projection[cut - 1], projection[cut])
            left_peak = int(np.max(projection[:cut]))
            right_peak = int(np.max(projection[cut:]))
            if not 0 < neck <= 0.65 * min(left_peak, right_peak):
                continue
            strip_index = cut - 1 if projection[cut - 1] < projection[cut] else cut
            strip = pixels[:, strip_index] if axis == 0 else pixels[strip_index, :]
            runs = np.count_nonzero(strip & ~np.r_[False, strip[:-1]])
            elongated_pair = (
                max(pixels.shape) >= 1.65 * min(pixels.shape)
                and 0.25 <= left_area / area <= 0.75
            )
            if runs != 1 and not elongated_pair:
                continue
            score = (neck / min(left_peak, right_peak), -min(left_area, right_area))
            candidates.append((score, axis, cut))

    if not candidates:
        return None
    _, axis, cut = min(candidates)
    first = np.zeros_like(pixels)
    second = np.zeros_like(pixels)
    if axis == 0:
        first[:, :cut] = pixels[:, :cut]
        second[:, cut:] = pixels[:, cut:]
    else:
        first[:cut] = pixels[:cut]
        second[cut:] = pixels[cut:]
    if not all(split_touching.is_single_connected_component(piece)
               for piece in (first, second)):
        return None
    return first, second


def _assert_same_partition(actual, expected, source: np.ndarray) -> None:
    assert (actual is None) == (expected is None)
    if expected is None:
        return
    assert len(actual) == len(expected)
    for actual_piece, expected_piece in zip(actual, expected):
        np.testing.assert_array_equal(actual_piece, expected_piece)
    np.testing.assert_array_equal(np.logical_or.reduce(actual), source)


def _random_masks() -> list[np.ndarray]:
    rng = np.random.default_rng(20261026)
    masks = []
    for index in range(96):
        height = int(rng.integers(1, 58))
        width = int(rng.integers(1, 78))
        density = (0.12, 0.35, 0.62)[index % 3]
        masks.append(rng.random((height, width)) < density)

    # Include varied joined-lobe silhouettes, with deterministic variation in
    # their dimensions and bridge thickness to exercise real candidate cuts.
    for index in range(24):
        height = 18 + index % 11
        width = 36 + (index * 7) % 25
        mask = np.zeros((height, width), dtype=bool)
        cv2.ellipse(mask.view(np.uint8), (width // 4, height // 2),
                    (width // 4, height // 3), 0, 0, 360, 1, -1)
        cv2.ellipse(mask.view(np.uint8), (3 * width // 4, height // 2),
                    (width // 4, height // 3), 0, 0, 360, 1, -1)
        bridge_y = height // 2 + (index % 3 - 1)
        bridge_half = 1 + index % 2
        mask[max(0, bridge_y - bridge_half):bridge_y + bridge_half + 1,
             width // 2 - 2:width // 2 + 3] = True
        masks.append(mask)
    return masks


def _recorded_component_masks() -> list[np.ndarray]:
    fixture = Path(__file__).resolve().parent / "fixtures" / "lighting_b2_m6_touch_masks.npz"
    masks = []
    with np.load(fixture, allow_pickle=False) as archive:
        for frame in archive["masks"]:
            count, labels, stats, _ = cv2.connectedComponentsWithStats(
                (frame != 0).astype(np.uint8), connectivity=8
            )
            for label in range(1, count):
                left = int(stats[label, cv2.CC_STAT_LEFT])
                top = int(stats[label, cv2.CC_STAT_TOP])
                width = int(stats[label, cv2.CC_STAT_WIDTH])
                height = int(stats[label, cv2.CC_STAT_HEIGHT])
                crop = labels[top:top + height, left:left + width] == label
                masks.append(np.pad(crop, 3))
    return masks


def test_projection_neck_matches_legacy_for_random_and_recorded_masks():
    masks = _random_masks() + _recorded_component_masks()
    for pixels in masks:
        expected = _legacy_split_by_projection_neck(pixels, min_child_area=12)
        actual = split_touching._split_by_projection_neck(pixels, min_child_area=12)
        _assert_same_partition(actual, expected, pixels)
