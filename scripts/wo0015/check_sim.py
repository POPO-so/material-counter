"""Compare XSim's per-beat trace to WO-0015 vectors and write a result JSON."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path


WIDTH = 320
HEIGHT = 240
PIXELS_PER_FRAME = WIDTH * HEIGHT
BEATS_PER_FRAME = PIXELS_PER_FRAME // 4


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_data(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_trace(path: Path) -> list[dict]:
    records: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 3:
            raise ValueError(f"{path}:{line_number}: expected 'TDATA TKEEP TLAST'")
        try:
            data, keep, last = int(fields[0], 16), int(fields[1], 16), int(fields[2], 10)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: invalid numeric trace field") from exc
        if not 0 <= data <= 0xFFFFFFFF or not 0 <= keep <= 0xF or last not in (0, 1):
            raise ValueError(f"{path}:{line_number}: field outside its AXIS width")
        records.append({"data": data, "keep": keep, "last": last})
    return records


def read_metrics(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    converted = []
    for row in rows:
        converted.append({
            "kind": row["kind"],
            "frame": int(row["frame"]),
            "accepted_pixels": int(row["accepted_pixels"]),
            "sent_beats": int(row["sent_beats"]),
            "eof": int(row["eof"]),
            "fault": int(row["fault"]),
            "error_code": int(row["error_code"]),
        })
    return converted


def compare(args: argparse.Namespace) -> dict:
    case_dir = args.case_dir.resolve()
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    golden_path = case_dir / "golden.bin"
    golden = golden_path.read_bytes()
    trace_path = args.actual.resolve()
    metrics_path = args.metrics.resolve()
    trace = read_trace(trace_path)
    metrics = read_metrics(metrics_path)
    kind = case["kind"]
    eof_count = sum(record["last"] for record in trace)
    actual_bytes = b"".join(record["data"].to_bytes(4, "little") for record in trace)
    mismatch_bytes = sum(actual != expected
                         for actual, expected in zip(actual_bytes, golden))

    for index, record in enumerate(trace):
        if record["keep"] != 0xF:
            raise AssertionError(f"output beat {index} TKEEP={record['keep']:x}, expected f")

    if kind in ("normal", "random"):
        frame_count = case["expected_frames"]
        if len(trace) != frame_count * BEATS_PER_FRAME:
            raise AssertionError(f"output beat count {len(trace)} != {frame_count*BEATS_PER_FRAME}")
        expected_last_indexes = {((frame + 1) * BEATS_PER_FRAME) - 1
                                 for frame in range(frame_count)}
        actual_last_indexes = {index for index, record in enumerate(trace) if record["last"]}
        if actual_last_indexes != expected_last_indexes:
            raise AssertionError(f"EOF beat indexes {sorted(actual_last_indexes)} != "
                                 f"{sorted(expected_last_indexes)}")
        if eof_count != frame_count:
            raise AssertionError(f"EOF count {eof_count} != expected {frame_count}")
        if actual_bytes != golden:
            first = next((index for index, pair in enumerate(zip(actual_bytes, golden))
                          if pair[0] != pair[1]), min(len(actual_bytes), len(golden)))
            raise AssertionError(f"Binary8 byte mismatch at offset {first}: "
                                 f"actual={len(actual_bytes)} expected={len(golden)}")
        frame_metrics = [row for row in metrics if row["kind"] == "FRAME"]
        if len(frame_metrics) != frame_count:
            raise AssertionError(f"FRAME metrics count {len(frame_metrics)} != {frame_count}")
        for frame, row in enumerate(frame_metrics):
            expected = (frame, PIXELS_PER_FRAME, BEATS_PER_FRAME, 1, 0, 0)
            actual = (row["frame"], row["accepted_pixels"], row["sent_beats"],
                      row["eof"], row["fault"], row["error_code"])
            if actual != expected:
                raise AssertionError(f"frame {frame} metrics {actual} != {expected}")
        status = "PASS"
    elif kind == "fault":
        if eof_count != 0 or any(record["last"] for record in trace):
            raise AssertionError(f"fault case emitted {eof_count} successful EOFs")
        fault_rows = [row for row in metrics if row["kind"] == "FAULT"]
        if len(fault_rows) != 1:
            raise AssertionError(f"expected one FAULT metric row, found {len(fault_rows)}")
        row = fault_rows[0]
        if row["fault"] != 1 or row["error_code"] == 0:
            raise AssertionError(f"fault did not latch: {row}")
        if len(actual_bytes) > len(golden) or actual_bytes != golden[:len(actual_bytes)]:
            raise AssertionError("pre-fault S2MM prefix differs from the Binary8 golden")
        status = "PASS_FAULT_STOP"
    elif kind == "reset":
        if eof_count != 0 or any(record["last"] for record in trace):
            raise AssertionError(f"reset case emitted {eof_count} successful EOFs")
        reset_rows = [row for row in metrics if row["kind"] == "RESET"]
        if len(reset_rows) != 1:
            raise AssertionError(f"expected one RESET metric row, found {len(reset_rows)}")
        row = reset_rows[0]
        if any(row[key] != 0 for key in ("accepted_pixels", "sent_beats", "fault", "error_code")):
            raise AssertionError(f"reset did not clear packer state: {row}")
        if len(actual_bytes) > len(golden) or actual_bytes != golden[:len(actual_bytes)]:
            raise AssertionError("pre-reset S2MM prefix differs from the Binary8 golden")
        status = "PASS_RESET_STOP"
    else:
        raise ValueError(f"unsupported case kind: {kind}")

    parent_manifest = case_dir.parent / "manifest.json"
    parent_source_manifest_sha = None
    if parent_manifest.is_file():
        parent_source_manifest_sha = json.loads(
            parent_manifest.read_text(encoding="utf-8")).get("source_manifest_sha256")

    if args.sim_log:
        log = args.sim_log.resolve().read_text(encoding="utf-8", errors="replace")
        if "Fatal:" in log or "FATAL:" in log:
            raise AssertionError("XSim log contains Fatal")
        if status == "PASS" and "WO0015_PIPELINE_PASS" not in log:
            raise AssertionError("XSim log lacks WO0015_PIPELINE_PASS")
        if status == "PASS_FAULT_STOP" and "WO0015_FAULT_PASS" not in log:
            raise AssertionError("XSim log lacks WO0015_FAULT_PASS")
        if status == "PASS_RESET_STOP" and "WO0015_RESET_PASS" not in log:
            raise AssertionError("XSim log lacks WO0015_RESET_PASS")

    result = {
        "schema": 1,
        "case": case["case"],
        "kind": kind,
        "status": status,
        "frames_expected": case["expected_frames"],
        "frames_eof": eof_count,
        "eof_beat_numbers": [index + 1 for index, record in enumerate(trace) if record["last"]],
        "eof_positions_zero_based": [index for index, record in enumerate(trace) if record["last"]],
        "output_beats": len(trace),
        "output_bytes": len(actual_bytes),
        "golden_bytes": len(golden),
        "mismatch_bytes": mismatch_bytes,
        "actual_binary_sha256": sha256_data(actual_bytes),
        "actual_trace_sha256": sha256_file(trace_path),
        "golden_sha256": sha256_file(golden_path),
        "source_manifest_sha256": case.get("source_manifest_sha256") or parent_source_manifest_sha,
        "seed": args.seed,
    }
    result_path = args.result.resolve()
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--actual", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--sim-log", type=Path)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    try:
        result = compare(args)
    except (AssertionError, KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"WO0015_SIM_CHECK_FAIL: {exc}", file=sys.stderr)
        if args.result:
            result_path = args.result.resolve()
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps({
                "schema": 1,
                "status": "FAIL",
                "error": str(exc),
                "case_dir": str(args.case_dir.resolve()),
                "actual_trace": str(args.actual.resolve()),
                "metrics": str(args.metrics.resolve()),
                "seed": args.seed,
            }, indent=2) + "\n", encoding="utf-8")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
