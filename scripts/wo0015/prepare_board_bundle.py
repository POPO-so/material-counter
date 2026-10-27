"""Package checked offline artifacts for review. Never connects to a board."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import zipfile

import board_preflight as preflight

ROOT = Path(__file__).resolve().parents[2]
BASELINE_ROOT = ROOT / "data/results/wo0015-gray8-baseline-20261027"
PAIR_MANIFEST_PATH = BASELINE_ROOT / "export/pair-manifest.json"
XSIM_VECTOR_ROOT = BASELINE_ROOT / "xsim-final/vectors"
XSIM_RESULT_ROOT = BASELINE_ROOT / "xsim-final/xsim"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(destination: Path, *, replace_existing: bool = False,
            boot_id: str | None = None) -> dict:
    pair_path = PAIR_MANIFEST_PATH
    approved_boot_id = (preflight.AUTHORIZED_BOOT_ID if boot_id is None else
                        preflight._require_boot_id(boot_id, "approved boot_id"))
    pair = json.loads(pair_path.read_text(encoding="utf-8"))
    if pair["implementation_status"] != "implemented_offline" or pair["clock_mhz"] != 50:
        raise RuntimeError("Checked 50 MHz build manifest required")

    def pair_file(name: str) -> Path:
        value = Path(pair[name]["path"])
        return (value if value.is_absolute() else ROOT / value).resolve()

    for name in ("bit", "hwh"):
        if sha(pair_file(name)) != pair[name]["sha256"]:
            raise RuntimeError(f"Changed build artifact: {name}")

    xsim_case_names = ("A", "B", "C-seed1", "C-seed7", "C-seed42",
                       "C-seed20261015", "D_short", "D_long", "D_checksum", "D_reset")
    for case_name in xsim_case_names:
        result_path = XSIM_RESULT_ROOT / f"{case_name}-result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        expected_status = "PASS" if not case_name.startswith("D_") else (
            "PASS_RESET_STOP" if case_name == "D_reset" else "PASS_FAULT_STOP"
        )
        if result["status"] != expected_status or result["mismatch_bytes"] != 0:
            raise RuntimeError(f"Gray8 XSim case did not pass: {case_name}")

    destination = destination.resolve()
    if destination.exists():
        if not replace_existing:
            raise FileExistsError(destination)
        expected_destination = (BASELINE_ROOT / "board-package").resolve()
        if destination != expected_destination:
            raise RuntimeError("--replace-existing is restricted to the canonical Gray8 board package")
        existing_pair = destination / "build-pair-manifest.json"
        if not existing_pair.is_file() or sha(existing_pair) != sha(pair_path):
            raise RuntimeError("Existing package is not the current Gray8 baseline package")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.mkdir(parents=False, exist_ok=False)
    for filename in ("board_once.py", "board_preflight.py"):
        shutil.copyfile(Path(__file__).parent / filename, destination / filename)
    shutil.copyfile(pair_file("bit"), destination / "wo15.bit")
    shutil.copyfile(pair_file("hwh"), destination / "wo15.hwh")
    shutil.copyfile(pair_path, destination / "build-pair-manifest.json")
    for case_name in xsim_case_names:
        shutil.copyfile(XSIM_RESULT_ROOT / f"{case_name}-result.json",
                        destination / f"xsim-{case_name}-result.json")
    case_root = XSIM_VECTOR_ROOT / "B"
    case = json.loads((case_root / "case.json").read_text(encoding="utf-8"))
    records = [int(line, 16) for line in (case_root / "beats.hex").read_text().splitlines()]
    packed = bytearray()
    for i, word in enumerate(records):
        if (word >> 32) & 15 != 15 or ((word >> 36) & 1) != ((i + 1) % preflight.TX_BEATS == 0):
            raise RuntimeError("Unexpected input record framing/KEEP")
        packed.extend(struct.pack("<I", word & 0xffffffff))
    golden = (case_root / "golden.bin").read_bytes()
    if (case["expected_frames"] != 3 or len(case["source_frames"]) != 3
            or len(packed) != 3 * preflight.TX_BYTES
            or len(golden) != 3 * preflight.RX_BYTES
            or sha(case_root / "golden.bin") != case["golden_sha256"]):
        raise RuntimeError("Expected three complete simulation frames")
    frames = []
    for i, record in enumerate(case["source_frames"]):
        packet_path = destination / f"frame-{i}.gray8.bin"
        binary_path = destination / f"frame-{i}.binary.bin"
        packet_path.write_bytes(packed[i * preflight.TX_BYTES:(i + 1) * preflight.TX_BYTES])
        binary_path.write_bytes(golden[i * preflight.RX_BYTES:(i + 1) * preflight.RX_BYTES])
        if sha(binary_path) != record["golden_sha256"]:
            raise RuntimeError("Per-frame simulation golden hash differs")
        frames.append({"frame_id": record["frame_id"], "packet_path": packet_path.name,
                       "packet_sha256": sha(packet_path), "binary_path": binary_path.name,
                       "binary_sha256": sha(binary_path)})
    manifest = {
        "schema": 1, "work_order": "WO-0015", "stage": "S2MM_BINARY8_FRAME_RETURN",
        "build_id": pair["build_id"], "boot_id": approved_boot_id,
        "part": preflight.EXPECTED_PART, "clock_mhz": 50,
        "bit_path": "wo15.bit", "bit_sha256": sha(destination / "wo15.bit"),
        "hwh_path": "wo15.hwh", "hwh_sha256": sha(destination / "wo15.hwh"),
        "dma_base": preflight.DMA_BASE, "checker_base": preflight.CHECKER_BASE,
        "contract": dict(preflight._CONTRACT_VALUES),
        "executor_sha256": {n: sha(destination / n) for n in ("board_once.py", "board_preflight.py")},
        "ready_for_board": True, "frames": frames,
    }
    manifest_path = destination / "run-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    plan = preflight.validate_manifest(
        manifest_path, sha(manifest_path), approved_boot_id=approved_boot_id
    )
    # The board controller uses the explicit deployment name while offline
    # validators retain the run-manifest name; bind both identical copies.
    shutil.copyfile(manifest_path, destination / "wo0015-manifest.json")
    (destination / "SHA256SUMS.json").write_text(json.dumps(
        {p.name: sha(p) for p in sorted(destination.iterdir()) if p.is_file()}, indent=2) + "\n", encoding="utf-8")
    archive_path = destination.with_suffix(".zip")
    if archive_path.exists() and not replace_existing:
        raise FileExistsError(archive_path)
    archive_mode = "w" if replace_existing else "x"
    with zipfile.ZipFile(archive_path, archive_mode, compression=zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(destination.iterdir()):
            archive.write(p, p.name)
    result = {"status": "OFFLINE_BUNDLE_VALIDATED", "board_accessed": False,
              "ready_for_board_means": "offline eligibility only; requires separate human authorization",
              "directory": str(destination), "manifest_sha256": plan.manifest_sha256,
              "archive": str(archive_path), "archive_sha256": sha(archive_path),
              "bit_sha256": plan.bit_sha256, "hwh_sha256": plan.hwh_sha256}
    (destination.parent / (destination.name + "-receipt.json")).write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="new local directory, never a board path")
    parser.add_argument("--replace-existing", action="store_true",
                        help="refresh only the canonical Gray8 board package after verifying its build manifest")
    parser.add_argument("--boot-id",
                        help="explicit current board Boot ID to bind into both manifests")
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    print(json.dumps(prepare(args.out.resolve(), replace_existing=args.replace_existing,
                             boot_id=args.boot_id), indent=2))
