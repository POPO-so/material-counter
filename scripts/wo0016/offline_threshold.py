"""CPU Binary8 reference for offline comparison; board execution keeps the PL path."""
from __future__ import annotations

import cv2
import numpy as np

from lighting_profile import HEIGHT, WIDTH, OUTPUT_ROI
from scale_parameters import DEFAULT_THRESHOLD, validate_threshold


def software_binary8(gray: np.ndarray, *, threshold: int = DEFAULT_THRESHOLD,
                     output: np.ndarray | None = None,
                     roi: tuple[int, int] = OUTPUT_ROI,
                     inclusive: bool = False) -> np.ndarray:
    """Threshold prepared Gray8 in the active ROI into caller-owned cached RAM.

    The original image is never modified. Pixels outside the active ROI are
    background. ``inclusive=False`` keeps the historical OpenCV comparison
    (``cv2.THRESH_BINARY``: pixel > threshold), which is **not** the PL rule;
    ``inclusive=True`` uses pixel >= threshold to match the fixed PL >=128
    comparison. Software binary8 is never equivalent to a PL measurement and
    must not be reported as board evidence.
    """
    image = np.asarray(gray)
    if image.dtype != np.uint8 or image.shape != (HEIGHT, WIDTH):
        raise ValueError("gray must be uint8 (240, 320)")
    threshold = validate_threshold(threshold)
    top, bottom = roi
    if (isinstance(top, bool) or isinstance(bottom, bool)
            or not isinstance(top, (int, np.integer))
            or not isinstance(bottom, (int, np.integer))
            or not 0 <= top < bottom <= HEIGHT):
        raise ValueError("roi must contain valid integer row bounds")
    if output is None:
        output = np.empty((HEIGHT, WIDTH), np.uint8)
    if (not isinstance(output, np.ndarray) or output.dtype != np.uint8
            or output.shape != image.shape or not output.flags.c_contiguous
            or not output.flags.writeable):
        raise ValueError("output must be writable contiguous uint8 (240, 320)")
    if np.shares_memory(image, output):
        raise ValueError("gray and output must not overlap")
    output.fill(0)
    if inclusive:
        output[top:bottom] = np.where(image[top:bottom] >= threshold, 255, 0)
    else:
        cv2.threshold(image[top:bottom], threshold, 255, cv2.THRESH_BINARY,
                      dst=output[top:bottom])
    return output
