"""Conservative pixel-domain split for a two-lobed touching Binary8 component."""
from __future__ import annotations

import numpy as np
import cv2


def split_touching_component(component: np.ndarray, *, min_child_area: int = 140,
                             min_split_area: int = 280
                             ) -> tuple[np.ndarray, ...]:
    """Return two disjoint pixel masks only when a narrow, solid neck is visible.

    Candidates require at least 280 foreground pixels and an elongated bounding
    box. Near-square silhouettes and pairs without a neck remain intact. Each
    child must meet min_child_area and be connected. Their union is the input.
    """
    pixels = np.asarray(component) != 0
    if pixels.ndim != 2:
        raise ValueError("component must be a 2D mask")
    area = int(np.count_nonzero(pixels))
    if (isinstance(min_split_area, bool) or not isinstance(min_split_area, int)
            or min_split_area < 280):
        raise ValueError("min_split_area must be an integer >= 280")
    if (isinstance(min_child_area, bool) or not isinstance(min_child_area, int)
            or min_child_area < 1):
        raise ValueError("min_child_area must be a positive integer")
    if area < max(min_split_area, 2 * min_child_area):
        return (pixels,)
    # A pair should have spatially distinct lobes. Near-square silhouettes are
    # ambiguous, including ordinary large nuts with uneven thresholded edges.
    if max(pixels.shape) < 1.35 * min(pixels.shape):
        return (pixels,)
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
            if not 0 < neck <= 0.55 * min(left_peak, right_peak):
                continue
            neck_index = cut - 1 if projection[cut - 1] < projection[cut] else cut
            strip = pixels[:, neck_index] if axis == 0 else pixels[neck_index, :]
            # A central hole has two foreground runs; it is not a contact neck.
            if np.count_nonzero(strip & ~np.r_[False, strip[:-1]]) != 1:
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
    # A cut must create two individual connected children, not two arbitrary
    # collections of fragments that happen to lie on either side of a line.
    if any(cv2.connectedComponents(piece.astype(np.uint8), connectivity=8)[0] != 2
           for piece in (first, second)):
        return (pixels,)
    return first, second
