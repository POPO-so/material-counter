"""Camera-angle calibration profile for the measured source ROI and threshold.

The PL threshold stays fixed at 128. The private TX LUT uses offset
128-(T+1), with saturation; default T=100 gives +27. Original Gray8 remains
unchanged for evidence and preview.
"""
from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from typing import Any

import cv2
import numpy as np
from scale_parameters import (DEFAULT_THRESHOLD, PL_THRESHOLD, SOURCE_MIN_NET_AREA,
                              threshold_offset, validate_threshold)

THRESHOLD = DEFAULT_THRESHOLD
SOURCE_ROI = (20, 380)
CAMERA_CONTROLS = {
    "auto_exposure": 1,
    "exposure_time_absolute": 50,
    "brightness": 0,
    "gamma": 100,
}
OPTIONAL_CAMERA_CONTROLS = {"gain": 0}
GAIN_UNVERIFIED_MESSAGE = "UVC 驱动未暴露 gain 控件，未能通过 V4L2 验证"
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
def gray_to_pl_lut(threshold: int = THRESHOLD) -> np.ndarray:
    """Map Gray8 > T to the fixed PL >=128 comparison, including negative offsets."""
    return _gray_to_pl_lut(validate_threshold(threshold))


@lru_cache(maxsize=16)
def _gray_to_pl_lut(threshold: int) -> np.ndarray:
    result = np.clip(np.arange(256, dtype=np.int16) + threshold_offset(threshold),
                     0, 255).astype(np.uint8)
    result.flags.writeable = False
    return result


def classify_area(area: int | None) -> str:
    """Return a provisional size hint; this does not identify material or split blobs."""
    if isinstance(area, bool) or not isinstance(area, (int, np.integer)):
        return "unknown"
    pixels = int(area)
    for label, (minimum, maximum) in AREA_HINTS["ranges"].items():
        if minimum <= pixels <= maximum:
            return label
    return "unknown"


def filter_source_noise(gray640x400: np.ndarray, *, threshold: int = THRESHOLD,
                        min_net_area: int = SOURCE_MIN_NET_AREA) -> np.ndarray:
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
    binary = np.ascontiguousarray(source_roi > validate_threshold(threshold), dtype=np.uint8) * 255
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

        if net_area >= min_net_area:
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


def encode_into(gray320x240: np.ndarray, output: Any, *, frame_id: int = 0,
                threshold: int = THRESHOLD) -> None:
    """Encode a compensated private copy; preserve caller Gray8 for evidence."""
    prepared = prepare_gray(gray320x240)
    pl_input = gray_to_pl_lut(threshold)[prepared]
    gray320x240_to_rgb_packet_into(pl_input, output, frame_id=frame_id)


def encode(gray320x240: np.ndarray, *, frame_id: int = 0,
           threshold: int = THRESHOLD) -> np.ndarray:
    """Allocate and return one TX packet encoded with this lighting profile."""
    threshold = validate_threshold(threshold)
    output = np.empty(TX_BYTES, dtype=np.uint8)
    if threshold == THRESHOLD:
        encode_into(gray320x240, output, frame_id=frame_id)
    else:
        encode_into(gray320x240, output, frame_id=frame_id, threshold=threshold)
    return output


def make_packet_encoders(threshold: int = THRESHOLD):
    """Return ``(encode, encode_into)`` callables with private reusable scratch.

    The pair shares one transformed Gray8 scratch matrix, so callers must invoke
    both callables from a single serialized producer. Each factory call owns a
    separate matrix. ``encode_into`` still writes every TX byte into its
    caller-owned output slot and rejects source/output memory overlap.
    """
    lut = gray_to_pl_lut(threshold)
    scratch = np.empty((HEIGHT, WIDTH), dtype=np.uint8)

    def encode_into(gray320x240: np.ndarray, output: Any, *,
                    frame_id: int = 0) -> None:
        gray = np.asarray(gray320x240)
        if gray.dtype != np.uint8 or gray.shape != (HEIGHT, WIDTH):
            raise ValueError(
                f"expected a uint8 Gray8 matrix with shape {(HEIGHT, WIDTH)}"
            )
        destination = np.asarray(output)
        if np.shares_memory(gray, destination):
            raise ValueError("Gray8 input and TX output must not overlap")

        # Match the legacy prepare-then-LUT pipeline: zeroed rows are LUT-mapped
        # too, so their value is lut[0] (27 at the default threshold).
        scratch.fill(lut[0])
        top, bottom = OUTPUT_ROI
        cv2.LUT(gray[top:bottom, :], lut, dst=scratch[top:bottom, :])
        gray320x240_to_rgb_packet_into(scratch, output, frame_id=frame_id)

    def encode(gray320x240: np.ndarray, *, frame_id: int = 0) -> np.ndarray:
        output = np.empty(TX_BYTES, dtype=np.uint8)
        encode_into(gray320x240, output, frame_id=frame_id)
        return output

    return encode, encode_into


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


