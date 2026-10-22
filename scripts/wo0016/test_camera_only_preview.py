import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from camera_only_preview import profile_matches, software_gray_binary  # noqa: E402


def test_camera_preview_generates_same_threshold_128_binary_pip():
    for value, expected in ((127, 0), (128, 0), (129, 255)):
        camera_bgr = np.full((400, 640, 3), value, dtype=np.uint8)

        gray, mask = software_gray_binary(camera_bgr)

        assert gray.shape == (240, 320)
        assert gray.dtype == np.uint8
        assert mask.shape == (240, 320)
        assert mask.dtype == np.uint8
        assert np.all(mask == expected)


def test_camera_preview_requires_exact_live_manual_v4l2_profile():
    profile = """Width/Height      : 640/400
Pixel Format      : 'MJPG'
Frames per second: 60.000 (60/1)
brightness: 0
auto_exposure: 1 (Manual Mode)
exposure_time_absolute: 50
"""

    assert profile_matches(profile)
    assert not profile_matches(profile.replace("brightness: 0", "brightness: 1"))
    assert not profile_matches(profile.replace("exposure_time_absolute: 50",
                                               "exposure_time_absolute: 51"))
