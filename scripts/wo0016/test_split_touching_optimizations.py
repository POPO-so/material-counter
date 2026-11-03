"""Equivalence coverage for the bounded split-touching hot-path optimizations."""
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import split_touching


def _touching_hollow_grid(rows: int, columns: int) -> np.ndarray:
    """Build a connected cluster with one visible enclosed hole per piece."""
    outer_size = 23
    pitch = 24
    pixels = np.zeros((rows * pitch + 2, columns * pitch + 2), dtype=bool)
    for row in range(rows):
        for column in range(columns):
            top = 1 + row * pitch
            left = 1 + column * pitch
            pixels[top:top + outer_size, left:left + outer_size] = True
            pixels[top + 5:top + 18, left + 5:left + 18] = False
            if column + 1 < columns:
                pixels[top + 10:top + 13,
                       left + outer_size:left + pitch] = True
            if row + 1 < rows:
                pixels[top + outer_size:top + pitch,
                       left + 10:left + 13] = True
    return pixels


def _spread_out_sixteen_hole_cluster() -> np.ndarray:
    """Build a cap-sized, irregular sixteen-hole cluster with a single bridge path."""
    starts = (1, 78, 155, 232)
    size = 23
    pixels = np.zeros((256, 256), dtype=bool)
    for top in starts:
        for left in starts:
            pixels[top:top + size, left:left + size] = True
            pixels[top + 5:top + 18, left + 5:left + 18] = False

    path = []
    for row in range(4):
        columns = range(4) if row % 2 == 0 else range(3, -1, -1)
        path.extend((row, column) for column in columns)
    for (row_a, column_a), (row_b, column_b) in zip(path, path[1:]):
        top_a, left_a = starts[row_a], starts[column_a]
        top_b, left_b = starts[row_b], starts[column_b]
        if row_a == row_b:
            pixels[top_a + 11, min(left_a, left_b) + size:max(left_a, left_b) + 1] = True
        else:
            pixels[min(top_a, top_b) + size:max(top_a, top_b) + 1, left_a + 11] = True
    return pixels


def test_visible_enclosed_holes_split_four_and_eight_pieces():
    for rows, columns, expected_count in ((1, 4, 4), (1, 8, 8)):
        pixels = _touching_hollow_grid(rows, columns)
        assert split_touching.is_single_connected_component(pixels)
        pieces = split_touching.split_touching_component(pixels)
        assert len(pieces) == expected_count
        assert split_touching._valid_partition(pixels, pieces)
        assert all(np.count_nonzero(piece) >= 12 for piece in pieces)


def test_failed_many_hole_split_keeps_parent_instead_of_projection_guess():
    pixels = _spread_out_sixteen_hole_cluster()
    assert split_touching.is_single_connected_component(pixels)
    assert len(split_touching.enclosed_hole_centers(pixels)) == 16
    assert np.count_nonzero(pixels) <= 8192
    assert pixels.shape[0] * pixels.shape[1] == 65536

    pieces = split_touching.split_touching_component(pixels)

    assert len(pieces) == 1
    np.testing.assert_array_equal(pieces[0], pixels)


def test_enclosed_hole_seed_limit_accepts_sixteen_and_rejects_seventeen():
    at_limit = _touching_hollow_grid(1, 16)
    pieces = split_touching.split_touching_component(at_limit)
    assert len(pieces) == 16
    assert split_touching._valid_partition(at_limit, pieces, min_child_area=12)

    over_limit = _touching_hollow_grid(1, 17)
    unchanged, = split_touching.split_touching_component(over_limit)
    np.testing.assert_array_equal(unchanged, over_limit)


def test_split_resource_limits_are_inclusive_and_reject_values_above_caps():
    at_foreground_limit = np.zeros((256, 256), dtype=bool)
    at_foreground_limit[:32, :] = True
    assert np.count_nonzero(at_foreground_limit) == 8192
    assert split_touching._within_split_limits(at_foreground_limit)
    over_foreground_limit = at_foreground_limit.copy()
    over_foreground_limit[32, 0] = True
    assert not split_touching._within_split_limits(over_foreground_limit)

    at_bbox_limit = np.zeros((256, 256), dtype=bool)
    at_bbox_limit[0, :] = True
    at_bbox_limit[:, 0] = True
    at_bbox_limit[-1, :] = True
    at_bbox_limit[:, -1] = True
    assert np.count_nonzero(at_bbox_limit) <= 8192
    assert split_touching._within_split_limits(at_bbox_limit)
    over_bbox_limit = np.zeros((257, 256), dtype=bool)
    over_bbox_limit[0, :] = True
    over_bbox_limit[:, 0] = True
    over_bbox_limit[-1, :] = True
    over_bbox_limit[:, -1] = True
    assert not split_touching._within_split_limits(over_bbox_limit)


