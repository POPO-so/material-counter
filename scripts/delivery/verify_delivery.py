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


def verify_rtl_sources(manifest, manifest_path, sources, root=ROOT):
    """Check immutable historical snapshots and current source correspondence."""
    snapshot_root = Path(manifest_path).resolve().parent.parent
    exact, normalized = [], []
    for snapshot, digest in manifest["rtl_snapshot_sha256"].items():
        name = Path(snapshot).name
        require(name in sources, "Unexpected Gray8 RTL snapshot: " + str(snapshot))
        snapshot_file = (snapshot_root / snapshot).resolve()
        require(snapshot_file.is_file(), "Missing historical RTL snapshot: " + str(snapshot))
        snapshot_bytes = snapshot_file.read_bytes()
        require(hashlib.sha256(snapshot_bytes).hexdigest() == digest,
                "Historical RTL snapshot SHA-256 mismatch: " + str(snapshot))

        current_file = Path(root) / sources[name]
        require(current_file.is_file(), "Missing: " + sources[name])
        current_bytes = current_file.read_bytes()
        if current_bytes == snapshot_bytes:
            exact.append(sources[name])
        elif current_bytes.replace(b"\r\n", b"\n") == snapshot_bytes.replace(b"\r\n", b"\n"):
            normalized.append(sources[name])
        else:
            raise RuntimeError("RTL content mismatch: " + sources[name])
    require(len(manifest["rtl_snapshot_sha256"]) == len(sources), "Incomplete Gray8 RTL snapshot")
    return exact, normalized


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
    exact, normalized = verify_rtl_sources(manifest, path, sources)
    return dict(status="PASS", protocol=manifest["protocol"],
                manifest_sha256=admission.sha256(path), rtl_sources=len(sources),
                rtl_exact_files=len(exact), rtl_exact_range=exact,
                rtl_eol_normalized_files=len(normalized), rtl_eol_normalized_range=normalized,
                hardware_actions=0, board_verified=False,
                scope="Current Gray8 artifact hashes, HWH topology and RTL source correspondence; historical snapshot bytes are SHA-256 verified, current RTL comparison allows CRLF-to-LF equivalence")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=ROOT / "data/results/gray8-dma-20261027/export/manifest.json")
    args = parser.parse_args()
    print(json.dumps(verify_current(args.manifest), indent=2))
