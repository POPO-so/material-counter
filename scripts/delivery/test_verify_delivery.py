import importlib.util
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_delivery

ROOT = verify_delivery.ROOT
MANIFEST = ROOT / "data/results/gray8-dma-20261027/export/manifest.json"


class HistoricalGray8SnapshotTests(unittest.TestCase):
    def test_real_historical_manifest_accepts_eol_only_source_differences(self):
        result = verify_delivery.verify_current(MANIFEST)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["rtl_sources"], 6)
        self.assertEqual(result["rtl_exact_files"], 3)
        self.assertEqual(result["rtl_eol_normalized_files"], 3)
        self.assertEqual(len(result["rtl_exact_range"]), 3)
        self.assertEqual(len(result["rtl_eol_normalized_range"]), 3)

    def _fixture(self, temp):
        base = Path(temp)
        manifest_path = base / "export" / "manifest.json"
        manifest_path.parent.mkdir(parents=True)
        snapshot = base / "rtl" / "sample.v"
        snapshot.parent.mkdir()
        source = base / "current" / "sample.v"
        source.parent.mkdir()
        original = b"module sample;\r\nendmodule\r\n"
        snapshot.write_bytes(original)
        source.write_bytes(b"module sample;\nendmodule\n")
        manifest = {"rtl_snapshot_sha256": {"rtl/sample.v": hashlib.sha256(original).hexdigest()}}
        return manifest, manifest_path, {"sample.v": "current/sample.v"}, base, snapshot, source

    def test_real_content_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest, manifest_path, sources, base, _snapshot, source = self._fixture(temp)
            source.write_bytes(b"module changed;\nendmodule\n")
            with self.assertRaisesRegex(RuntimeError, "RTL content mismatch"):
                verify_delivery.verify_rtl_sources(manifest, manifest_path, sources, base)

    def test_tampered_historical_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest, manifest_path, sources, base, snapshot, _source = self._fixture(temp)
            snapshot.write_bytes(b"module tampered;\r\nendmodule\r\n")
            with self.assertRaisesRegex(RuntimeError, "Historical RTL snapshot SHA-256 mismatch"):
                verify_delivery.verify_rtl_sources(manifest, manifest_path, sources, base)


def test_check_enforces_declared_byte_count(tmp_path, monkeypatch):
    payload = b"intact record\n"
    (tmp_path / "record.txt").write_bytes(payload)
    monkeypatch.setattr(verify_delivery, "ROOT", tmp_path)

    digest = hashlib.sha256(payload).hexdigest()
    verify_delivery.check("record.txt", digest, expected_bytes=len(payload))
    with pytest.raises(RuntimeError, match="Byte count mismatch"):
        verify_delivery.check("record.txt", digest, expected_bytes=len(payload) - 1)


if __name__ == "__main__":
    unittest.main()
