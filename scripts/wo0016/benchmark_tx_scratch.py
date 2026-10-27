"""Offline Gray8 lighting TX benchmark over the captured B2-03 frames."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
import time
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parents[1]
GRAY8_DIR = SCRIPT_DIR.parent / "gray8"
if str(GRAY8_DIR) not in sys.path:
    sys.path.insert(0, str(GRAY8_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lighting_profile import make_packet_encoders
from packet_encoder import TX_BYTES
from packet_codec import HEIGHT, WIDTH, validate_packet

DEFAULT_DATASET_DIR = (
    PROJECT_DIR / "data" / "results" / "lighting-calibration-20261025"
    / "board-approval" / "b2-attempt-03" / "evidence"
    / "b2-capture-b2-m6touch-220-20261025-03"
)
FRAME_COUNT = 220
MEASURE_REPETITIONS = 15
FRAME_SHAPE = (HEIGHT, WIDTH)
GRAY_BYTES = int(np.prod(FRAME_SHAPE))
THRESHOLD = 100


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
        raise ValueError(f"raw_gray8 must be uint8 with shape {(FRAME_COUNT, *FRAME_SHAPE)}")
    records_bytes = records_path.read_bytes()
    records = [json.loads(line) for line in records_bytes.decode("utf-8").splitlines()
               if line.strip()]
    if len(records) != FRAME_COUNT:
        raise ValueError(f"expected {FRAME_COUNT} frame records, found {len(records)}")
    frame_ids = [record.get("frame_id") for record in records]
    if frame_ids != list(range(FRAME_COUNT)):
        raise ValueError("frame-records IDs must be exactly 0 through 219 in order")
    for index, (frame, record) in enumerate(zip(frames, records)):
        actual_hash = _sha256_array(frame)
        expected_hash = record.get("gray_sha256")
        if not expected_hash or expected_hash.lower() != actual_hash:
            raise ValueError(f"raw_gray8 frame {index} does not match its frame record")
        if record.get("threshold", THRESHOLD) != THRESHOLD:
            raise ValueError(f"frame {index} was captured with a non-default threshold")
    return frames, frame_ids, {
        "dataset_kind": "captured B2-03 raw_gray8",
        "npz_path": str(npz_path),
        "npz_sha256": npz_sha256,
        "frame_records_path": str(records_path),
        "frame_records_sha256": hashlib.sha256(records_bytes).hexdigest(),
        "frame_count": len(records),
        "frame_ids_first": frame_ids[0],
        "frame_ids_last": frame_ids[-1],
        "raw_gray8_sha256": _sha256_array(frames),
        "per_frame_gray_hashes_verified": len(records),
    }


def _stats(samples: list[float]) -> dict[str, float]:
    return {"p50": round(float(np.percentile(samples, 50)), 4),
            "p95": round(float(np.percentile(samples, 95)), 4),
            "mean": round(statistics.mean(samples), 4)}


def benchmark(dataset_dir: Path) -> dict:
    frames, frame_ids, dataset_info = _load_dataset(dataset_dir)
    encode, encode_into = make_packet_encoders(threshold=THRESHOLD)
    slots = [np.empty(TX_BYTES, dtype=np.uint8) for _ in range(2)]
    if np.shares_memory(slots[0], slots[1]):
        raise AssertionError("TX slots must own independent storage")
    input_hash_before = _sha256_array(frames)
    packet_digest = hashlib.sha256()
    for index, (gray, frame_id) in enumerate(zip(frames, frame_ids)):
        slot = slots[index % 2]
        allocated = encode(gray, frame_id=frame_id)
        encode_into(gray, slot, frame_id=frame_id)
        if allocated.tobytes() != slot.tobytes():
            raise AssertionError(f"Gray8 packet differs for frame_id={frame_id}")
        np.testing.assert_array_equal(validate_packet(slot, frame_id), allocated[16:-16].reshape(HEIGHT, WIDTH))
        packet_digest.update(slot)

    for gray, frame_id in zip(frames, frame_ids):
        encode(gray, frame_id=frame_id)
        encode_into(gray, slots[frame_id % 2], frame_id=frame_id)

    allocated_samples: list[float] = []
    reusable_samples: list[float] = []
    for repetition in range(MEASURE_REPETITIONS):
        order = ("allocated", "reusable") if repetition % 2 == 0 else ("reusable", "allocated")
        for index, (gray, frame_id) in enumerate(zip(frames, frame_ids)):
            for method in order:
                started = time.perf_counter()
                if method == "allocated":
                    encode(gray, frame_id=frame_id)
                    allocated_samples.append((time.perf_counter() - started) * 1000.0)
                else:
                    encode_into(gray, slots[index % 2], frame_id=frame_id)
                    reusable_samples.append((time.perf_counter() - started) * 1000.0)

    input_hash_after = _sha256_array(frames)
    if input_hash_after != input_hash_before or input_hash_after != dataset_info["raw_gray8_sha256"]:
        raise AssertionError("raw_gray8 input changed during packet encoding")
    return {
        "mode": "offline-captured-b2-gray8-lighting-encoder-microbenchmark",
        "board_accessed": False,
        "physical_accuracy_claim": False,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "geometry": list(FRAME_SHAPE),
        "threshold": THRESHOLD,
        "packet_bytes": TX_BYTES,
        "dataset": dataset_info,
        "gray8_packet_verification": {
            "per_frame_assertions": FRAME_COUNT,
            "match": True,
            "aggregate_sha256": packet_digest.hexdigest(),
        },
        "input_integrity": {"sha256": input_hash_after, "unchanged": True},
        "measurement_repetitions": MEASURE_REPETITIONS,
        "measurement_calls_per_method": FRAME_COUNT * MEASURE_REPETITIONS,
        "latency_ms_per_packet": {
            "allocated_encode": _stats(allocated_samples),
            "reusable_encode_into": _stats(reusable_samples),
        },
        "transform_storage": {
            "persistent_scratch_bytes": GRAY_BYTES,
            "preallocated_tx_slots": 2,
            "tx_output_bytes_per_slot": TX_BYTES,
        },
        "source_sha256": {
            name: _sha256_file(SCRIPT_DIR / name)
            for name in ("lighting_profile.py", "packet_encoder.py")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error(f"refusing to overwrite existing report: {args.report}")
    report = benchmark(args.dataset_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps({"report_path": str(args.report), **report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
