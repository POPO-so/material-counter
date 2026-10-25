"""Camera-angle calibration profile for the measured source ROI and threshold.

The PL threshold is fixed at 128. Saturating +27 on a private TX image maps
source Gray8 values greater than 100 to PL values at least 128, while the
captured/prepared Gray8 image remains available unchanged as evidence.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any

import cv2
import numpy as np

THRESHOLD = 100
SOURCE_ROI = (20, 380)
SOURCE_MIN_NET_AREA = 60
CAMERA_CONTROLS = {
    "auto_exposure": 1,
    "exposure_time_absolute": 50,
    "brightness": 0,
    "gamma": 100,
    "gain": 0,
}
AREA_HINTS = {
    "ranges": {
        "small": (60, 95),
        "medium": (96, 130),
        "large": (131, 188),
    },
    "material_labels": {
        "small": "M6平垫圈",
        "medium": "M6螺母",
        "large": "M8螺母",
    },
    "provisional": True,
    "units": "output_foreground_pixels",
}

WIDTH = 320
HEIGHT = 240
OUTPUT_ROI = (30, 210)
CAMERA_CONTROL_TIMEOUT_S = 5
TX_BYTES = 16 + WIDTH * HEIGHT * 3 + 16
_GRAY_TO_PL_LUT = np.minimum(
    np.arange(256, dtype=np.uint16) + 27, 255
).astype(np.uint8)


def classify_area(area: int | None) -> str:
    """Return a provisional size hint; this does not identify material or split blobs."""
    if isinstance(area, bool) or not isinstance(area, (int, np.integer)):
        return "unknown"
    pixels = int(area)
    for label, (minimum, maximum) in AREA_HINTS["ranges"].items():
        if minimum <= pixels <= maximum:
            return label
    return "unknown"


def filter_source_noise(gray640x400: np.ndarray) -> np.ndarray:
    """Clear sub-60-net-contour foreground and rows outside the calibrated source ROI.

    Net area follows the lighting tuner convention: each external contour area
    minus the summed areas of its direct child hole contours. Only foreground
    pixels belonging to rejected contours are cleared; lower-gray background is
    retained inside the ROI.
    """
    gray = np.asarray(gray640x400)
    if gray.dtype != np.uint8 or gray.shape != (400, 640):
        raise ValueError("expected a uint8 Gray8 matrix with shape (400, 640)")

    filtered = gray.copy()
    top, bottom = SOURCE_ROI
    filtered[:top].fill(0)
    filtered[bottom:].fill(0)

    source_roi = gray[top:bottom, :]
    binary = np.ascontiguousarray(source_roi > THRESHOLD, dtype=np.uint8) * 255
    contours, hierarchy = cv2.findContours(
        binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )
    if hierarchy is None:
        return filtered

    tree = hierarchy[0]
    filtered_roi = filtered[top:bottom, :]
    for index, contour in enumerate(contours):
        if tree[index, 3] != -1:
            continue

        net_area = float(cv2.contourArea(contour))
        child_index = int(tree[index, 2])
        while child_index != -1:
            net_area -= float(cv2.contourArea(contours[child_index]))
            child_index = int(tree[child_index, 0])

        if net_area >= SOURCE_MIN_NET_AREA:
            continue

        component_mask = np.zeros(binary.shape, dtype=np.uint8)
        cv2.drawContours(component_mask, contours, index, 255, thickness=cv2.FILLED)
        rejected_foreground = (component_mask != 0) & (binary != 0)
        filtered_roi[rejected_foreground] = 0

    return filtered


def classify_material_area(area: int | None) -> str | None:
    """Return the operator-confirmed material label for a provisional area band."""
    size = classify_area(area)
    return AREA_HINTS["material_labels"].get(size)


def prepare_gray(gray320x240: np.ndarray) -> np.ndarray:
    """Return a copy with source ROI [20,380) mapped to output rows [30,210)."""
    gray = np.asarray(gray320x240)
    if gray.dtype != np.uint8 or gray.shape != (HEIGHT, WIDTH):
        raise ValueError(f"expected a uint8 Gray8 matrix with shape {(HEIGHT, WIDTH)}")
    prepared = gray.copy()
    top, bottom = OUTPUT_ROI
    prepared[:top].fill(0)
    prepared[bottom:].fill(0)
    return prepared


def gray320x240_to_rgb_packet_into(gray: np.ndarray, output: Any, *,
                                   frame_id: int = 0) -> None:
    """Load the existing packet writer only when the lighting profile is used."""
    try:
        import packet_encoder
    except ImportError:  # pragma: no cover - package-style import support
        from . import packet_encoder
    packet_encoder.gray320x240_to_rgb_packet_into(gray, output, frame_id=frame_id)


def encode_into(gray320x240: np.ndarray, output: Any, *, frame_id: int = 0) -> None:
    """Encode a compensated private copy; preserve caller Gray8 for evidence."""
    prepared = prepare_gray(gray320x240)
    pl_input = _GRAY_TO_PL_LUT[prepared]
    gray320x240_to_rgb_packet_into(pl_input, output, frame_id=frame_id)


def encode(gray320x240: np.ndarray, *, frame_id: int = 0) -> np.ndarray:
    """Allocate and return one TX packet encoded with this lighting profile."""
    output = np.empty(TX_BYTES, dtype=np.uint8)
    encode_into(gray320x240, output, frame_id=frame_id)
    return output


def _v4l2_prefix() -> list[str]:
    geteuid = getattr(os, "geteuid", lambda: 0)
    if geteuid() == 0:
        return ["v4l2-ctl"]
    return ["sudo", "-n", "v4l2-ctl"]


def _device_value(device: os.PathLike[str] | str) -> str:
    value = os.fspath(device)
    if not isinstance(value, str) or not value:
        raise ValueError("camera device must be a non-empty path")
    return value


def _read_controls(stdout: str | None) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in (stdout or "").splitlines():
        name, separator, raw_value = line.partition(":")
        if not separator:
            continue
        token = raw_value.strip().split(maxsplit=1)[0] if raw_value.strip() else ""
        try:
            values[name.strip()] = int(token, 0)
        except ValueError:
            continue
    return values


def verify_camera_controls(device: os.PathLike[str] | str) -> dict[str, int]:
    """Read all calibrated controls and fail unless every value matches exactly."""
    device_path = _device_value(device)
    command = [*_v4l2_prefix(), "--device", device_path,
               "--get-ctrl", ",".join(CAMERA_CONTROLS)]
    result = subprocess.run(
        command,
        timeout=CAMERA_CONTROL_TIMEOUT_S,
        check=True,
        capture_output=True,
        text=True,
    )
    actual = _read_controls(result.stdout)
    for name, expected in CAMERA_CONTROLS.items():
        if name not in actual:
            raise RuntimeError(f"camera control {name} is missing from V4L2 readback")
        if actual[name] != expected:
            raise RuntimeError(
                f"camera control {name} readback mismatch: expected {expected}, got {actual[name]}"
            )
    return {name: actual[name] for name in CAMERA_CONTROLS}


def apply_camera_controls(device: os.PathLike[str] | str) -> dict[str, int]:
    """Set calibrated controls, then confirm them with a fresh V4L2 readback."""
    device_path = _device_value(device)
    settings = ",".join(f"{name}={value}" for name, value in CAMERA_CONTROLS.items())
    command = [*_v4l2_prefix(), "--device", device_path, "--set-ctrl", settings]
    subprocess.run(
        command,
        timeout=CAMERA_CONTROL_TIMEOUT_S,
        check=True,
        capture_output=True,
        text=True,
    )
    return verify_camera_controls(device_path)
