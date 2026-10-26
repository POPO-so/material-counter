"""Scale lighting-profile thresholds from a measured reference area."""
from __future__ import annotations

import math
import numbers


REFERENCE_AREA = 111.0
DEFAULT_THRESHOLD = 100
PL_THRESHOLD = 128
SOURCE_MIN_NET_AREA = 60

_AREA_PARAMETERS = {
    "min_area": 15,
    "min_split_child_area": 48,
    "single_intact_area": 64,
    "split_recursive_item_area": 80,
    "max_area": 540,
    "max_split_area": 540,
    "max_merge_area": 540,
    "split_min_hole_area": 16,
    "source_min_net_area": SOURCE_MIN_NET_AREA,
}

_LENGTH_PARAMETERS = {
    "split_min_hole_distance": 10,
    "max_merged_span": 48,
    "max_bbox_width": 85,
    "max_bbox_height": 85,
    "hysteresis": 2.0,
}


def _reference_ratio(reference_area: float) -> tuple[int, int]:
    """Return the positive real as an exact numerator/denominator pair."""
    if (isinstance(reference_area, bool)
            or not isinstance(reference_area, numbers.Real)):
        raise ValueError("reference_area must be a positive finite real number")
    if isinstance(reference_area, numbers.Rational):
        numerator = int(reference_area.numerator)
        denominator = int(reference_area.denominator)
    else:
        try:
            area = float(reference_area)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError("reference_area must be a positive finite real number") from exc
        if not math.isfinite(area):
            raise ValueError("reference_area must be a positive finite real number")
        numerator, denominator = area.as_integer_ratio()
    if numerator <= 0 or denominator <= 0:
        raise ValueError("reference_area must be a positive finite real number")
    return numerator, denominator


def _round_ratio(numerator: int, denominator: int) -> int:
    """Round a positive rational half-up and keep the value at least one."""
    return max(1, (2 * numerator + denominator) // (2 * denominator))


def _round_scaled_length(base: int, area_num: int, area_den: int) -> int:
    """Round pixel lengths using floor(base * sqrt(A_ref / 111) + 0.5)."""
    scale = math.sqrt(area_num / (area_den * int(REFERENCE_AREA)))
    return max(1, math.floor(base * scale + 0.5))


def derive_lighting_parameters(reference_area: float = REFERENCE_AREA) -> dict[str, int | float]:
    """Scale pixel-area and pixel-length settings from ``REFERENCE_AREA``.

    Area thresholds scale linearly with the reference area. Pixel distances,
    spans, dimensions, and hysteresis scale with its square root. All values
    use positive half-up rounding; hysteresis is returned as a float.
    """
    area_num, area_den = _reference_ratio(reference_area)
    area_denominator = area_den * int(REFERENCE_AREA)
    scaled: dict[str, int | float] = {
        name: _round_ratio(int(value) * area_num, area_denominator)
        for name, value in _AREA_PARAMETERS.items()
    }
    scaled.update({
        name: (float(_round_scaled_length(int(value), area_num, area_den))
               if name == "hysteresis"
               else _round_scaled_length(int(value), area_num, area_den))
        for name, value in _LENGTH_PARAMETERS.items()
    })

    if not (scaled["min_area"] < scaled["min_split_child_area"]
            < scaled["single_intact_area"]
            < scaled["split_recursive_item_area"] < scaled["max_area"]):
        raise ValueError(
            "reference_area is too small to preserve the strict area threshold hierarchy"
        )
    return scaled


def validate_threshold(threshold: int) -> int:
    """Return an integer threshold after validating its Binary8 range."""
    if (isinstance(threshold, bool)
            or not isinstance(threshold, numbers.Integral)
            or not 0 <= threshold <= 255):
        raise ValueError("threshold must be an integer in the range 0..255")
    return int(threshold)


def threshold_offset(threshold: int) -> int:
    """Return the saturating-lookup offset making ``T`` equal PL threshold 128.

    The strict software comparison is ``gray > T``; adding
    ``128 - (T + 1)`` therefore maps its first foreground value to 128.
    Negative offsets are valid for thresholds above 127.
    """
    return PL_THRESHOLD - (validate_threshold(threshold) + 1)
