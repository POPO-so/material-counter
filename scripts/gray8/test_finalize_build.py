import importlib.util
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finalize_build import build_manifest, load_manifest

ROOT = Path(__file__).resolve().parents[2]


def fixture_export(tmp_path):
    export = tmp_path / "export"
    export.mkdir()
    old = ROOT / "data/results/wo0015-hw-50mhz-20261015/export/wo15.hwh"
    (export / "gray8.hwh").write_text(old.read_text(encoding="utf-8-sig").replace(
        "axis_dma_s2mm_pipeline_bd", "axis_dma_gray_s2mm_pipeline_bd"), encoding="utf-8")
    (export / "gray8.bit").write_bytes(b"unit-test-not-a-real-bitstream")
    return export


def test_manifest_requires_new_gray8_hwh_and_rejects_tampered_artifact(tmp_path):
    export = fixture_export(tmp_path)
    manifest = build_manifest(tmp_path, ROOT)
    path = export / "manifest.json"
    path.write_text(json.dumps(manifest))
    loaded = load_manifest(path)
    assert loaded["tx_bytes"] == 76832
    assert loaded["checker_signature"] == 0x47525938
    (export / "gray8.bit").write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA"):
        load_manifest(path)


def test_old_rgb_hwh_is_never_admitted_as_gray8(tmp_path):
    export = fixture_export(tmp_path)
    path = export / "gray8.hwh"
    path.write_text(path.read_text(encoding="utf-8").replace("axis_dma_gray_s2mm_pipeline_bd", "axis_dma_s2mm_pipeline_bd"), encoding="utf-8")
    with pytest.raises(ValueError, match="Gray8"):
        build_manifest(tmp_path, ROOT)
