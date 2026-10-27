"""Package the current Gray8 bitstream and Gray8 input frames offline."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import zipfile

import board_preflight as preflight
from prepare_vectors import (NORMAL_CASES, REPO_ROOT, binary8_from_gray,
                             make_packet, read_gray_png, resolve_inputs,
                             validate_source_manifest)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUILD = ROOT / "data/results/gray8-dma-20261027/export"
DEFAULT_XSIM = ROOT / "data/results/gray8-dma-20261027/final/xsim"
RTL_SOURCES = {
    "axis_binary_output_checker.v": ROOT / "fpga/experimental/wo0014/axis_binary_output_checker.v",
    "axis_dma_gray_adapter.v": ROOT / "fpga/experimental/gray8/axis_dma_gray_adapter.v",
    "axis_dma_gray_image_pipeline.v": ROOT / "fpga/experimental/gray8/axis_dma_gray_image_pipeline.v",
    "axis_dma_gray_s2mm_pipeline_bd.v": ROOT / "fpga/experimental/gray8/axis_dma_gray_s2mm_pipeline_bd.v",
    "axis_gray2binary.v": ROOT / "fpga/rtl/axis_gray2binary.v",
    "axis_s2mm_frame_packer.v": ROOT / "fpga/experimental/wo0015/axis_s2mm_frame_packer.v",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(destination: Path, *, build_dir: Path = DEFAULT_BUILD,
            xsim_dir: Path = DEFAULT_XSIM, source_root: Path | None = None,
            source_manifest: Path | None = None) -> dict:
    build_manifest_path = build_dir / "manifest.json"
    build = json.loads(build_manifest_path.read_text(encoding="utf-8"))
    expected = {
        "protocol": "GRAY8_PACKET_V1", "checker_signature": preflight.PACKET_MAGIC,
        "packer_signature": preflight.PACKER_SIGNATURE, "width": preflight.WIDTH,
        "height": preflight.HEIGHT, "tx_bytes": preflight.TX_BYTES,
        "rx_bytes": preflight.RX_BYTES, "threshold": 128,
        "clock_mhz": 50, "part": preflight.EXPECTED_PART,
    }
    for key, value in expected.items():
        if build.get(key) != value:
            raise RuntimeError(f"Gray8 build manifest {key} is {build.get(key)!r}; expected {value!r}")
    if build.get("build_evidence") != "ROUTED_NOT_BOARD_VERIFIED":
        raise RuntimeError("Build evidence status is not the expected routed, not board-verified state")
    for artifact_key, expected_name in (("bit_path", "gray8.bit"), ("hwh_path", "gray8.hwh")):
        path = build_dir / build[artifact_key]
        digest_key = "bit_sha256" if artifact_key == "bit_path" else "hwh_sha256"
        if path.name != expected_name or sha(path) != build[digest_key]:
            raise RuntimeError(f"Gray8 build artifact changed or has an unexpected name: {path}")
    bit_path, hwh_path = build_dir / build["bit_path"], build_dir / build["hwh_path"]
    preflight.validate_hwh(hwh_path)

    rtl_hashes = build.get("rtl_snapshot_sha256")
    if not isinstance(rtl_hashes, dict) or set(Path(name).name for name in rtl_hashes) != set(RTL_SOURCES):
        raise RuntimeError("Gray8 manifest RTL snapshot list is incomplete or unexpected")
    for relative, digest in rtl_hashes.items():
        source = RTL_SOURCES.get(Path(relative).name)
        if source is None or sha(source) != digest:
            raise RuntimeError(f"Gray8 RTL source differs from the routed snapshot: {relative}")

    adapter_log = xsim_dir / "sim.log"
    wrapper_log = xsim_dir / "wrapper-sim.log"
    if "GRAY8_ADAPTER_PASS" not in adapter_log.read_text(encoding="utf-8", errors="replace"):
        raise RuntimeError("Gray8 adapter/pipeline XSim PASS evidence is missing")
    if "GRAY8_WRAPPER_320X240_PASS" not in wrapper_log.read_text(encoding="utf-8", errors="replace"):
        raise RuntimeError("Gray8 320x240 wrapper XSim PASS evidence is missing")
    h3_root, source_manifest_path = resolve_inputs(
        (source_root or REPO_ROOT).resolve(), source_root, source_manifest
    )
    _, records = validate_source_manifest(h3_root, source_manifest_path)
    selected = [records[index] for index in NORMAL_CASES["B"]]

    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(bit_path, destination / "gray8.bit")
    shutil.copyfile(hwh_path, destination / "gray8.hwh")
    shutil.copyfile(build_manifest_path, destination / "gray8-build-manifest.json")
    shutil.copyfile(adapter_log, destination / "gray8-adapter-xsim.log")
    shutil.copyfile(wrapper_log, destination / "gray8-wrapper-xsim.log")
    for filename in ("board_once.py", "board_preflight.py", "camera_adapter.py"):
        shutil.copyfile(Path(__file__).parent / filename, destination / filename)
    codec_dir = destination / "gray8"
    codec_dir.mkdir()
    shutil.copyfile(ROOT / "scripts/gray8/packet_codec.py", codec_dir / "packet_codec.py")

    frames = []
    for frame_id, source in enumerate(selected):
        gray = read_gray_png(source)
        beats = make_packet(gray, preflight.WIDTH, preflight.HEIGHT, frame_id)
        packet = b"".join(struct.pack("<I", beat.data) for beat in beats)
        golden = binary8_from_gray(gray)
        if len(packet) != preflight.TX_BYTES or len(golden) != preflight.RX_BYTES:
            raise RuntimeError("Generated Gray8 packet or Binary8 golden has an invalid length")
        preflight._validate_packet(packet, frame_id)
        packet_path = destination / f"frame-{frame_id}.gray8.bin"
        binary_path = destination / f"frame-{frame_id}.binary.bin"
        packet_path.write_bytes(packet)
        binary_path.write_bytes(golden)
        frames.append({"frame_id": frame_id, "packet_path": packet_path.name,
                       "packet_sha256": sha(packet_path), "binary_path": binary_path.name,
                       "binary_sha256": sha(binary_path)})

    manifest = {
        "schema": 1, "work_order": "WO-0015", "stage": "S2MM_BINARY8_FRAME_RETURN",
        "build_id": destination.name, "boot_id": preflight.AUTHORIZED_BOOT_ID,
        "part": preflight.EXPECTED_PART, "clock_mhz": 50,
        "bit_path": "gray8.bit", "bit_sha256": sha(destination / "gray8.bit"),
        "hwh_path": "gray8.hwh", "hwh_sha256": sha(destination / "gray8.hwh"),
        "dma_base": preflight.DMA_BASE, "checker_base": preflight.CHECKER_BASE,
        "contract": dict(preflight._CONTRACT_VALUES),
        "executor_sha256": {name: sha(destination / name)
                             for name in ("board_once.py", "board_preflight.py")},
        "ready_for_board": True, "frames": frames,
    }
    manifest_path = destination / "run-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    plan = preflight.validate_manifest(manifest_path, sha(manifest_path))
    shutil.copyfile(manifest_path, destination / "wo0015-manifest.json")
    sums = {path.name: sha(path) for path in sorted(destination.iterdir()) if path.is_file()}
    (destination / "SHA256SUMS.json").write_text(json.dumps(sums, indent=2) + "\n", encoding="utf-8")
    archive_path = destination.with_suffix(".zip")
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(destination.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    result = {"status": "OFFLINE_GRAY8_BUNDLE_VALIDATED", "board_accessed": False,
              "ready_for_board_means": "offline Gray8 eligibility only; requires separate human authorization",
              "directory": str(destination), "manifest_sha256": plan.manifest_sha256,
              "archive": str(archive_path), "archive_sha256": sha(archive_path),
              "bit_sha256": plan.bit_sha256, "hwh_sha256": plan.hwh_sha256,
              "board_tested": False}
    (destination.parent / (destination.name + "-receipt.json")).write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="new local directory, never a board path")
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--xsim-dir", type=Path, default=DEFAULT_XSIM)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.out.resolve(), build_dir=args.build_dir.resolve(),
                             xsim_dir=args.xsim_dir.resolve(), source_root=args.source_root,
                             source_manifest=args.source_manifest), indent=2))
