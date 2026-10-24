"""Conservative pixel-domain split for touching hollow nuts in Binary8."""
from __future__ import annotations

import cv2
import numpy as np


def _split_by_enclosed_holes(pixels: np.ndarray, min_child_area: int
                             ) -> tuple[np.ndarray, ...] | None:
    """Assign a joined cluster to two or three independently visible nut holes."""
    height, width = pixels.shape
    area = int(np.count_nonzero(pixels))
    count, _, stats, centers = cv2.connectedComponentsWithStats(
        (~pixels).astype(np.uint8), connectivity=8
    )
    min_hole_area = max(30, int(np.ceil(0.06 * area)))
    holes = [index for index in range(1, count)
             if stats[index, cv2.CC_STAT_AREA] >= min_hole_area
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
    ys, xs = np.nonzero(pixels)
    distances = ((xs[:, None] - hole_centers[None, :, 0]) ** 2
                 + (ys[:, None] - hole_centers[None, :, 1]) ** 2)
    assignments = np.argmin(distances, axis=1)
    children = []
    for index in range(len(holes)):
        child_y = ys[assignments == index]
        child_x = xs[assignments == index]
        if len(child_x) < max(min_child_area, int(0.16 * area)):
            return None
        child_width = int(child_x.max() - child_x.min() + 1)
        child_height = int(child_y.max() - child_y.min() + 1)
        if max(child_width, child_height) > 1.8 * min(child_width, child_height):
            return None
        child = np.zeros_like(pixels)
        child[child_y, child_x] = True
        children.append(child)
    return tuple(children)


def split_touching_component(component: np.ndarray, *, min_child_area: int = 12
                             ) -> tuple[np.ndarray, ...]:
    """Split two or three touching nuts when holes or a narrow neck are visible.

    A silhouette without distinct holes or a neck is ambiguous (for example
    flush rectangles), so it is left intact. Masks preserve the input union.
    """
    pixels = np.asarray(component) != 0
    if pixels.ndim != 2:
        raise ValueError("component must be a 2D mask")
    area = int(np.count_nonzero(pixels))
    if area < 2 * min_child_area:
        return (pixels,)
    hole_pieces = _split_by_enclosed_holes(pixels, min_child_area)
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
    return first, second
