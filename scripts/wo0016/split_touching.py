"""Conservative pixel-domain split for touching hollow nuts in Binary8."""
from __future__ import annotations

import cv2
import numpy as np


def is_single_connected_component(mask: np.ndarray) -> bool:
    """Return whether a non-empty 2D mask is one 8-connected region."""
    pixels = np.asarray(mask)
    if pixels.ndim != 2 or not np.any(pixels):
        return False
    count, _, _, _ = cv2.connectedComponentsWithStats(
        (pixels != 0).astype(np.uint8), connectivity=8
    )
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


def _split_by_enclosed_holes(pixels: np.ndarray, min_child_area: int, min_hole_area: int = 30
                             ) -> tuple[np.ndarray, ...] | None:
    """Assign a joined cluster to two or three independently visible nut holes."""
    height, width = pixels.shape
    area = int(np.count_nonzero(pixels))
    count, _, stats, centers = cv2.connectedComponentsWithStats(
        (~pixels).astype(np.uint8), connectivity=8
    )
    hole_area_threshold = max(min_hole_area, int(np.ceil(0.06 * area)))
    holes = [index for index in range(1, count)
             if stats[index, cv2.CC_STAT_AREA] >= hole_area_threshold
             and stats[index, cv2.CC_STAT_LEFT] > 0
             and stats[index, cv2.CC_STAT_TOP] > 0
             and stats[index, cv2.CC_STAT_LEFT] + stats[index, cv2.CC_STAT_WIDTH] < width
             and stats[index, cv2.CC_STAT_TOP] + stats[index, cv2.CC_STAT_HEIGHT] < height]
    if len(holes) not in (2, 3):
        return None
    hole_centers = centers[holes]
    separation = np.linalg.norm(hole_centers[:, None] - hole_centers[None, :],
                                axis=2)
    if np.any(separation[np.triu_indices(len(holes), 1)] < 10):
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


def split_touching_component(component: np.ndarray, *, min_child_area: int = 12,
                             min_hole_area: int = 30
                             ) -> tuple[np.ndarray, ...]:
    """Split two or three touching nuts when holes or a narrow neck are visible.

    A silhouette without distinct holes or a neck is ambiguous (for example
    flush rectangles), so it is left intact. Masks preserve the input union.
    """
    if isinstance(min_hole_area, bool) or not isinstance(min_hole_area, (int, np.integer)) or min_hole_area < 1:
        raise ValueError("min_hole_area must be a positive integer")
    pixels = np.asarray(component) != 0
    if pixels.ndim != 2:
        raise ValueError("component must be a 2D mask")
    area = int(np.count_nonzero(pixels))
    if area < 2 * min_child_area:
        return (pixels,)
    if not is_single_connected_component(pixels):
        return (pixels,)
    hole_pieces = _split_by_enclosed_holes(pixels, min_child_area, min_hole_area)
    if hole_pieces is not None:
        return hole_pieces
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
            strip = pixels[:, cut - 1 if projection[cut - 1] < projection[cut] else cut] if axis == 0 else pixels[cut - 1 if projection[cut - 1] < projection[cut] else cut, :]
            runs = np.count_nonzero(strip & ~np.r_[False, strip[:-1]])
            # A round hollow nut can have two runs through its central hole.
            # Two touching round nuts form an elongated, balanced pair instead.
            elongated_pair = (max(pixels.shape) >= 1.65 * min(pixels.shape)
                              and 0.35 <= left_area / area <= 0.65)
            if runs != 1 and not elongated_pair:
                continue
            score = (neck / min(left_peak, right_peak), -min(left_area, right_area))
            candidates.append((score, axis, cut))
    if not candidates:
        return (pixels,)
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
        return (pixels,)
    return first, second
