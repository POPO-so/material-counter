import unittest

import cv2
import numpy as np

from camera_format_benchmark import compare_results, parse_modes, raw_to_gray


class ParseModesTests(unittest.TestCase):
    def test_keeps_each_discrete_interval_with_its_own_size_and_format(self):
        listing = """
ioctl: VIDIOC_ENUM_FMT
    [0]: 'MJPG' (Motion-JPEG, compressed)
        Size: Discrete 640x400
            Interval: Discrete 0.016s (60.000 fps)
            Interval: Discrete 0.005s (200.000 fps)
        Size: Discrete 1280x720
            Interval: Discrete 0.008s (120.000 fps)
    [1]: 'YUYV' (YUYV 4:2:2)
        Size: Discrete 640x400
            Interval: Discrete 0.033s (30.000 fps)
"""

        self.assertEqual(
            parse_modes(listing),
            [
                {"fourcc": "MJPG", "width": 640, "height": 400, "fps": 60.0},
                {"fourcc": "MJPG", "width": 640, "height": 400, "fps": 200.0},
                {"fourcc": "MJPG", "width": 1280, "height": 720, "fps": 120.0},
                {"fourcc": "YUYV", "width": 640, "height": 400, "fps": 30.0},
            ],
        )

    def test_does_not_invent_fps_for_stepwise_interval(self):
        listing = """
    [0]: 'YUYV' (YUYV 4:2:2)
        Size: Discrete 640x400
            Interval: Stepwise 0.016s - 0.100s with step 0.001s
"""
        self.assertEqual(parse_modes(listing), [])

    def test_stepwise_size_does_not_reuse_previous_discrete_size(self):
        listing = """
    [0]: 'YUYV' (YUYV 4:2:2)
        Size: Discrete 640x400
            Interval: Discrete 0.016s (60.000 fps)
        Size: Stepwise 640x400 - 1280x720 with step 16x16
            Interval: Discrete 0.033s (30.000 fps)
"""
        self.assertEqual(
            parse_modes(listing),
            [{"fourcc": "YUYV", "width": 640, "height": 400, "fps": 60.0}],
        )


class RawToGrayTests(unittest.TestCase):
    def test_extracts_luma_from_yuyv_and_uyvy(self):
        yuyv = bytes([10, 101, 20, 151, 30, 111, 40, 161])
        uyvy = bytes([101, 10, 151, 20, 111, 30, 161, 40])
        expected = np.array([[10, 20], [30, 40]], dtype=np.uint8)

        np.testing.assert_array_equal(raw_to_gray(yuyv, "YUYV", 2, 2), expected)
        np.testing.assert_array_equal(raw_to_gray(yuyv, "YUY2", 2, 2), expected)
        np.testing.assert_array_equal(raw_to_gray(uyvy, "UYVY", 2, 2), expected)
        np.testing.assert_array_equal(raw_to_gray(bytes([1, 2, 3, 4]), "GREY", 2, 2),
                                      [[1, 2], [3, 4]])

    def test_decodes_mjpeg_as_grayscale(self):
        source = np.arange(100, dtype=np.uint8).reshape(10, 10)
        ok, encoded = cv2.imencode(".jpg", source)
        self.assertTrue(ok)

        gray = raw_to_gray(encoded.tobytes(), "MJPG", 10, 10)
        self.assertEqual(gray.shape, source.shape)
        self.assertEqual(gray.dtype, np.uint8)
        self.assertLessEqual(np.abs(gray.astype(int) - source).max(), 5)

    def test_rejects_color_frames_and_incorrect_raw_payload_length(self):
        color = np.zeros((2, 2, 3), dtype=np.uint8)
        with self.assertRaises(ValueError):
            raw_to_gray(color, "YUYV", 2, 2)
        with self.assertRaises(ValueError):
            raw_to_gray(b"\x00" * 7, "YUYV", 2, 2)
        with self.assertRaises(ValueError):
            raw_to_gray(b"\x00" * 3, "GREY", 2, 2)


class CompareResultsTests(unittest.TestCase):
    @staticmethod
    def result(fourcc, *, negotiated=None, status="ok", fps=20.0, cpu_ms=10.0,
               requested_fps=60.0, controls=None):
        requested = {"fourcc": fourcc, "width": 640, "height": 400,
                     "fps": requested_fps}
        return {
            "status": status,
            "requested": requested,
            "negotiated": negotiated or dict(requested),
            "pipeline_fps": fps,
            "cpu_ms_per_frame": cpu_ms,
            "controls_before": controls,
        }

    def test_reports_comparable_speed_and_cpu_changes(self):
        compared = compare_results(
            self.result("MJPG", fps=20.0, cpu_ms=10.0),
            self.result("YUYV", fps=25.0, cpu_ms=7.5),
        )

        self.assertEqual(compared["comparability"], "matched")
        self.assertAlmostEqual(compared["fps_gain_percent"], 25.0)
        self.assertAlmostEqual(compared["cpu_reduction_percent"], 25.0)

    def test_does_not_claim_improvement_for_unmatched_or_failed_runs(self):
        wrong_negotiation = self.result(
            "YUYV", negotiated={"fourcc": "MJPG", "width": 640,
                                "height": 400, "fps": 60.0}, fps=100.0
        )
        failed = self.result("YUYV", status="error", fps=100.0)
        for candidate in (wrong_negotiation, failed):
            with self.subTest(candidate=candidate["status"]):
                compared = compare_results(self.result("MJPG"), candidate)
                self.assertEqual(compared["comparability"], "unavailable")
                self.assertIsNone(compared["fps_gain_percent"])
                self.assertIsNone(compared["cpu_reduction_percent"])

    def test_zero_baseline_does_not_divide_by_zero(self):
        compared = compare_results(
            self.result("MJPG", fps=0.0, cpu_ms=0.0),
            self.result("YUYV", fps=20.0, cpu_ms=5.0),
        )
        self.assertEqual(compared["comparability"], "unavailable")
        self.assertIsNone(compared["fps_gain_percent"])
        self.assertIsNone(compared["cpu_reduction_percent"])

    def test_does_not_match_different_controls_or_requested_frame_rates(self):
        baseline = self.result("MJPG", controls={"exposure": 50})
        candidates = (
            self.result("YUYV", controls={"exposure": 60}),
            self.result("YUYV", requested_fps=30.0),
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate["requested"],
                              controls=candidate["controls_before"]):
                compared = compare_results(baseline, candidate)
                self.assertEqual(compared["comparability"], "unavailable")
                self.assertIsNone(compared["fps_gain_percent"])
                self.assertIsNone(compared["cpu_reduction_percent"])


if __name__ == "__main__":
    unittest.main()