def _run_control(device: str, action: str, value: str) -> subprocess.CompletedProcess[str]:
    command = [*_v4l2_prefix(), "--device", device, action, value]
    return subprocess.run(
        command,
        timeout=CAMERA_CONTROL_TIMEOUT_S,
        check=True,
        capture_output=True,
        text=True,
    )


def _error_text(error: subprocess.CalledProcessError) -> str:
    parts = (getattr(error, "stderr", None), getattr(error, "stdout", None), str(error))
    return "\n".join(str(part) for part in parts if part).strip()


def _is_unknown_gain(error: subprocess.CalledProcessError) -> bool:
    return re.search(
        r"\bunknown control\s+(?:'gain'|\"gain\"|gain)(?=\s|$|:)",
        _error_text(error),
        flags=re.IGNORECASE,
    ) is not None


def _verify_required_controls(device: str) -> dict[str, int]:
    result = _run_control(device, "--get-ctrl", ",".join(CAMERA_CONTROLS))
    actual = _read_controls(result.stdout)
    for name, expected in CAMERA_CONTROLS.items():
        if name not in actual:
            raise RuntimeError(f"camera control {name} is missing from V4L2 readback")
        if actual[name] != expected:
            raise RuntimeError(
                f"camera control {name} readback mismatch: expected {expected}, got {actual[name]}"
            )
    return {name: actual[name] for name in CAMERA_CONTROLS}


def _verify_gain(device: str, *, set_response: str) -> dict[str, Any]:
    requested = OPTIONAL_CAMERA_CONTROLS["gain"]
    try:
        result = _run_control(device, "--get-ctrl", "gain")
    except subprocess.CalledProcessError as exc:
        if not _is_unknown_gain(exc):
            raise
        return {
            "requested": requested,
            "value": None,
            "status": "not_exposed",
            "verification": "not_verified",
            "message": GAIN_UNVERIFIED_MESSAGE,
            "driver_response": _error_text(exc),
            "set_response": set_response,
        }

    actual = _read_controls(result.stdout)
    if "gain" not in actual:
        raise RuntimeError("camera control gain is missing from V4L2 readback")
    if actual["gain"] != requested:
        raise RuntimeError(
            f"camera control gain readback mismatch: expected {requested}, got {actual['gain']}"
        )
    if set_response.startswith("unknown control"):
        raise RuntimeError("gain set returned unknown control but readback exposed a value")
    return {
        "requested": requested,
        "value": actual["gain"],
        "status": "verified",
        "verification": "readback",
        "message": None,
        "driver_response": None,
        "set_response": set_response,
    }


def _control_report(device: str, *, gain_set_response: str) -> dict[str, Any]:
    required = _verify_required_controls(device)
    gain = _verify_gain(device, set_response=gain_set_response)
    return {
        "required_controls_verified": True,
        "required_controls": required,
        "gain": gain,
    }


def verify_camera_controls(device: os.PathLike[str] | str) -> dict[str, Any]:
    """Strictly read required controls and report the optional gain status."""
    return _control_report(_device_value(device), gain_set_response="not_attempted")


def apply_camera_controls(device: os.PathLike[str] | str) -> dict[str, Any]:
    """Set required controls, attempt gain=0, and report exact readback status."""
    device_path = _device_value(device)
    settings = ",".join(f"{name}={value}" for name, value in CAMERA_CONTROLS.items())
    _run_control(device_path, "--set-ctrl", settings)

    gain_value = OPTIONAL_CAMERA_CONTROLS["gain"]
    try:
        _run_control(device_path, "--set-ctrl", f"gain={gain_value}")
    except subprocess.CalledProcessError as exc:
        if not _is_unknown_gain(exc):
            raise
        gain_set_response = _error_text(exc)
    else:
        gain_set_response = "applied"
    return _control_report(device_path, gain_set_response=gain_set_response)
