from __future__ import annotations

import cv2
import numpy as np
import pytest


def test_area_filter_keeps_run_labels_consistent(native_library):
    from scripts.rle.components import analyze_runs, local_mask
    # Two separate components; only the length-three run qualifies.
    runs=np.array([(2<<20)|(2<<10)|2,(5<<20)|(10<<10)|12],dtype=np.uint32)
    analysis=analyze_runs(runs,library=native_library,min_area=2)
    assert len(analysis.components)==1 and analysis.components[0].area==3
    assert analysis.labels.tolist()==[0xffffffff,0]
    assert local_mask(runs,analysis.labels,0).shape==(1,3)


def test_native_filtered_abi_returns_only_qualified_records(native_library):
    import ctypes
    from scripts.rle.components import _load_library, _NativeComponent
    _load_library(native_library)
    function=ctypes.CDLL(str(native_library)).rle_components_filtered
    function.argtypes=[ctypes.POINTER(ctypes.c_uint32),ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(_NativeComponent),
        ctypes.c_uint32,ctypes.c_uint32]
    function.restype=ctypes.c_int
    runs=(ctypes.c_uint32*2)((2<<20)|(2<<10)|2,(5<<20)|(10<<10)|12)
    labels=(ctypes.c_uint32*2)()
    output=(_NativeComponent*2)()
    assert function(runs,2,labels,output,2,2)==1
    assert list(labels)==[0xffffffff,0] and output[0].area==3

from .components import analyze_runs, build_native, local_mask


@pytest.fixture(scope="session")
def native_library(tmp_path_factory):
    output = tmp_path_factory.mktemp("rle-native") / "rle_components.dll"
    return build_native(output)


def encode_mask(mask: np.ndarray) -> np.ndarray:
    """Test-only Binary8-to-run encoder using row-major maximal runs."""
    runs = []
    for row, values in enumerate(mask != 0):
        edges = np.diff(np.pad(values.astype(np.int8), (1, 1)))
        starts = np.flatnonzero(edges == 1)
        ends = np.flatnonzero(edges == -1) - 1
        runs.extend((row << 20) | (int(start) << 10) | int(end)
                    for start, end in zip(starts, ends))
    return np.asarray(runs, dtype=np.uint32)


def cv_components(mask):
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8, ltype=cv2.CV_32S)
    expected = []
    for label in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[label])
        expected.append((x, y, x + width - 1, y + height - 1, area,
                         float(centroids[label, 0]), float(centroids[label, 1])))
    expected.sort(key=lambda item: (item[1], item[0]))
    return labels, expected


def assert_matches_opencv(mask, runs, library):
    result = analyze_runs(runs, library=library)
    labels, expected = cv_components(mask)
    actual = sorted(
        ((c.x_min, c.y_min, c.x_max, c.y_max, c.area,
          c.centroid_x, c.centroid_y) for c in result.components),
        key=lambda item: (item[1], item[0], item[2], item[3]),
    )
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        assert got[:5] == want[:5]
        assert got[5:] == pytest.approx(want[5:])
    for index, component in enumerate(result.components):
        restored = local_mask(runs, result.labels, index)
        x0, y0, x1, y1 = component.x_min, component.y_min, component.x_max, component.y_max
        run_index = int(np.flatnonzero(result.labels == index)[0])
        first_run = int(runs[run_index])
        expected_label = labels[first_run >> 20, (first_run >> 10) & 0x3FF]
        assert np.array_equal(restored != 0,
                              labels[y0:y1 + 1, x0:x1 + 1] == expected_label)
    return result


def test_eight_connectivity_handles_diagonal_and_branching_merge(native_library):
    mask = np.zeros((8, 12), np.uint8)
    mask[1, 1:5] = 1
    mask[2, 4:7] = 1  # diagonal touch at x=4
    mask[3, 2:4] = 1
    mask[3, 7:9] = 1
    mask[4, 3:8] = 1  # bridges both prior runs
    mask[6, 10] = 1
    assert_matches_opencv(mask, encode_mask(mask), native_library)


def test_empty_and_full_masks(native_library):
    empty = np.zeros((5, 7), np.uint8)
    result = assert_matches_opencv(empty, encode_mask(empty), native_library)
    assert result.components == ()

    full = np.ones((9, 13), np.uint8)
    result = assert_matches_opencv(full, encode_mask(full), native_library)
    assert len(result.components) == 1
    assert result.components[0].area == full.size


def test_components_remain_separate_across_blank_rows(native_library):
    mask = np.zeros((9, 9), np.uint8)
    mask[1, 1:3] = 1
    mask[3, 1:3] = 1  # one blank row prevents 8-connectivity
    assert_matches_opencv(mask, encode_mask(mask), native_library)


