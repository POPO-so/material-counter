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


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(destination: Path) -> dict:
    pair_path = ROOT / "data/results/wo0015-hw-50mhz-20261015/export/pair-manifest.json"
    pair = json.loads(pair_path.read_text(encoding="utf-8"))
    if pair["implementation_status"] != "implemented_offline" or pair["clock_mhz"] != 50:
        raise RuntimeError("Checked 50 MHz build manifest required")
    for name in ("bit", "hwh"):
        if sha(Path(pair[name]["path"])) != pair[name]["sha256"]:
            raise RuntimeError(f"Changed build artifact: {name}")
    xsim_path = ROOT / "data/results/wo0015-offline-20261015/xsim-summary.json"
    xsim = json.loads(xsim_path.read_text(encoding="utf-8"))
    if xsim["status"] != "PASS" or len(xsim["cases"]) != 10:
        raise RuntimeError("Required independent XSim comparisons are incomplete")
    for rel, digest in xsim["rtl_sha256"].items():
        if sha(ROOT / rel) != digest:
            raise RuntimeError(f"Simulation source changed after acceptance: {rel}")
    replay_path = ROOT / "data/results/wo0015-xsim-replay-20261015/replay-summary.json"
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    if replay["suite_status"] != "PASS":
        raise RuntimeError("Final delivered simulation entrypoint did not pass")
    for rel, digest in replay["source_sha256"].items():
        if sha(ROOT / rel) != digest:
            raise RuntimeError(f"Replay source changed after acceptance: {rel}")
    destination.mkdir(parents=True, exist_ok=False)
    for filename in ("board_once.py", "board_preflight.py"):
        shutil.copyfile(Path(__file__).parent / filename, destination / filename)
    shutil.copyfile(Path(pair["bit"]["path"]), destination / "wo15.bit")
    shutil.copyfile(Path(pair["hwh"]["path"]), destination / "wo15.hwh")
    shutil.copyfile(pair_path, destination / "build-pair-manifest.json")
    shutil.copyfile(replay_path, destination / "xsim-replay-summary.json")
    case_root = ROOT / "data/results/wo0015-offline-20261015/vectors/B"
    case = json.loads((case_root / "case.json").read_text(encoding="utf-8"))
    records = [int(line, 16) for line in (case_root / "beats.hex").read_text().splitlines()]
    packed = bytearray()
    for i, word in enumerate(records):
        if (word >> 32) & 15 != 15 or ((word >> 36) & 1) != ((i + 1) % preflight.TX_BEATS == 0):
            raise RuntimeError("Unexpected input record framing/KEEP")
        packed.extend(struct.pack("<I", word & 0xffffffff))
    golden = (case_root / "golden.bin").read_bytes()
    if len(packed) != 3 * preflight.TX_BYTES or len(golden) != 3 * preflight.RX_BYTES:
        raise RuntimeError("Expected three complete simulation frames")
    frames = []
    for i, record in enumerate(case["source_frames"]):
        packet_path = destination / f"frame-{i}.rgb.bin"
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
        "build_id": pair["build_id"], "boot_id": preflight.AUTHORIZED_BOOT_ID,
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
    plan = preflight.validate_manifest(manifest_path, sha(manifest_path))
    # The board controller uses the explicit deployment name while offline
    # validators retain the run-manifest name; bind both identical copies.
    shutil.copyfile(manifest_path, destination / "wo0015-manifest.json")
    (destination / "SHA256SUMS.json").write_text(json.dumps(
        {p.name: sha(p) for p in sorted(destination.iterdir()) if p.is_file()}, indent=2) + "\n", encoding="utf-8")
    archive_path = destination.with_suffix(".zip")
    if archive_path.exists():
        raise FileExistsError(archive_path)
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="new local directory, never a board path")
    args = parser.parse_args()
    print(json.dumps(prepare(args.out.resolve()), indent=2))
