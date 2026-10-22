"""Camera-only monochrome preprocessing and optical calibration diagnostics."""
from __future__ import annotations

import threading
import subprocess
import os
import re
from typing import Any

import cv2
import numpy as np


def require_standard_camera_controls(device: str) -> None:
    """Independent read-only formal gate, including after a killed preview process."""
    command = ["v4l2-ctl", "-d", str(device),
               "--get-ctrl=auto_exposure,exposure_time_absolute,brightness"]
    if getattr(os, "geteuid", lambda: -1)() != 0:
        command = ["sudo", "-n"] + command
    output = subprocess.run(command, check=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=5).stdout
    required = {"auto_exposure": 1, "exposure_time_absolute": 50, "brightness": 0}
    for name, value in required.items():
        if re.search(r"^\s*" + name + r":\s*" + str(value) + r"(?:\s|$)", output, re.MULTILINE) is None:
            raise RuntimeError("standard camera controls required before formal sampling: " + output)


def monochrome_view(frame: np.ndarray) -> np.ndarray:
    """OV9281 decoded channels repeat monochrome samples: take a view, never BGR luma."""
    image = np.asarray(frame)
    if image.dtype != np.uint8:
        raise ValueError("camera frame must be uint8")
    if image.shape in ((400, 640, 3), (400, 640, 1)):
        return image[:, :, 0]
    if image.shape == (400, 640):
        return image
    raise ValueError("camera frame must have shape (400,640), (400,640,1) or (400,640,3)")


class MonochromePreprocessor:
    """Reusable outputs; caller must snapshot them before processing another frame."""

    def __init__(self):
        self.gray = np.empty((240, 320), np.uint8)
        self.binary = np.empty_like(self.gray)

    def __call__(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        gray_full = monochrome_view(frame)
        # Exact INTER_NEAREST map for 640x400 -> 320x240: x=2*j,
        # y=floor(5*i/3). Three strided copies avoid OpenCV first packing
        # the entire 640x400 channel view on the Zynq CPU.
        np.copyto(self.gray[0::3], gray_full[0::5, ::2])
        np.copyto(self.gray[1::3], gray_full[1::5, ::2])
        np.copyto(self.gray[2::3], gray_full[3::5, ::2])
        cv2.threshold(self.gray, 128, 255, cv2.THRESH_BINARY, dst=self.binary)
        return self.gray, self.binary


def optical_metrics(gray: np.ndarray, result: Any, counter: Any, *, fps: float) -> dict:
    """ROI metrics; BG Mean estimates background from pixels <=128, excluding bright objects.

    Over-128 includes nuts as well as glare. Glare area counts upper-bound-rejected
    connected components; it is an optical diagnostic, not physical identity proof.
    """
    roi = gray[result.roi_top:result.roi_bottom, :]
    bright = roi > 128
    background = roi[~bright]
    bg_mean = float(background.mean()) if background.size else None
    glare_area = counter.glare_area_px
    status = ("GLARE >128: ADJUST LIGHT/EXPOSURE" if glare_area else
              "OPTICS READY (TH=128 OK)" if bg_mean is not None and 20 <= bg_mean <= 45 else
              "ADJUST BACKGROUND: TARGET BG 20-45")
    return {
        "roi_bounds_xyxy": [0, result.roi_top, gray.shape[1], result.roi_bottom],
        "bg_mean": bg_mean, "bg_mean_method": "ROI pixels <=128 (bright objects excluded)",
        "roi_mean": float(roi.mean()), "over_128_ratio": float(np.count_nonzero(bright) / roi.size),
        "over_128_area_px": int(np.count_nonzero(bright)),
        "glare_area_px": glare_area, "glare_rejected_count": counter.glare_rejected_count,
        "glare_contours": [np.asarray(c).reshape(-1, 2).tolist() for c in counter.rejected_contours],
        "candidate_nut_blobs": len(result.detections),
        "candidate_areas_px": [d.area for d in result.detections],
        "fps": float(fps), "fps_kind": "rolling 60-frame camera/processing loop", "status": status,
    }


class CalibrationControls:
    """Explicit preview-only allowlist. Driver limits verified on the OV9281 board."""

    LIMITS = {"exposure_time_absolute": (50, 10000), "brightness": (-64, 64)}
    STANDARD = {"exposure_time_absolute": 50, "brightness": 0}

    def __init__(self, *, enabled: bool, apply):
        self.enabled = enabled
        self.apply = apply
        self.values = dict(self.STANDARD)
        self._lock = threading.Lock()

    def update(self, payload: dict) -> dict:
        if not self.enabled:
            raise PermissionError("controls require --calibration-mode; formal sampling stays locked")
        if not isinstance(payload, dict) or not payload:
            raise ValueError("nonempty JSON object required")
        if set(payload) == {"reset"} and payload["reset"] is True:
            changes = dict(self.STANDARD)
        else:
            changes = dict(payload)
            for name, value in changes.items():
                if name not in self.LIMITS:
                    raise ValueError("only exposure_time_absolute and brightness may be changed")
                low, high = self.LIMITS[name]
                if type(value) is not int or not low <= value <= high:
                    raise ValueError(f"{name} must be an integer in {low}..{high}")
        with self._lock:
            if not self.enabled:
                raise PermissionError("calibration controls closed")
            expected = dict(self.values, **changes)
            readback = self.apply(expected)
            if readback != expected:
                raise RuntimeError("camera control readback differs from requested values")
            self.values = expected
            return dict(self.values)

    def close_and_restore(self) -> None:
        with self._lock:
            if not self.enabled:
                return
            self.enabled = False
            values = self.apply(dict(self.STANDARD))
            if values != self.STANDARD:
                raise RuntimeError("standard profile restoration readback failed")
            self.values = dict(values)
