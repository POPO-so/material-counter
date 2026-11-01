"""Conservative pixel-domain split for touching hollow nuts in Binary8."""
from __future__ import annotations

import cv2
import numpy as np

_MAX_RELATIVE_HOLE_AREA_THRESHOLD = 16


def _hole_area_threshold(area: int, min_hole_area: int, *,
                         relative_cap: int = _MAX_RELATIVE_HOLE_AREA_THRESHOLD) -> int:
    """Keep the relative gate below the smallest calibrated M6 hole (17 px)."""
    if min_hole_area >= relative_cap:
        return int(min_hole_area)
    relative = int(np.ceil(0.06 * area))
    return max(min_hole_area,
               min(relative, relative_cap))


def is_single_connected_component(mask: np.ndarray) -> bool:
    """Return whether a non-empty 2D mask is one 8-connected region."""
    pixels = np.asarray(mask)
    if pixels.ndim != 2 or pixels.shape[0] == 0 or pixels.shape[1] == 0:
        return False
    binary = (np.ascontiguousarray(pixels) if pixels.dtype == np.uint8
              else np.asarray(pixels != 0, dtype=np.uint8))
    count, _ = cv2.connectedComponents(binary, connectivity=8, ltype=cv2.CV_32S)
    return count == 2


def _geodesic_hole_assignments(pixels: np.ndarray,
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
    if all(is_single_connected_component(labels == index)
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


def _enclosed_holes(pixels: np.ndarray, min_hole_area: int,
                    hole_area_cap: int) -> tuple[list[int], np.ndarray, int]:
    """Return the interior background components the splitter accepts as holes."""
    height, width = pixels.shape
    area = int(np.count_nonzero(pixels))
    count, _, stats, centers = cv2.connectedComponentsWithStats(
        (~pixels).astype(np.uint8), connectivity=8
    )
    hole_area_threshold = _hole_area_threshold(area, min_hole_area, relative_cap=hole_area_cap)
    holes = [index for index in range(1, count)
             if stats[index, cv2.CC_STAT_AREA] >= hole_area_threshold
             and stats[index, cv2.CC_STAT_LEFT] > 0
             and stats[index, cv2.CC_STAT_TOP] > 0
             and stats[index, cv2.CC_STAT_LEFT] + stats[index, cv2.CC_STAT_WIDTH] < width
             and stats[index, cv2.CC_STAT_TOP] + stats[index, cv2.CC_STAT_HEIGHT] < height]
    return holes, centers, area


def count_enclosed_holes(pixels: np.ndarray, *, min_hole_area: int = 30,
                         hole_area_cap: int = 16) -> int:
    """Count holes eligible for a touching split without running the split."""
    mask = np.asarray(pixels) != 0
    if mask.ndim != 2 or not mask.size:
        return 0
    holes, _, _ = _enclosed_holes(mask, int(min_hole_area), int(hole_area_cap))
    return len(holes)


def _split_by_enclosed_holes(pixels: np.ndarray, min_child_area: int, min_hole_area: int = 30,
                             min_hole_distance: float = 10, hole_area_cap: int = 16
                             ) -> tuple[np.ndarray, ...] | None:
    """Assign a joined cluster to two or three independently visible nut holes."""
    holes, centers, area = _enclosed_holes(pixels, min_hole_area, hole_area_cap)
    if len(holes) not in (2, 3):
        return None
    hole_centers = centers[holes]
    separation = np.linalg.norm(hole_centers[:, None] - hole_centers[None, :],
                                axis=2)
    if np.any(separation[np.triu_indices(len(holes), 1)] < min_hole_distance):
        return None
    assignments = _geodesic_hole_assignments(pixels, hole_centers)
    if assignments is None:
        return None
    children = []
    for index in range(len(holes)):
        child_y, child_x = np.nonzero(assignments == index)
        if len(child_x) < max(min_child_area, int(0.16 * area)):
            return None
        child_width = int(child_x.max() - child_x.min() + 1)
        child_height = int(child_y.max() - child_y.min() + 1)
        if max(child_width, child_height) > 1.8 * min(child_width, child_height):
            return None
        child = np.zeros_like(pixels)
        child[child_y, child_x] = True
        if not is_single_connected_component(child):
            return None
        children.append(child)
    return tuple(children)


def _slice_run_counts(pixels: np.ndarray, axis: int) -> np.ndarray:
    """Count foreground runs in every row (axis 1) or column (axis 0)."""
    if axis == 0:
        first = pixels[0, :]
        transitions = pixels[1:, :] & ~pixels[:-1, :]
        return first.astype(np.int32) + transitions.sum(axis=0, dtype=np.int32)
    first = pixels[:, 0]
    transitions = pixels[:, 1:] & ~pixels[:, :-1]
    return first.astype(np.int32) + transitions.sum(axis=1, dtype=np.int32)


def _projection_neck_candidates(pixels: np.ndarray,
                                min_child_area: int) -> list[tuple[tuple[float, int], int, int]]:
    """Return every cut that satisfies the projection-neck conditions."""
    area = int(np.count_nonzero(pixels))
    candidates = []
    for axis in (0, 1):
        projection = np.count_nonzero(pixels, axis=axis)
        length = len(projection)
        if length < 7:
            continue
        cuts = np.arange(3, length - 3)
        if not len(cuts):
            continue
        cumulative = np.cumsum(projection)
        prefix_maximum = np.maximum.accumulate(projection)
        suffix_maximum = np.maximum.accumulate(projection[::-1])[::-1]
        left_area = cumulative[cuts - 1]
        right_area = area - left_area
        smaller_area = np.minimum(left_area, right_area)
        neck = np.minimum(projection[cuts - 1], projection[cuts])
        peak = np.minimum(prefix_maximum[cuts - 1], suffix_maximum[cuts])
        passing = (smaller_area >= min_child_area) & (neck > 0) & (neck <= 0.65 * peak)
        if not np.any(passing):
            continue
        slice_runs = _slice_run_counts(pixels, axis)
        strip_index = np.where(projection[cuts - 1] < projection[cuts], cuts - 1, cuts)
        runs = slice_runs[strip_index]
        # A round hollow nut can have two runs through its central hole.
        # Two touching round nuts form an elongated, balanced pair instead.
        ratio = left_area / area
        elongated = ((max(pixels.shape) >= 1.65 * min(pixels.shape))
                     & (0.25 <= ratio) & (ratio <= 0.75))
        for index in np.flatnonzero(passing & ((runs == 1) | elongated)):
            candidates.append(((float(neck[index]) / float(peak[index]),
                                -int(smaller_area[index])), axis, int(cuts[index])))
    return candidates


def has_projection_neck(pixels: np.ndarray, *, min_child_area: int) -> bool:
    """Return whether the splitter would find any projection-neck cut."""
    mask = np.asarray(pixels) != 0
    if mask.ndim != 2 or not mask.size:
        return False
    return bool(_projection_neck_candidates(mask, int(min_child_area)))


def _split_by_projection_neck(pixels: np.ndarray,
                              min_child_area: int) -> tuple[np.ndarray, np.ndarray] | None:
    """Return a connected two-way cut only when a narrow projected neck exists."""
    candidates = _projection_neck_candidates(pixels, min_child_area)
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
    if not all(is_single_connected_component(piece) for piece in (first, second)):
        return None
    return first, second


def _valid_partition(parent: np.ndarray, pieces: tuple[np.ndarray, ...]) -> bool:
    if not 2 <= len(pieces) <= 3 or any(piece.shape != parent.shape for piece in pieces):
        return False
    if not all(is_single_connected_component(piece) for piece in pieces):
        return False
    coverage = np.sum(np.stack(pieces), axis=0)
    return (np.array_equal(coverage > 0, parent)
            and not np.any(coverage > 1))


def _split_recursive(pixels: np.ndarray, min_child_area: int, min_hole_area: int,
                     *, recursive_item_area: int | None,
                     min_hole_distance: float = 10,
                     hole_area_cap: int = 16,
                     depth: int = 0) -> tuple[np.ndarray, ...]:
    """Retry a large two-seed child so a visible 1+2 cluster can yield three."""
    area = int(np.count_nonzero(pixels))
    if depth >= 2 or area < 2 * min_child_area:
        return (pixels,)

    hole_pieces = (_split_by_enclosed_holes(pixels, min_child_area, min_hole_area)
                  if min_hole_distance == 10 and hole_area_cap == 16 else
                  _split_by_enclosed_holes(pixels, min_child_area, min_hole_area,
                                           min_hole_distance=min_hole_distance,
                                           hole_area_cap=hole_area_cap))
    if hole_pieces is not None:
        if (len(hole_pieces) == 3 or recursive_item_area is None
                or area < (3 if depth == 0 else 2) * recursive_item_area):
            return hole_pieces
        if len(hole_pieces) == 2:
            for index in sorted(range(2),
                                key=lambda i: int(np.count_nonzero(hole_pieces[i])),
                                reverse=True):
                child = hole_pieces[index]
                if (int(np.count_nonzero(child)) < 2 * min_child_area
                        or recursive_item_area is None
                        or int(np.count_nonzero(child)) < 2 * recursive_item_area):
                    continue
                nested = _split_recursive(child, min_child_area, min_hole_area,
                                          recursive_item_area=recursive_item_area,
                                          min_hole_distance=min_hole_distance,
                                          hole_area_cap=hole_area_cap, depth=depth + 1)
                if len(nested) > 1 and len(nested) + 1 <= 3:
                    candidate = tuple(hole_pieces[i] for i in range(2) if i != index) + nested
                    if _valid_partition(pixels, candidate):
                        return candidate
            return hole_pieces

    neck_pieces = _split_by_projection_neck(pixels, min_child_area)
    if neck_pieces is None:
        return (pixels,)
    if (recursive_item_area is None
            or area < (3 if depth == 0 else 2) * recursive_item_area):
        return neck_pieces
    for index in sorted(range(2),
                        key=lambda i: int(np.count_nonzero(neck_pieces[i])),
                        reverse=True):
        child = neck_pieces[index]
        if (int(np.count_nonzero(child)) < 2 * min_child_area
                or int(np.count_nonzero(child)) < 2 * recursive_item_area):
            continue
        nested = _split_recursive(child, min_child_area, min_hole_area,
                                  recursive_item_area=recursive_item_area,
                                  min_hole_distance=min_hole_distance,
                                  hole_area_cap=hole_area_cap, depth=depth + 1)
        if len(nested) > 1 and len(nested) + 1 <= 3:
            candidate = tuple(neck_pieces[i] for i in range(2) if i != index) + nested
            if _valid_partition(pixels, candidate):
                return candidate
    return neck_pieces


def split_touching_component(component: np.ndarray, *, min_child_area: int = 12,
                             min_hole_area: int = 30,
                             recursive_item_area: int | None = None,
                             min_hole_distance: float = 10, hole_area_cap: int = 16,
                             assume_connected: bool = False
                             ) -> tuple[np.ndarray, ...]:
    """Split two or three touching nuts when holes or a narrow neck are visible.

    Two-hole partitions may contain an oversized joined child; that child is
    retried so a three-item silhouette with one obscured hole can still split.
    Ambiguous silhouettes stay intact, and accepted masks preserve the input
    union without overlap. ``assume_connected`` lets a caller that obtained the
    mask from a connected-component pass skip the repeated connectivity check.
    """
    if isinstance(min_child_area, bool) or not isinstance(min_child_area, (int, np.integer)) or min_child_area < 1:
        raise ValueError("min_child_area must be a positive integer")
    if isinstance(min_hole_area, bool) or not isinstance(min_hole_area, (int, np.integer)) or min_hole_area < 1:
        raise ValueError("min_hole_area must be a positive integer")
    if (recursive_item_area is not None
            and (isinstance(recursive_item_area, bool)
                 or not isinstance(recursive_item_area, (int, np.integer))
                 or recursive_item_area < 1)):
        raise ValueError("recursive_item_area must be a positive integer or None")
    if (isinstance(min_hole_distance, bool)
            or not isinstance(min_hole_distance, (int, float, np.integer, np.floating))
            or not np.isfinite(min_hole_distance) or min_hole_distance <= 0):
        raise ValueError("min_hole_distance must be positive and finite")
    if (isinstance(hole_area_cap, bool) or not isinstance(hole_area_cap, (int, np.integer))
            or hole_area_cap < 1):
        raise ValueError("hole_area_cap must be a positive integer")
    if not isinstance(assume_connected, bool):
        raise ValueError("assume_connected must be a bool")
    pixels = np.asarray(component) != 0
    if pixels.ndim != 2:
        raise ValueError("component must be a 2D mask")
    if int(np.count_nonzero(pixels)) < 2 * min_child_area:
        return (pixels,)
    if not assume_connected and not is_single_connected_component(pixels):
        return (pixels,)
    pieces = _split_recursive(
        pixels, int(min_child_area), int(min_hole_area),
        recursive_item_area=(None if recursive_item_area is None
                             else int(recursive_item_area)),
        min_hole_distance=float(min_hole_distance),
        hole_area_cap=int(hole_area_cap),
    )
    return pieces if _valid_partition(pixels, pieces) else (pixels,)