def test_split_returns_over_limit_connected_masks_unchanged():
    over_foreground_limit = np.ones((100, 100), dtype=bool)
    unchanged, = split_touching.split_touching_component(over_foreground_limit)
    np.testing.assert_array_equal(unchanged, over_foreground_limit)

    over_bbox_limit = np.zeros((257, 256), dtype=bool)
    over_bbox_limit[0, :] = True
    over_bbox_limit[:, 0] = True
    over_bbox_limit[-1, :] = True
    over_bbox_limit[:, -1] = True
    unchanged, = split_touching.split_touching_component(over_bbox_limit)
    np.testing.assert_array_equal(unchanged, over_bbox_limit)


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


def _legacy_geodesic_hole_assignments(pixels: np.ndarray,
                               hole_centers: np.ndarray) -> np.ndarray | None:
    """Assign foreground to hole seeds, validating connectivity before use.

    Euclidean distance transforms handle ordinary hollow-piece clusters in
    native code. If their Voronoi regions disconnect inside a concave shape,
    a dilation-wave geodesic flood assigns each new pixel from an adjacent
    label, keeping every child connected by construction.
    """
    ys, xs = np.nonzero(pixels)
    if not len(xs):
        return None
    seeds: list[tuple[int, int]] = []
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
    if all(split_touching.is_single_connected_component(labels == index)
           for index in range(len(seeds))):
        return labels

    labels.fill(-1)
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


def test_geodesic_flood_preserves_ties_with_one_dilation_per_wave(monkeypatch):
    pixels = np.ones((9, 15), dtype=bool)
    centers = np.array([[2., 4.], [12., 4.]])
    # Force the concave-mask fallback to isolate the multi-source wave itself.
    monkeypatch.setattr(split_touching, 'is_single_connected_component', lambda _: False)
    original = cv2.dilate
    calls = []
    def dilate(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(cv2, 'dilate', dilate)
    expected = _legacy_geodesic_hole_assignments(pixels, centers)
    old_calls = len(calls)
    calls.clear()
    actual = split_touching._geodesic_hole_assignments(pixels, centers)
    np.testing.assert_array_equal(actual, expected)
    assert len(calls) <= old_calls // len(centers)


def test_projection_neck_does_not_build_a_strip_for_each_cut(monkeypatch):
    pixels = np.ones((20, 50), dtype=bool)
    pixels[:, 22:28] = False
    pixels[9:11, 22:28] = True
    expected = _legacy_split_by_projection_neck(pixels, 12)
    assert expected is not None
    class ForbiddenConcatenation:
        def __getitem__(self, _):
            raise AssertionError('per-cut strip concatenation')
    monkeypatch.setattr(np, 'r_', ForbiddenConcatenation())
    actual = split_touching._split_by_projection_neck(pixels, 12)
    _assert_same_partition(actual, expected, pixels)



def test_geodesic_flood_matches_legacy_on_seeded_masks_and_three_seeds(monkeypatch):
    monkeypatch.setattr(split_touching, 'is_single_connected_component', lambda _: False)
    rng = np.random.default_rng(20261031)
    for count in (2, 3):
        for _ in range(24):
            pixels = rng.random((13, 19)) > .3
            centers = rng.random((count, 2)) * (18, 12)
            expected = _legacy_geodesic_hole_assignments(pixels, centers)
            actual = split_touching._geodesic_hole_assignments(pixels, centers)
            assert (actual is None) == (expected is None)
            if expected is not None:
                np.testing.assert_array_equal(actual, expected)


def test_four_touching_rings_keep_four_disjoint_connected_partitions():
    mask = np.zeros((120, 120), dtype=np.uint8)
    for x, y in ((40, 40), (80, 40), (40, 80), (80, 80)):
        cv2.circle(mask, (x, y), 25, 255, -1)
        cv2.circle(mask, (x, y), 10, 0, -1)
    pieces = split_touching.split_touching_component(mask)
    assert len(pieces) == 4
    assert np.array_equal(np.sum(pieces, axis=0), mask != 0)
    assert all(split_touching.is_single_connected_component(piece) for piece in pieces)


def test_hole_split_does_not_recheck_connectivity_after_assignment(monkeypatch):
    mask = np.zeros((120, 120), dtype=np.uint8)
    for x, y in ((40, 40), (80, 40), (40, 80), (80, 80)):
        cv2.circle(mask, (x, y), 25, 255, -1)
        cv2.circle(mask, (x, y), 10, 0, -1)
    assign = split_touching._geodesic_hole_assignments
    check = split_touching.is_single_connected_component
    after_assignment = False

    def assign_and_mark(*args):
        nonlocal after_assignment
        result = assign(*args)
        after_assignment = True
        return result

    def check_before_assignment(piece):
        assert not after_assignment
        return check(piece)

    monkeypatch.setattr(split_touching, "_geodesic_hole_assignments", assign_and_mark)
    monkeypatch.setattr(split_touching, "is_single_connected_component", check_before_assignment)
    pieces = split_touching._split_by_enclosed_holes(mask != 0, 12)
    assert len(pieces) == 4
    assert all(check(piece) for piece in pieces)
