"""Conservative pixel-domain split for a two-lobed touching Binary8 component."""
from __future__ import annotations

import numpy as np


def split_touching_component(component: np.ndarray, *, min_child_area: int = 12
                             ) -> tuple[np.ndarray, ...]:
    """Return two disjoint pixel masks only when a narrow, solid neck is visible.

    A silhouette without a neck is ambiguous (for example two flush rectangles),
    so it is left intact. Masks have the same shape and their union is the input.
    """
    pixels = np.asarray(component) != 0
    if pixels.ndim != 2:
        raise ValueError("component must be a 2D mask")
    area = int(np.count_nonzero(pixels))
    if area < 2 * min_child_area:
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
            strip = pixels[:, cut - 1 if projection[cut - 1] < projection[cut] else cut] if axis == 0 else pixels[cut - 1 if projection[cut - 1] < projection[cut] else cut, :]
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
    return first, second
