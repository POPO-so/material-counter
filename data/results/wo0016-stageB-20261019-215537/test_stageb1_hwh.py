from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from stageb1_hwh import read_threshold


REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_HWH = REPO_ROOT / "data/results/wo0015-hw-50mhz-20261015/export/wo15.hwh"
EXPECTED_HWH_SHA256 = "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6"


class StageB1HwhTests(unittest.TestCase):
    def test_reads_threshold_from_reviewed_real_hwh(self) -> None:
        digest = hashlib.sha256(REAL_HWH.read_bytes()).hexdigest()
        self.assertEqual(digest, EXPECTED_HWH_SHA256)
        self.assertEqual(read_threshold(str(REAL_HWH)), 128)

    def test_rejects_missing_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.hwh"
            path.write_text("<ROOT/>", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_threshold(str(path))

    def test_rejects_duplicate_threshold_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.hwh"
            path.write_text(
                '<ROOT><PARAMETER NAME="THRESHOLD" VALUE="128"/>'
                '<PARAMETER NAME="THRESHOLD" VALUE="128"/></ROOT>',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                read_threshold(str(path))

    def test_rejects_non_integer_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.hwh"
            path.write_text(
                '<ROOT><PARAMETER NAME="THRESHOLD" VALUE="high"/></ROOT>',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                read_threshold(str(path))


if __name__ == "__main__":
    unittest.main()