def test_holes_and_random_masks_match_opencv(native_library):
    mask = np.ones((11, 13), np.uint8)
    mask[3:8, 4:9] = 0
    assert_matches_opencv(mask, encode_mask(mask), native_library)

    rng = np.random.default_rng(214)
    for _ in range(24):
        random_mask = (rng.random((17, 21)) < 0.28).astype(np.uint8)
        assert_matches_opencv(random_mask, encode_mask(random_mask), native_library)


def test_full_camera_geometry_matches_opencv(native_library):
    rng = np.random.default_rng(99)
    mask = (rng.random((400, 640)) < 0.025).astype(np.uint8)
    result = assert_matches_opencv(mask, encode_mask(mask), native_library)
    assert sum(component.area for component in result.components) == int(mask.sum())


@pytest.mark.parametrize("runs", [
    [((1 << 20) | (2 << 10) | 1)],  # reversed interval
    [((1 << 20) | (1 << 10) | 3), ((1 << 20) | (3 << 10) | 5)],  # non-maximal
    [((1 << 20) | (2 << 10) | 4), ((1 << 20) | (1 << 10) | 1)],  # unsorted
    [(1 << 29)],  # reserved high bit
    [(512 << 20)],  # row outside supported geometry
])
def test_invalid_runs_are_rejected(native_library, runs):
    with pytest.raises(ValueError):
        analyze_runs(np.asarray(runs, np.uint32), library=native_library)


def test_run_and_label_count_must_match(native_library):
    with pytest.raises(ValueError):
        local_mask(np.asarray([1 << 10], np.uint32), np.asarray([], np.uint32), 0)


def _reference_local_mask(runs, labels, component_index):
    """Independent copy of the pre-vectorized per-run rasterization."""
    selected = np.flatnonzero(labels == component_index)
    if not len(selected):
        raise ValueError("component_index has no runs")
    x_min = min(int((int(runs[i]) >> 10) & 0x3FF) for i in selected)
    x_max = max(int(runs[i]) & 0x3FF for i in selected)
    y_min = min(int(runs[i]) >> 20 for i in selected)
    y_max = max(int(runs[i]) >> 20 for i in selected)
    mask = np.zeros((y_max - y_min + 1, x_max - x_min + 1), dtype=np.uint8)
    for index in selected:
        word = int(runs[index])
        row = (word >> 20) - y_min
        begin = ((word >> 10) & 0x3FF) - x_min
        end = (word & 0x3FF) - x_min
        mask[row, begin:end + 1] = 255
    return mask


def test_local_mask_matches_run_reference_at_coordinate_boundaries():
    runs = np.asarray([
        (0 << 20) | (0 << 10) | 0,
        (0 << 20) | (1022 << 10) | 1023,
        (2 << 20) | (3 << 10) | 7,
        (2 << 20) | (20 << 10) | 20,
        (511 << 20) | (1023 << 10) | 1023,
    ], dtype=np.uint32)
    labels = np.asarray([4, 4, 4, 9, 4], dtype=np.uint32)
    actual = local_mask(runs, labels, 4)
    expected = _reference_local_mask(runs, labels, 4)
    assert actual.dtype == np.uint8
    assert np.array_equal(actual, expected)
    assert set(np.unique(actual)) <= {0, 255}
    assert actual.shape == (512, 1024)


def test_local_mask_matches_run_reference_for_random_runs():
    rng = np.random.default_rng(143)
    for _ in range(100):
        height = int(rng.integers(1, 20))
        width = int(rng.integers(1, 40))
        runs = []
        for y in range(height):
            occupied = rng.random(width) < rng.uniform(0.05, 0.8)
            edges = np.diff(np.pad(occupied.astype(np.int8), (1, 1)))
            starts = np.flatnonzero(edges == 1)
            ends = np.flatnonzero(edges == -1) - 1
            runs.extend((y << 20) | (int(a) << 10) | int(b)
                        for a, b in zip(starts, ends))
        runs = np.asarray(runs, dtype=np.uint32)
        labels = rng.integers(0, 4, size=len(runs), dtype=np.uint32)
        for component_index in range(4):
            selected = np.flatnonzero(labels == component_index)
            if not len(selected):
                with pytest.raises(ValueError):
                    local_mask(runs, labels, component_index)
                continue
            actual = local_mask(runs, labels, component_index)
            expected = _reference_local_mask(runs, labels, component_index)
            assert actual.dtype == np.uint8
            assert np.array_equal(actual, expected)
            assert set(np.unique(actual)) <= {0, 255}
