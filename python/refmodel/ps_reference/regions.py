"""Offline Gray8 preprocessing and 8-connected region features for WO-0010."""

from dataclasses import dataclass

import cv2
import numpy as np


_MODES = (0, 1, 2)
_FOREGROUND = 0xFF
_BACKGROUND = 0x00
_FLAG_MERGE_SUSPECT = 1
_FLAG_TOUCHES_FRAME_EDGE = 2
_FLAG_TWO_DIMENSIONAL = 4


@dataclass(frozen=True)
class Region:
    """Inclusive region bounds and basic OpenCV connected-component features."""

    x_min: int
    x_max: int
    row_min: int
    row_max: int
    area: int
    centroid_x: float
    centroid_y: float
    peak: int = 0
    flags: int = _FLAG_TWO_DIMENSIONAL

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        """Return inclusive bounds as ``(x_min, x_max, row_min, row_max)``."""

        return self.x_min, self.x_max, self.row_min, self.row_max


def _require_byte_integer(value: int, name: str) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    result = int(value)
    if not 0 <= result <= 255:
        raise ValueError(f"{name} must be in the range 0..255")
    return result


def _require_gray8(gray: np.ndarray) -> np.ndarray:
    if not isinstance(gray, np.ndarray) or gray.ndim != 2 or gray.dtype != np.uint8:
        raise ValueError("gray must be a two-dimensional uint8 NumPy array")
    if gray.shape[0] == 0 or gray.shape[1] == 0:
        raise ValueError("gray dimensions must be non-zero")
    return gray


def _require_nonnegative_integer(value: int, name: str) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be a non-negative integer")
    result = int(value)
    if result < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return result


def preprocess(gray: np.ndarray, threshold: int = 128, morph_mode: int = 0) -> np.ndarray:
    """Threshold Gray8 to Binary8 and optionally apply a zero-padded 3x3 filter.

    ``morph_mode`` is 0 for bypass, 1 for erosion, or 2 for dilation. Pixels
    outside the image are background (00), including for erosion.
    """

    gray = _require_gray8(gray)
    threshold = _require_byte_integer(threshold, "threshold")
    if isinstance(morph_mode, (bool, np.bool_)) or not isinstance(morph_mode, (int, np.integer)):
        raise ValueError("morph_mode must be 0 (bypass), 1 (erosion), or 2 (dilation)")
    morph_mode = int(morph_mode)
    if morph_mode not in _MODES:
        raise ValueError("morph_mode must be 0 (bypass), 1 (erosion), or 2 (dilation)")

    binary = np.where(gray >= threshold, _FOREGROUND, _BACKGROUND).astype(np.uint8)
    if morph_mode == 0:
        return binary

    kernel = np.ones((3, 3), dtype=np.uint8)
    if morph_mode == 1:
        return cv2.erode(
            binary,
            kernel,
            iterations=1,
            borderType=cv2.BORDER_CONSTANT,
            borderValue=_BACKGROUND,
        )
    return cv2.dilate(
        binary,
        kernel,
        iterations=1,
        borderType=cv2.BORDER_CONSTANT,
        borderValue=_BACKGROUND,
    )


def extract_regions(
    gray: np.ndarray,
    threshold: int = 128,
    min_area: int = 16,
    morph_mode: int = 0,
    max_width: int | None = 24,
) -> list[Region]:
    """Return filtered 8-connected foreground regions, sorted by centroid.

    A nonzero ``max_width`` marks wider components as merge-suspect. Pass
    ``0`` or ``None`` to disable that candidate flag. Components touching any
    image edge receive the frame-edge flag. Areas remain full Python integers.
    """

    min_area = _require_nonnegative_integer(min_area, "min_area")
    if max_width is not None:
        max_width = _require_nonnegative_integer(max_width, "max_width")

    binary = preprocess(gray, threshold=threshold, morph_mode=morph_mode)
    height, width = binary.shape
    component_count, _, stats, centroids = cv2.connectedComponentsWithStats(
        binary,
        connectivity=8,
        ltype=cv2.CV_32S,
    )

    regions = []
    for label in range(1, component_count):
        x_min, row_min, box_width, box_height, area = (int(value) for value in stats[label])
        if area < min_area:
            continue

        x_max = x_min + box_width - 1
        row_max = row_min + box_height - 1
        flags = _FLAG_TWO_DIMENSIONAL
        if max_width not in (0, None) and box_width > max_width:
            flags |= _FLAG_MERGE_SUSPECT
        if x_min == 0 or row_min == 0 or x_max == width - 1 or row_max == height - 1:
            flags |= _FLAG_TOUCHES_FRAME_EDGE

        regions.append(Region(
            x_min=x_min,
            x_max=x_max,
            row_min=row_min,
            row_max=row_max,
            area=area,
            centroid_x=float(centroids[label, 0]),
            centroid_y=float(centroids[label, 1]),
            peak=0,
            flags=flags,
        ))

    regions.sort(key=lambda region: (region.centroid_x, region.centroid_y, region.bbox))
    return regions
