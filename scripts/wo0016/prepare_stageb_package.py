"""Prepare a hash-pinned WO-0016 Stage B board package without board access."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
WO15_SOURCE = ROOT / "scripts" / "wo0015"
if str(WO15_SOURCE) not in sys.path:
    sys.path.insert(0, str(WO15_SOURCE))

import board_preflight as preflight
import run_stream_counter as stageb


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_exclusive(path: Path, payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _prepare_wo15_bundle(destination: Path) -> dict:
    pair_path = ROOT / "data/results/wo0015-hw-50mhz-20261015/export/pair-manifest.json"
    pair = json.loads(pair_path.read_text(encoding="utf-8"))
    if pair.get("implementation_status") != "implemented_offline" or pair.get("clock_mhz") != 50:
        raise RuntimeError("a checked WO-0015 50 MHz implementation manifest is required")
    destination.mkdir(parents=True, exist_ok=False)
    for key, name, expected in (
        ("bit", "wo15.bit", "a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420"),
        ("hwh", "wo15.hwh", "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6"),
    ):
        source = Path(pair[key]["path"])
        if _sha(source) != expected or pair[key].get("sha256") != expected:
            raise RuntimeError(f"authorized WO-0015 {key.upper()} SHA-256 mismatch")
        shutil.copyfile(source, destination / name)
    shutil.copyfile(pair_path, destination / "build-pair-manifest.json")
    shutil.copyfile(WO15_SOURCE / "board_once.py", destination / "board_once.py")
    shutil.copyfile(WO15_SOURCE / "board_preflight.py", destination / "board_preflight.py")

    case_root = ROOT / "data/results/wo0015-xsim-final-20261015/vectors/B"
    case = json.loads((case_root / "case.json").read_text(encoding="utf-8"))
    if case.get("expected_frames") != 3 or len(case.get("source_frames", [])) != 3:
        raise RuntimeError("the pinned WO-0015 Case B three-frame vector is required")
    words = [int(line, 16) for line in (case_root / "beats.hex").read_text().splitlines()]
    if len(words) != 3 * preflight.TX_BEATS:
        raise RuntimeError("WO-0015 Case B does not contain exactly three complete frames")
    packed = bytearray()
    for index, word in enumerate(words):
        if ((word >> 32) & 0xF) != 0xF or ((word >> 36) & 1) != int((index + 1) % preflight.TX_BEATS == 0):
            raise RuntimeError("WO-0015 Case B input framing/KEEP differs from its contract")
        packed.extend(struct.pack("<I", word & 0xFFFFFFFF))
    golden = (case_root / "golden.bin").read_bytes()
    if len(packed) != 3 * preflight.TX_BYTES or len(golden) != 3 * preflight.RX_BYTES:
        raise RuntimeError("WO-0015 Case B vector byte lengths differ from contract")

    frames = []
    for index, record in enumerate(case["source_frames"]):
        packet_path = destination / f"frame-{index}.rgb.bin"
        binary_path = destination / f"frame-{index}.binary.bin"
        packet_path.write_bytes(packed[index * preflight.TX_BYTES:(index + 1) * preflight.TX_BYTES])
        binary_path.write_bytes(golden[index * preflight.RX_BYTES:(index + 1) * preflight.RX_BYTES])
        if _sha(binary_path) != record["golden_sha256"]:
            raise RuntimeError(f"WO-0015 frame {index} golden hash differs")
        frames.append({
            "frame_id": record["frame_id"], "packet_path": packet_path.name,
            "packet_sha256": _sha(packet_path), "binary_path": binary_path.name,
            "binary_sha256": _sha(binary_path),
        })

    manifest = {
        "schema": 1,
        "work_order": "WO-0015",
        "stage": "S2MM_BINARY8_FRAME_RETURN",
        "build_id": pair["build_id"],
        "boot_id": preflight.AUTHORIZED_BOOT_ID,
        "part": preflight.EXPECTED_PART,
        "clock_mhz": 50,
        "bit_path": "wo15.bit",
        "bit_sha256": _sha(destination / "wo15.bit"),
        "hwh_path": "wo15.hwh",
        "hwh_sha256": _sha(destination / "wo15.hwh"),
        "dma_base": preflight.DMA_BASE,
        "checker_base": preflight.CHECKER_BASE,
        "contract": dict(preflight._CONTRACT_VALUES),
        "executor_sha256": {
            name: _sha(destination / name) for name in ("board_once.py", "board_preflight.py")
        },
        "ready_for_board": True,
        "frames": frames,
    }
    for name in ("run-manifest.json", "wo0015-manifest.json"):
        _write_exclusive(destination / name, manifest)
    plan = preflight.validate_manifest(destination / "wo0015-manifest.json",
                                       _sha(destination / "wo0015-manifest.json"))
    _write_exclusive(destination / "SHA256SUMS.json", {
        path.name: _sha(path) for path in sorted(destination.iterdir()) if path.is_file()
    })
    return {
        "status": "WO0015_PACKAGE_VALIDATED_OFFLINE",
        "manifest_sha256": plan.manifest_sha256,
        "bit_sha256": plan.bit_sha256,
        "hwh_sha256": plan.hwh_sha256,
        "frames": len(plan.frames),
        "board_accessed": False,
        "input_vectors": "WO-0015 XSim Case B, three-frame packet/golden outputs",
        "historical_replay_logs": "not present in checkout/archive; not used by the strict manifest validator",
    }


def prepare(destination: Path) -> dict:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    wo15_dir = destination / "wo0015"
    wo15_summary = _prepare_wo15_bundle(wo15_dir)

    for name in ("run_stream_counter.py", "stream_counter.py", "packet_encoder.py",
                 "mjpeg_preview.py", "stageb_analysis.py", "camera_preview_server.py"):
        shutil.copyfile(stageb.SCRIPT_DIR / name, destination / name)
    for name in ("pynq_device_probe.py", "phase16_board_entry.sh"):
        shutil.copyfile(stageb.SCRIPT_DIR / name, destination / name)
    shutil.copyfile(WO15_SOURCE / "camera_adapter.py", wo15_dir / "camera_adapter.py")
    shutil.copyfile(WO15_SOURCE / "prepare_vectors.py", wo15_dir / "prepare_vectors.py")

    wo15_manifest = wo15_dir / "wo0015-manifest.json"
    plan = preflight.validate_manifest(wo15_manifest, _sha(wo15_manifest))
    runtime_manifest = {
        "schema": 1,
        "work_order": "WO-0016",
        "stage": "calibration-batch-preview",
        "boot_id": plan.boot_id,
        "wo15_manifest_sha256": plan.manifest_sha256,
        "tier2_dma_addresses": {
            "mm2s": stageb.TIER2_MM2S_ADDRESS,
            "s2mm": stageb.TIER2_S2MM_ADDRESS,
        },
        "executor_sha256": {
            name: _sha(stageb.STAGEB_EXECUTOR_PATHS[name])
            for name in stageb.STAGEB_EXECUTOR_FILES
        },
    }
    stageb_manifest_path = destination / "stageb-runtime-manifest.json"
    _write_exclusive(stageb_manifest_path, runtime_manifest)
    stageb_plan = stageb.validate_stageb_manifest(
        stageb_manifest_path, _sha(stageb_manifest_path),
        wo15_manifest_sha256=plan.manifest_sha256, boot_id=plan.boot_id,
    )

    files = {
        path.relative_to(destination).as_posix(): _sha(path)
        for path in sorted(destination.rglob("*")) if path.is_file()
    }
    summary = {
        "schema": 1,
        "work_order": "WO-0016",
        "boot_id": plan.boot_id,
        "wo15_manifest_sha256": plan.manifest_sha256,
        "stageb_manifest_sha256": stageb_plan["manifest_sha256"],
        "bit_sha256": plan.bit_sha256,
        "hwh_sha256": plan.hwh_sha256,
        "files": files,
        "board_accessed": False,
        "ready_for_board_means": "offline package validation only; hardware stages remain individually gated",
    }
    _write_exclusive(destination / "package-manifest.json", summary)
    archive_path = destination.with_suffix(".zip")
    if archive_path.exists():
        raise FileExistsError(archive_path)
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(destination).as_posix())
    return {
        "status": "STAGEB_BOARD_PACKAGE_READY",
        "board_accessed": False,
        "directory": str(destination),
        "archive": str(archive_path),
        "archive_sha256": _sha(archive_path),
        "package_manifest_sha256": _sha(destination / "package-manifest.json"),
        "wo15_manifest_sha256": plan.manifest_sha256,
        "stageb_manifest_sha256": stageb_plan["manifest_sha256"],
        "bit_sha256": plan.bit_sha256,
        "hwh_sha256": plan.hwh_sha256,
        "wo15_bundle": wo15_summary,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True,
                        help="new local package directory; existing output is never replaced")
    args = parser.parse_args()
    print(json.dumps(prepare(args.out), ensure_ascii=False, indent=2, sort_keys=True))
