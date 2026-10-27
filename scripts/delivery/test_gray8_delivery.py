import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("gray8_replay_prepare", ROOT / "scripts/delivery/prepare_replay.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def test_replay_package_contains_only_gray8_pipeline_and_detects_changed_source(tmp_path):
    result = prepare.prepare(tmp_path / "package")
    assert result["protocol"] == "GRAY8_PACKET_V1"
    assert "fpga/experimental/gray8/axis_dma_gray_adapter.v" in result["source_sha256"]
    assert not any("rgb2gray" in p or "dma_rgb" in p for p in result["source_sha256"])
    assert (tmp_path / "package/scripts/gray8/run_sim.tcl").is_file()
    with pytest.raises(ValueError, match="empty"):
        prepare.prepare(tmp_path / "package")
