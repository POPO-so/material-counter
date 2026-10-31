"""Pure CPU microbenchmarks run inside one temporary source copy per process."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time

import numpy as np


WARMUPS = 5
SAMPLES = 30


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def measure(function) -> dict:
    for _ in range(WARMUPS):
        function()
    samples = []
    for _ in range(SAMPLES):
        started = time.perf_counter_ns()
        function()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "warmups": WARMUPS,
        "samples": SAMPLES,
        "values_ms": samples,
        "p50_ms": statistics.median(samples),
        "p95_ms": float(np.percentile(samples, 95)),
        "max_ms": max(samples),
    }


def _captured_mask(npz_path: Path, height: int, width: int) -> np.ndarray:
    with np.load(npz_path, allow_pickle=False) as data:
        runs = np.asarray(data["runs"], dtype=np.uint32).copy()
    mask = np.zeros((height, width), dtype=np.uint8)
    for packed in runs:
        row = int(packed >> 20)
        start = int((packed >> 10) & 0x3FF)
        end = int(packed & 0x3FF)
        if row >= height or start > end or end >= width:
            raise ValueError("captured RLE contains out-of-frame coordinates")
        mask[row, start:end + 1] = 255
    return mask, runs


def run(version: str, root: Path, captured_npz: Path, output: Path) -> None:
    root = root.resolve(strict=True)
    captured_npz = captured_npz.resolve(strict=True)
    sys.path.insert(0, str(root))

    from scripts.rle.protocol import (HEIGHT, PIXELS, TX_BYTES, WIDTH,
                                      decode_packet, encode_gray_into, encode_mask)
    from scripts.standalone.count_controls import make_runtime_counter
    from scripts.standalone.crossing_snapshots import CrossingSnapshots

    if "pynq" in sys.modules or any(name.startswith("pynq.") for name in sys.modules):
        raise RuntimeError("PYNQ must not be imported for this benchmark")

    package_source_path = root / "package-source.json"
    declared = []
    if package_source_path.is_file():
        package_source = json.loads(package_source_path.read_text(encoding="utf-8"))
        declared = list(package_source.get("module_files", []))
    for required in ("scripts/standalone/performance_replay.py",
                     "scripts/standalone/crossing_snapshots.py",
                     "scripts/rle/protocol.py"):
        if required not in declared:
            declared.append(required)
    declared = sorted(set(declared))
    source_hashes = {name: sha256_file(root / name) for name in declared}
    native_path = root / "native/rle_components_v2.so"

    gray = (np.arange(PIXELS, dtype=np.uint32).reshape(HEIGHT, WIDTH) % 256).astype(np.uint8)
    gray_output = bytearray(TX_BYTES)
    gray_input_sha = sha256_bytes(gray.tobytes())
    gray_measure = measure(lambda: encode_gray_into(gray, gray_output, frame_id=123))

    empty_mask = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    high_runs_mask = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    high_runs_mask[:50, ::2] = 255  # 16,000 isolated runs, below MAX_RUNS.
    captured_mask, captured_runs = _captured_mask(captured_npz, HEIGHT, WIDTH)
    masks = {
        "empty": empty_mask,
        "high_runs_16000": high_runs_mask,
        "captured_frame": captured_mask,
    }
    decode_results = {}
    input_hashes = {
        "gray8_array_sha256": gray_input_sha,
        "captured_rle_npz_sha256": sha256_file(captured_npz),
        "captured_runs_sha256": sha256_bytes(captured_runs.tobytes()),
        "masks_sha256": {},
        "encoded_packets_sha256": {},
    }
    packets = {}
    for index, (name, mask) in enumerate(masks.items(), 1):
        packet = encode_mask(mask, frame_id=1000 + index)
        parsed = decode_packet(packet)
        if not parsed.valid:
            raise ValueError(f"prepared {name} RLE packet is invalid")
        packets[name] = packet
        input_hashes["masks_sha256"][name] = sha256_bytes(mask.tobytes())
        input_hashes["encoded_packets_sha256"][name] = sha256_bytes(packet)
        decode_results[name] = {
            "runs": parsed.run_count,
            "total_foreground": parsed.total_fg,
            "packet_bytes": parsed.actual_bytes,
            "decode": measure(lambda packet=packet: decode_packet(packet)),
        }

    counter = make_runtime_counter(None, profile="field-v1")
    empty_packet = decode_packet(packets["empty"])
    snapshots = CrossingSnapshots(
        f"/tmp/material-perf-observe-{version}-{os.getpid()}",
        identity={"benchmark": "pure-cpu-empty-frame"}, context_frames=0,
        storage_mode="compact", min_free_bytes=0)
    observe_measure = measure_observer = None
    sequence = 0

    def observe_once() -> None:
        nonlocal sequence
        frame_id = 2000 + sequence
        sequence += 1
        packet = decode_packet(encode_mask(empty_mask, frame_id=frame_id))
        result = counter.update_rle(packet, capture_monotonic_ns=frame_id * 100_000_000)
        started = time.perf_counter_ns()
        snapshots.observe(gray, packet, result, counter,
                          capture_monotonic_ns=frame_id * 100_000_000,
                          camera_sequence=frame_id, paused=False, commands=(), timings=None)
        observe_measure_holder["values"].append((time.perf_counter_ns() - started) / 1_000_000)

    observe_measure_holder = {"values": []}
    # Keep the observer timer separate from input generation/counter update.
    for _ in range(WARMUPS):
        observe_once()
    observe_measure = []
    for _ in range(SAMPLES):
        before = len(observe_measure_holder["values"])
        observe_once()
        observe_measure.append(observe_measure_holder["values"][before])
    observe_measure = {
        "warmups": WARMUPS, "samples": SAMPLES,
        "values_ms": observe_measure,
        "p50_ms": statistics.median(observe_measure),
        "p95_ms": float(np.percentile(observe_measure, 95)),
        "max_ms": max(observe_measure),
    }
    closed = snapshots.close()
    status = snapshots.status()
    if not closed or status["writer_alive"] or status["observed"] != WARMUPS + SAMPLES:
        raise RuntimeError(f"snapshot writer did not drain cleanly: {status}")

    if "pynq" in sys.modules or any(name.startswith("pynq.") for name in sys.modules):
        raise RuntimeError("PYNQ was imported during this benchmark")
    result = {
        "schema": "material-counter.pure-cpu-microbench.v1",
        "version": version,
        "mode": "cpu-only-no-camera-no-pynq-no-mmio-no-dma",
        "source": {"root": str(root), "sha256_by_path": source_hashes,
                   "native_library": {"path": str(native_path),
                                      "sha256": sha256_file(native_path)}},
        "inputs": input_hashes,
        "measurements": {
            "gray8_pack_into_640x400": gray_measure,
            "rle_decode": decode_results,
            "crossing_snapshots_observe_empty_frame": observe_measure,
        },
        "snapshot_writer": {
            "events": status["triggered"], "saved": status["saved"],
            "write_errors": status["write_errors"], "closed_cleanly": closed,
        },
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps({"version": version, "source_modules": len(source_hashes),
                      "native_sha256": result["source"]["native_library"]["sha256"],
                      "measurement_groups": list(result["measurements"]),
                      "writer_events": status["triggered"]}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, choices=("v7", "v8"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--captured-npz", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.version, args.root, args.captured_npz, args.output)


if __name__ == "__main__":
    main()
