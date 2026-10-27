"""Verify current Gray8 artifacts and source correspondence; never touch hardware.

Historical reports remain under data/results but are not current executables
or current source-correspondence claims.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check(path, expected, expected_bytes=None):
    file = ROOT / str(path).replace("\\", "/")
    require(file.is_file(), "Missing: " + str(path))
    raw = file.read_bytes()
    if expected_bytes is not None:
        require(len(raw) == expected_bytes, "Byte count mismatch: " + str(path))
    require(hashlib.sha256(raw).hexdigest() == expected, "SHA-256 mismatch: " + str(path))


def verify_current(manifest_path):
    spec = importlib.util.spec_from_file_location("gray8_artifact_admission", ROOT / "scripts/gray8/finalize_build.py")
    admission = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(admission)
    path = Path(manifest_path).resolve()
    manifest = admission.load_manifest(path)
    sources = {
        "axis_gray2binary.v": "fpga/rtl/axis_gray2binary.v",
        "axis_binary_output_checker.v": "fpga/experimental/wo0014/axis_binary_output_checker.v",
        "axis_s2mm_frame_packer.v": "fpga/experimental/wo0015/axis_s2mm_frame_packer.v",
        "axis_dma_gray_adapter.v": "fpga/experimental/gray8/axis_dma_gray_adapter.v",
        "axis_dma_gray_image_pipeline.v": "fpga/experimental/gray8/axis_dma_gray_image_pipeline.v",
        "axis_dma_gray_s2mm_pipeline_bd.v": "fpga/experimental/gray8/axis_dma_gray_s2mm_pipeline_bd.v",
    }
    for snapshot, digest in manifest["rtl_snapshot_sha256"].items():
        check(sources[Path(snapshot).name], digest)
    require(len(manifest["rtl_snapshot_sha256"]) == len(sources), "Incomplete Gray8 RTL snapshot")
    return dict(status="PASS", protocol=manifest["protocol"],
                manifest_sha256=admission.sha256(path), rtl_sources=len(sources),
                hardware_actions=0, board_verified=False,
                scope="Current Gray8 artifact hashes, HWH topology and RTL source correspondence")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=ROOT / "data/results/gray8-dma-20261027/export/manifest.json")
    args = parser.parse_args()
    print(json.dumps(verify_current(args.manifest), indent=2))
