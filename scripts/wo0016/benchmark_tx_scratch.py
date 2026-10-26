"""Offline byte-exact benchmark using the captured B2-03 Gray8 frames."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import statistics
import sys
import time
from pathlib import Path
from types import ModuleType

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parents[1]
WO15_DIR = SCRIPT_DIR.parent / "wo0015"
DEFAULT_DATASET_DIR = (
    PROJECT_DIR / "data" / "results" / "lighting-calibration-20261025"
    / "board-approval" / "b2-attempt-03" / "evidence"
    / "b2-capture-b2-m6touch-220-20261025-03"
)
FRAME_COUNT = 220
WARMUP_REPETITIONS = 1
MEASURE_REPETITIONS = 15
FRAME_SHAPE = (240, 320)
GRAY_BYTES = int(np.prod(FRAME_SHAPE))
TX_BYTES = 16 + GRAY_BYTES * 3 + 16
THRESHOLD = 100


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Python source: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_array(array: np.ndarray) -> str:
    return hashlib.sha256(memoryview(array).cast("B")).hexdigest()


def _load_dataset(dataset_dir: Path) -> tuple[np.ndarray, list[int], dict]:
    dataset_dir = dataset_dir.resolve(strict=True)
    npz_path = dataset_dir / "frames_gray8_binary8.npz"
    records_path = dataset_dir / "frame-records.jsonl"
    if not npz_path.is_file() or not records_path.is_file():
        raise FileNotFoundError(
            "dataset directory must contain frames_gray8_binary8.npz and frame-records.jsonl"
        )

    npz_sha256 = _sha256_file(npz_path)
    sidecar = npz_path.with_suffix(npz_path.suffix + ".sha256")
    if sidecar.is_file():
        expected_npz_sha256 = sidecar.read_text(encoding="ascii").split()[0]
        if expected_npz_sha256.lower() != npz_sha256:
            raise ValueError("NPZ SHA-256 does not match its sidecar")

    with np.load(npz_path, allow_pickle=False) as archive:
        if "raw_gray8" not in archive.files:
            raise ValueError("dataset NPZ is missing raw_gray8")
        frames = archive["raw_gray8"]
    if frames.dtype != np.uint8 or frames.shape != (FRAME_COUNT, *FRAME_SHAPE):
        raise ValueError(
            f"raw_gray8 must be uint8 with shape {(FRAME_COUNT, *FRAME_SHAPE)}"
        )

    records_bytes = records_path.read_bytes()
    records = [
        json.loads(line)
        for line in records_bytes.decode("utf-8").splitlines()
        if line.strip()
    ]
    if len(records) != FRAME_COUNT:
        raise ValueError(f"expected {FRAME_COUNT} frame records, found {len(records)}")
    frame_ids = [record.get("frame_id") for record in records]
    if frame_ids != list(range(FRAME_COUNT)):
        raise ValueError("frame-records IDs must be exactly 0 through 219 in order")

    gray_record_hashes = []
    for index, (frame, record) in enumerate(zip(frames, records)):
        expected_hash = record.get("gray_sha256")
        actual_hash = _sha256_array(frame)
        if not expected_hash or expected_hash.lower() != actual_hash:
            raise ValueError(f"raw_gray8 frame {index} does not match its frame record")
        gray_record_hashes.append(actual_hash)
        if record.get("threshold", THRESHOLD) != THRESHOLD:
            raise ValueError(f"frame {index} was captured with a non-default threshold")

    raw_gray8_sha256 = _sha256_array(frames)
    return frames, frame_ids, {
        "dataset_kind": "captured B2-03 raw_gray8",
        "npz_path": str(npz_path),
        "npz_sha256": npz_sha256,
        "frame_records_path": str(records_path),
        "frame_records_sha256": hashlib.sha256(records_bytes).hexdigest(),
        "frame_count": len(records),
        "frame_ids_first": frame_ids[0],
        "frame_ids_last": frame_ids[-1],
        "raw_gray8_sha256": raw_gray8_sha256,
        "per_frame_gray_hashes_verified": len(gray_record_hashes),
    }


def _load_encoder_pair(snapshot_dir: Path):
    snapshot_dir = snapshot_dir.resolve(strict=True)
    current_scale = SCRIPT_DIR / "scale_parameters.py"
    before_scale = snapshot_dir / "scale_parameters.py"
    if _sha256_file(current_scale) != _sha256_file(before_scale):
        raise RuntimeError("current and snapshot scale_parameters.py differ")

    if str(WO15_DIR) not in sys.path:
        sys.path.insert(0, str(WO15_DIR))
    old_scale = _load_module(before_scale, "scale_parameters")
    old_packet = _load_module(snapshot_dir / "packet_encoder.py", "packet_encoder")
    old_profile = _load_module(
        snapshot_dir / "lighting_profile.py", "lighting_profile_before"
    )
    current_packet = _load_module(SCRIPT_DIR / "packet_encoder.py", "packet_encoder")
    current_profile = _load_module(
        SCRIPT_DIR / "lighting_profile.py", "lighting_profile_current"
    )
    if current_profile.validate_threshold is not old_scale.validate_threshold:
        raise RuntimeError("lighting profile did not retain the verified scale module")
    return old_profile, old_packet, current_profile, current_packet


def _frame_stats(samples: list[float]) -> dict[str, float]:
    return {
        "p50": round(float(np.percentile(samples, 50)), 4),
        "p95": round(float(np.percentile(samples, 95)), 4),
        "mean": round(statistics.mean(samples), 4),
    }


def _call_encoder(packet_module, encode_into, gray, output, frame_id):
    # lighting_profile deliberately resolves the existing packet writer lazily.
    sys.modules["packet_encoder"] = packet_module
    encode_into(gray, output, frame_id=frame_id)


def benchmark(snapshot_dir: Path, dataset_dir: Path) -> dict:
    frames, frame_ids, dataset_info = _load_dataset(dataset_dir)
    before_profile, before_packet, current_profile, current_packet = _load_encoder_pair(
        snapshot_dir
    )
    before_encode_into = before_profile.encode_into
    _current_encode, current_encode_into = current_profile.make_packet_encoders(
        threshold=THRESHOLD
    )

    before_slots = [np.empty(TX_BYTES, dtype=np.uint8) for _ in range(2)]
    current_slots = [np.empty(TX_BYTES, dtype=np.uint8) for _ in range(2)]
    if (np.shares_memory(before_slots[0], before_slots[1])
            or np.shares_memory(current_slots[0], current_slots[1])):
        raise AssertionError("TX slots must own independent storage")

    input_hash_before = _sha256_array(frames)
    before_digest = hashlib.sha256()
    current_digest = hashlib.sha256()
    for index, (gray, frame_id) in enumerate(zip(frames, frame_ids)):
        slot_index = index % 2
        before_slot = before_slots[slot_index]
        current_slot = current_slots[slot_index]
        _call_encoder(before_packet, before_encode_into, gray, before_slot, frame_id)
        _call_encoder(current_packet, current_encode_into, gray, current_slot, frame_id)
        before_bytes = before_slot.tobytes()
        current_bytes = current_slot.tobytes()
        if before_bytes != current_bytes:
            raise AssertionError(f"TX packet bytes differ for frame_id={frame_id}")
        before_digest.update(before_bytes)
        current_digest.update(current_bytes)
    if before_digest.digest() != current_digest.digest():
        raise AssertionError("aggregate packet digest differs after per-frame equality checks")

    # One full-data warmup pass; both implementations reuse two alternating slots.
    for index, (gray, frame_id) in enumerate(zip(frames, frame_ids)):
        slot_index = index % 2
        _call_encoder(
            before_packet, before_encode_into, gray, before_slots[slot_index], frame_id
        )
        _call_encoder(
            current_packet, current_encode_into, gray, current_slots[slot_index], frame_id
        )

    before_samples: list[float] = []
    current_samples: list[float] = []
    for repetition in range(MEASURE_REPETITIONS):
        # Alternate paired call order each pass to reduce fixed A/B order bias.
        order = ("before", "current") if repetition % 2 == 0 else ("current", "before")
        for index, (gray, frame_id) in enumerate(zip(frames, frame_ids)):
            slot_index = index % 2
            for encoder_name in order:
                if encoder_name == "before":
                    packet_module, encode_into, slot, samples = (
                        before_packet, before_encode_into, before_slots[slot_index], before_samples
                    )
                else:
                    packet_module, encode_into, slot, samples = (
                        current_packet, current_encode_into,
                        current_slots[slot_index], current_samples,
                    )
                sys.modules["packet_encoder"] = packet_module
                started = time.perf_counter()
                encode_into(gray, slot, frame_id=frame_id)
                samples.append((time.perf_counter() - started) * 1000.0)

    input_hash_after = _sha256_array(frames)
    if input_hash_after != input_hash_before:
        raise AssertionError("raw_gray8 input was modified by TX encoding")
    if input_hash_after != dataset_info["raw_gray8_sha256"]:
        raise AssertionError("raw_gray8 input hash changed after dataset validation")

    before_sources = {
        name: _sha256_file(snapshot_dir / name)
        for name in ("lighting_profile.py", "packet_encoder.py", "scale_parameters.py")
    }
    current_sources = {
        name: _sha256_file(SCRIPT_DIR / name)
        for name in ("lighting_profile.py", "packet_encoder.py", "scale_parameters.py")
    }
    return {
        "mode": "offline-captured-b2-tx-encoder-microbenchmark",
        "board_accessed": False,
        "physical_accuracy_claim": False,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "opencv": current_profile.cv2.__version__,
        "geometry": list(FRAME_SHAPE),
        "threshold": THRESHOLD,
        "dataset": dataset_info,
        "packet_bytes": TX_BYTES,
        "exact_byte_match": {
            "per_frame_assertions": FRAME_COUNT,
            "match": True,
            "snapshot_packets_aggregate_sha256": before_digest.hexdigest(),
            "current_packets_aggregate_sha256": current_digest.hexdigest(),
        },
        "input_integrity": {
            "before_sha256": input_hash_before,
            "after_sha256": input_hash_after,
            "unchanged": True,
        },
        "warmup_full_frame_passes": WARMUP_REPETITIONS,
        "measurement_repetitions": MEASURE_REPETITIONS,
        "measurement_calls_per_encoder": FRAME_COUNT * MEASURE_REPETITIONS,
        "paired_order": "before/current then current/before, alternating each repetition",
        "latency_ms_per_packet": {
            "snapshot_encode_into": _frame_stats(before_samples),
            "current_factory_encode_into": _frame_stats(current_samples),
        },
        "source_sha256": {"snapshot": before_sources, "current": current_sources},
        "transform_image_allocations_per_packet": {
            "snapshot_encode_into": {
                "transient_arrays": 2,
                "transient_bytes": 2 * GRAY_BYTES,
                "basis": "prepare_gray copy plus LUT advanced-index result",
            },
            "current_factory_encode_into": {
                "transient_arrays": 0,
                "transient_bytes": 0,
                "persistent_scratch_bytes_per_factory": GRAY_BYTES,
                "basis": "ROI LUT writes into reusable factory-owned scratch",
            },
            "preallocated_tx_slots_per_encoder": 2,
            "tx_output_bytes_per_slot": TX_BYTES,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", type=Path, required=True,
        help="saved pre-change scripts/wo0016 source directory",
    )
    parser.add_argument(
        "--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR,
        help="B2-03 directory containing NPZ and frame-records.jsonl",
    )
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error(f"refusing to overwrite existing report: {args.report}")

    report = benchmark(args.snapshot, args.dataset_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.report.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    except FileExistsError:
        parser.error(f"refusing to overwrite existing report: {args.report}")
    print(json.dumps({"report_path": str(args.report), **report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
