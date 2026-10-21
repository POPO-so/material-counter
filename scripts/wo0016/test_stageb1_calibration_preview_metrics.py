import importlib.util
from pathlib import Path
import unittest



SCRIPT_PATH = Path(__file__).with_name("stageb1_calibration_preview_metrics.py")
SPEC = importlib.util.spec_from_file_location("stageb1_preview_metrics", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


class PreviewFpsTests(unittest.TestCase):
    def test_first_frame_has_no_interval(self):
        self.assertEqual(preview._fps_metrics(10.0, None, None), (None, None))

    def test_interval_and_smoothed_rate(self):
        interval_ms, fps_ema = preview._fps_metrics(10.05, 10.0, None)
        self.assertAlmostEqual(interval_ms, 50.0)
        self.assertAlmostEqual(fps_ema, 20.0)

        interval_ms, fps_ema = preview._fps_metrics(10.15, 10.05, fps_ema)
        self.assertAlmostEqual(interval_ms, 100.0)
        self.assertAlmostEqual(fps_ema, 17.5)

    def test_rejects_non_monotonic_frame_starts(self):
        with self.assertRaisesRegex(ValueError, "strictly increase"):
            preview._fps_metrics(10.0, 10.0, 20.0)


if __name__ == "__main__":
    unittest.main()
