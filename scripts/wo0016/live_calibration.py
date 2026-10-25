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


def frame_geometry(mapping: str = "stretch") -> dict[str, Any]:
    if mapping not in ("stretch", "letterbox", "lighting-calibrated"):
        raise ValueError("frame mapping must be stretch, letterbox or lighting-calibrated")
    geometry = {"mapping": mapping, "source_resolution": [640, 400],
            "output_resolution": [320, 240], "source_roi_xyxy": [0, 0, 640, 400],
            "active_output_roi_xyxy": ([0, 20, 320, 220] if mapping in ("letterbox", "lighting-calibrated")
                                       else [0, 0, 320, 240]),
            "belt_width_mm_nominal": 180.0, "physical_calibration_status": "NOT_MEASURED",
            "pixels_per_mm_y": None}
    if mapping == "lighting-calibrated":
        geometry.update(source_roi_xyxy=[0,20,640,380], active_output_roi_xyxy=[0,30,320,210],
                        binary_thresh=100, threshold_comparison=">", pl_threshold=128,
                        tx_intensity_mapping="saturating +27; PL >=128 equals source >100",
                        source_min_net_contour_area=60, runtime_min_foreground_pixels=15,
                        coordinate_transform="x_out=x_source/2; y_out=y_source/2+20")
    return geometry


def resize_monochrome_into(frame: np.ndarray, output: np.ndarray, *, mapping: str) -> None:
    """Preserve the source view extent; output ownership remains with the caller."""
    if mapping not in ("stretch", "letterbox", "lighting-calibrated"):
        raise ValueError("frame mapping must be stretch, letterbox or lighting-calibrated")
    source = monochrome_view(frame)
    if (output.dtype != np.uint8 or output.shape != (240, 320)
            or not output.flags.c_contiguous or not output.flags.writeable
            or np.shares_memory(source, output)):
        raise ValueError("output must be independent writable contiguous uint8 (240,320)")
    if mapping == "lighting-calibrated":
        from lighting_profile import filter_source_noise
        source = filter_source_noise(source)
    if mapping in ("letterbox", "lighting-calibrated"):
        output[:20].fill(0)
        output[220:].fill(0)
        np.copyto(output[20:220], source[::2, ::2])
        if mapping == "lighting-calibrated":
            output[:30].fill(0)
            output[210:].fill(0)
    else:
        np.copyto(output[0::3], source[0::5, ::2])
        np.copyto(output[1::3], source[1::5, ::2])
        np.copyto(output[2::3], source[3::5, ::2])


class MonochromePreprocessor:
    """Reusable outputs; caller must snapshot them before processing another frame."""

    def __init__(self, *, mapping: str = "stretch"):
        frame_geometry(mapping)
        self.mapping = mapping
        self.gray = np.empty((240, 320), np.uint8)
        self.binary = np.empty_like(self.gray)

    def __call__(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        # Strided copies avoid packing the full camera channel on the Zynq CPU.
        resize_monochrome_into(frame, self.gray, mapping=self.mapping)
        cv2.threshold(self.gray, 100 if self.mapping == "lighting-calibrated" else 128, 255, cv2.THRESH_BINARY, dst=self.binary)
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
