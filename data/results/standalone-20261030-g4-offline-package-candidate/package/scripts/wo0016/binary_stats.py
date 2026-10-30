"""Reusable helpers for validating binary image regions."""

import cv2
import numpy as np


class Binary8RoiValidator:
    """Reject non-Binary8 pixels using a reusable uint8 scratch image."""

    def __init__(self):
        self._scratch = np.empty((0, 0), dtype=np.uint8)

    def validate(self, roi) -> None:
        image = np.asarray(roi)
        if image.dtype != np.uint8 or image.ndim != 2 or not image.size:
            raise ValueError("mask must be a non-empty 2-D Binary8 image")
        if self._scratch.shape != image.shape:
            self._scratch = np.empty(image.shape, dtype=np.uint8)
        cv2.inRange(image, 1, 254, dst=self._scratch)
        if cv2.countNonZero(self._scratch):
            raise ValueError("mask values must be Binary8 0x00 or 0xFF")
