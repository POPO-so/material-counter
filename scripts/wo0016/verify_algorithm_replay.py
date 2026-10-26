"""Replay the B2-03 Binary8 capture against an isolated WO-0016 source tree.

This is a host-only algorithm replay and benchmark. It never opens a camera,
connects to the board, or starts a service.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import platform
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any


DEFAULT_DATASET = Path(
    Path(__file__).resolve().parents[2] / "data/results/lighting-calibration-20261025"
    / "board-approval/b2-attempt-03/evidence/b2-capture-b2-m6touch-220-20261025-03"
)
EXPECTED_EVENT_PAIRS = [(79, 5), (79, 8), (79, 9), (81, 7), (81, 10)]
EXPECTED_COUNT = 5
EXPECTED_FRAMES = 220
_NUMPY_MODULE = None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    """Convert nested dataclass/NumPy values to strict JSON-native values."""
    np = _NUMPY_MODULE
    if hasattr(value, "__dataclass_fields__"):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if np is not None and isinstance(value, np.ndarray):
        return [_json_safe(item) for item in value.tolist()]
    if np is not None and isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def _resolve_source_dir(raw: str | Path, script_dir: Path) -> Path:
    value = str(raw)
    if value.lower() == "current":
        path = script_dir
    else:
        path = Path(raw).expanduser()
    path = path.resolve(strict=True)
    if not (path / "stream_counter.py").is_file():
        raise ValueError(f"source directory does not contain stream_counter.py: {path}")
    return path


def _activate_source(source_dir: Path, script_dir: Path) -> tuple[Path, Path]:
    """Put the selected WO-0016 tree first, with WO-0015 from this checkout."""
    repo_root = script_dir.parent.parent
    wo15_dir = (repo_root / "scripts" / "wo0015").resolve(strict=True)
    ordered = list(dict.fromkeys((source_dir.resolve(), wo15_dir, script_dir.resolve())))
    normalized = {str(path).casefold() for path in ordered}
    remainder = [item for item in sys.path
                 if item and str(Path(item).resolve()).casefold() not in normalized]
    sys.path[:] = [str(path) for path in ordered] + remainder

    # The command normally runs in a fresh process. Clear only project module
    # names so an embedded invocation cannot mix a cached current tree with a
    # selected snapshot tree.
    for name in (
        "stream_counter", "split_touching", "run_stream_counter",
        "capture_dataset", "lighting_profile", "live_calibration",
        "packet_encoder", "camera_adapter", "board_once", "board_preflight", "scale_parameters",
        "dashboard_state", "mjpeg_preview", "physical_calibration_lock",
        "calibration_runtime_gate", "prepare_vectors",
    ):
        sys.modules.pop(name, None)
    importlib.invalidate_caches()

    counter_module = importlib.import_module("stream_counter")
    module_path = Path(counter_module.__file__).resolve()
    if module_path.parent != source_dir.resolve():
        raise RuntimeError(
            f"selected stream_counter was shadowed by {module_path}; expected {source_dir}"
        )
    return repo_root, wo15_dir


def _load_capture(dataset_dir: Path):
    import numpy as np

    if not dataset_dir.is_dir():
        raise ValueError(f"dataset directory does not exist: {dataset_dir}")
    manifest_bytes = (dataset_dir / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if manifest.get("status") != "COMPLETE":
        raise ValueError(f"capture status is not COMPLETE: {manifest.get('status')!r}")
    if manifest.get("captured_frames") != EXPECTED_FRAMES:
        raise ValueError(
            f"expected {EXPECTED_FRAMES} captured frames; got {manifest.get('captured_frames')}"
        )

    records_path = dataset_dir / "frame-records.jsonl"
    records_bytes = records_path.read_bytes()
    records_sha256 = _sha256_bytes(records_bytes)
    if records_sha256 != manifest.get("frame_records_sha256"):
        raise ValueError("frame-records.jsonl SHA-256 does not match manifest")
    records = [json.loads(line.decode("utf-8"))
               for line in records_bytes.splitlines() if line.strip()]
    if len(records) != EXPECTED_FRAMES:
        raise ValueError(f"expected {EXPECTED_FRAMES} frame records; got {len(records)}")

    archive_path = dataset_dir / "frames_gray8_binary8.npz"
    with np.load(archive_path, allow_pickle=False) as archive:
        if "raw_binary8" not in archive.files or "archive_metadata_json" not in archive.files:
            raise ValueError("NPZ archive is missing raw_binary8 or archive_metadata_json")
        raw_binary8 = np.array(archive["raw_binary8"], copy=True, order="C")
        archive_metadata = json.loads(str(archive["archive_metadata_json"].item()))
    if raw_binary8.dtype != np.uint8 or raw_binary8.shape != (EXPECTED_FRAMES, 240, 320):
        raise ValueError(
            f"raw_binary8 must be uint8 ({EXPECTED_FRAMES}, 240, 320); "
            f"got {raw_binary8.dtype} {raw_binary8.shape}"
        )
    if not np.isin(raw_binary8, (0, 255)).all():
        raise ValueError("raw_binary8 contains values other than 0x00 and 0xFF")
    raw_binary8_sha256 = _sha256_bytes(raw_binary8.tobytes(order="C"))
    if raw_binary8_sha256 != archive_metadata.get("raw_binary8_sha256"):
        raise ValueError("raw_binary8 SHA-256 does not match embedded NPZ metadata")

    frame_ids: list[int] = []
    recorded_config: dict[str, Any] | None = None
    for index, record in enumerate(records):
        frame_id = record.get("frame_id")
        metadata = record.get("metadata")
        if frame_id != index or not isinstance(metadata, dict):
            raise ValueError(f"frame record {index} is malformed or out of sequence")
        if metadata.get("frame_id") != frame_id:
            raise ValueError(f"frame record {frame_id} metadata frame_id mismatch")
        config = metadata.get("algorithm_config")
        if not isinstance(config, dict):
            raise ValueError(f"frame record {frame_id} has no algorithm_config object")
        if recorded_config is None:
            recorded_config = config
        elif config != recorded_config:
            raise ValueError(f"algorithm_config changes at frame {frame_id}")
        frame_ids.append(frame_id)
    if recorded_config is None:
        raise ValueError("capture has no recorded algorithm configuration")
    return {
        "manifest": manifest,
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "records": records,
        "records_sha256": records_sha256,
        "frame_ids": frame_ids,
        "masks": raw_binary8,
        "raw_binary8_sha256": raw_binary8_sha256,
        "archive_sha256": _sha256_file(archive_path),
        "recorded_config": dict(recorded_config),
    }


def _profile_defaults(source_dir: Path):
    runner = importlib.import_module("run_stream_counter")
    runner_path = Path(runner.__file__).resolve()
    if runner_path.parent != source_dir.resolve():
        raise RuntimeError(
            f"profile defaults came from {runner_path}; expected selected source {source_dir}"
        )
    args = runner._parser().parse_args(["--counting-profile", "lighting-calibrated"])
    return dict(runner.counting_configuration(args)), runner_path


def _serialize_frame(result: Any, counter: Any, frame_id: int) -> dict[str, Any]:
    result_data = asdict(result)
    result_data.pop("roi_subtimings_ms", None)
    return _json_safe({
        "frame_id": frame_id,
        "result": result_data,
        "last_associations": counter.last_associations,
        "suppressed": counter.last_suppressed_events,
    })


def _replay(MaskCounter: Any, masks: Any, frame_ids: list[int],
            config: dict[str, Any]) -> tuple[list[dict[str, Any]], int, list[tuple[int, int]]]:
    counter = MaskCounter(**config)
    output: list[dict[str, Any]] = []
    event_pairs: list[tuple[int, int]] = []
    for index, frame_id in enumerate(frame_ids):
        result = counter.update(frame_id, masks[index])
        output.append(_serialize_frame(result, counter, frame_id))
        event_pairs.extend((event.frame_id, event.track_id) for event in result.events)
    return output, int(counter.total_count), event_pairs


def _timing_summary(samples_ns: list[int]) -> dict[str, float | int]:
    np = _NUMPY_MODULE
    if np is None:  # pragma: no cover - guarded by main's runtime imports
        raise RuntimeError("NumPy is required for benchmark percentiles")
    values = [sample / 1_000_000 for sample in samples_ns]
    return {
        "samples": len(values),
        "mean_ms": statistics.fmean(values),
        "p50_ms": float(np.percentile(values, 50)),
        "p95_ms": float(np.percentile(values, 95)),
        "min_ms": min(values),
        "max_ms": max(values),
    }


def _benchmark(MaskCounter: Any, masks: Any, frame_ids: list[int],
               config: dict[str, Any], repeats: int) -> dict[str, Any]:
    # One untimed full replay warms imports and algorithm paths before samples.
    warm_counter = MaskCounter(**config)
    for index, frame_id in enumerate(frame_ids):
        warm_counter.update(frame_id, masks[index])

    per_frame_ns: list[list[int]] = [[] for _ in frame_ids]
    wall_ns: list[int] = []
    for _ in range(repeats):
        counter = MaskCounter(**config)
        started = time.perf_counter_ns()
        for index, frame_id in enumerate(frame_ids):
            frame_started = time.perf_counter_ns()
            counter.update(frame_id, masks[index])
            per_frame_ns[index].append(time.perf_counter_ns() - frame_started)
        wall_ns.append(time.perf_counter_ns() - started)

    return {
        "warmup_replays": 1,
        "timed_replays": repeats,
        "per_frame_latency_ms": [
            {"frame_id": frame_ids[index], **_timing_summary(samples)}
            for index, samples in enumerate(per_frame_ns)
        ],
        "per_frame_latency_over_all_samples": _timing_summary(
            [sample for frame_samples in per_frame_ns for sample in frame_samples]
        ),
        "whole_replay_wall": {
            **_timing_summary(wall_ns),
            "mean_ms_per_frame": statistics.fmean(wall_ns) / 1_000_000 / len(frame_ids),
        },
        "timed_region": "MaskCounter.update calls only; excludes serialization, file IO, and counter construction",
    }


def _first_difference(left: Any, right: Any, path: str = "$") -> str | None:
    if type(left) is not type(right):
        return f"{path}: type {type(left).__name__} != {type(right).__name__}"
    if isinstance(left, dict):
        if left.keys() != right.keys():
            missing = sorted(left.keys() - right.keys())
            extra = sorted(right.keys() - left.keys())
            return f"{path}: object keys differ (reference missing={missing}, current extra={extra})"
        for key in left:
            difference = _first_difference(left[key], right[key], f"{path}.{key}")
            if difference:
                return difference
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return f"{path}: list length {len(left)} != {len(right)}"
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            difference = _first_difference(left_item, right_item, f"{path}[{index}]")
            if difference:
                return difference
        return None
    if left != right:
        return f"{path}: {left!r} != {right!r}"
    return None


def _compare_reference(reference_path: Path, current_frames: list[dict[str, Any]],
                       dataset_sha256: str) -> dict[str, Any]:
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    if reference.get("dataset", {}).get("frame_records_sha256") != dataset_sha256:
        return {"status": "FAIL", "reason": "reference uses different frame-records data"}
    reference_frames = reference.get("frames")
    if not isinstance(reference_frames, list):
        return {"status": "FAIL", "reason": "reference has no frames array"}
    if len(reference_frames) != len(current_frames):
        return {"status": "FAIL", "reason": (
            f"frame count differs: reference={len(reference_frames)}, current={len(current_frames)}")}
    for index, (expected, actual) in enumerate(zip(reference_frames, current_frames)):
        difference = _first_difference(expected, actual, f"frames[{index}]")
        if difference:
            return {"status": "FAIL", "compared_frames": index + 1,
                    "first_difference": difference}
    return {"status": "PASS", "compared_frames": len(current_frames)}


def _write_exclusive(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(_json_safe(report), stream, ensure_ascii=False, indent=2,
                  sort_keys=True, allow_nan=False)
        stream.write("\n")


def main(argv: list[str] | None = None) -> int:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default=str(script_dir),
                        help="path to an isolated WO-0016 source tree, or 'current'")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, required=True,
                        help="new JSON result path; existing files are never overwritten")
    parser.add_argument("--reference", type=Path,
                        help="optional prior replay JSON for exact per-frame comparison")
    parser.add_argument("--repeats", type=int, default=15,
                        help="number of timed full replays after one warmup (default: 15)")
    parser.add_argument("--use-profile-defaults", action="store_true",
                        help="derive config from selected source's lighting-calibrated profile")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    try:
        source_dir = _resolve_source_dir(args.source_dir, script_dir)
        repo_root, wo15_dir = _activate_source(source_dir, script_dir)
        import numpy as np
        import cv2
        global _NUMPY_MODULE
        _NUMPY_MODULE = np

        counter_module = sys.modules["stream_counter"]
        MaskCounter = counter_module.MaskCounter
        capture = _load_capture(args.dataset_dir.expanduser().resolve(strict=True))
        if args.use_profile_defaults:
            config, config_source_path = _profile_defaults(source_dir)
            config_mode = "selected-source lighting-calibrated profile defaults"
        else:
            config = capture["recorded_config"]
            config_source_path = None
            config_mode = "B2-03 frame-records metadata algorithm_config"

        signature = inspect.signature(MaskCounter)
        unexpected = sorted(set(config) - set(signature.parameters))
        if unexpected:
            raise ValueError(f"selected MaskCounter does not accept config keys: {unexpected}")

        frames, total_count, event_pairs = _replay(
            MaskCounter, capture["masks"], capture["frame_ids"], config
        )
        benchmark = _benchmark(
            MaskCounter, capture["masks"], capture["frame_ids"], config, args.repeats
        )
        event_pairs_native = [[int(frame_id), int(track_id)]
                              for frame_id, track_id in event_pairs]
        event_contract = {
            "status": "PASS" if event_pairs_native == [list(pair) for pair in EXPECTED_EVENT_PAIRS]
            and total_count == EXPECTED_COUNT else "FAIL",
            "expected_events": [list(pair) for pair in EXPECTED_EVENT_PAIRS],
            "actual_events": event_pairs_native,
            "expected_count": EXPECTED_COUNT,
            "actual_count": total_count,
        }

        source_files = {"stream_counter.py": _sha256_file(source_dir / "stream_counter.py")}
        if (source_dir / "split_touching.py").is_file():
            source_files["split_touching.py"] = _sha256_file(source_dir / "split_touching.py")
        if (source_dir / "scale_parameters.py").is_file():
            source_files["scale_parameters.py"] = _sha256_file(source_dir / "scale_parameters.py")
        if config_source_path is not None:
            source_files["run_stream_counter.py"] = _sha256_file(config_source_path)
        profile_imports = {}
        prepare_vectors = sys.modules.get("prepare_vectors")
        if prepare_vectors is not None:
            dependency_path = Path(prepare_vectors.__file__).resolve()
            if dependency_path.parent != wo15_dir:
                raise RuntimeError(
                    f"WO-0015 dependency was imported from {dependency_path}, expected {wo15_dir}"
                )
            profile_imports["prepare_vectors.py"] = _sha256_file(dependency_path)

        comparison = ({"status": "NOT_REQUESTED"} if args.reference is None else
                      _compare_reference(args.reference.expanduser().resolve(strict=True),
                                         frames, capture["records_sha256"]))
        report = {
            "schema_version": 1,
            "status": "REPLAY_COMPLETE",
            "acceptance_status": "PASS" if event_contract["status"] == "PASS"
            and comparison["status"] in ("PASS", "NOT_REQUESTED") else "FAIL",
            "algorithm": "MaskCounter Binary8 replay",
            "configuration": {"mode": config_mode, "values": _json_safe(config)},
            "dataset": {
                "path": str(args.dataset_dir.expanduser().resolve()),
                "frame_count": len(frames),
                "frame_records_sha256": capture["records_sha256"],
                "manifest_sha256": capture["manifest_sha256"],
                "npz_sha256": capture["archive_sha256"],
                "raw_binary8_sha256": capture["raw_binary8_sha256"],
                "mask_source": "frames_gray8_binary8.npz:raw_binary8",
            },
            "source": {
                "source_dir": str(source_dir),
                "wo0015_dependency_dir": str(wo15_dir),
                "files_sha256": source_files,
                "wo0015_files_sha256": profile_imports,
            },
            "runtime": {
                "python": sys.version.split()[0],
                "numpy": np.__version__,
                "opencv": cv2.__version__,
                "platform": platform.platform(),
            },
            "event_count_contract": event_contract,
            "reference_comparison": comparison,
            "performance": benchmark,
            "frames": frames,
        }
        _write_exclusive(args.output.expanduser().resolve(strict=False), report)
        print(json.dumps({
            "status": report["acceptance_status"],
            "frames": len(frames),
            "count": total_count,
            "events": event_pairs_native,
            "event_contract": event_contract["status"],
            "reference": comparison["status"],
            "mean_frame_ms": benchmark["per_frame_latency_over_all_samples"]["mean_ms"],
            "whole_replay_mean_ms": benchmark["whole_replay_wall"]["mean_ms"],
            "output": str(args.output.expanduser().resolve(strict=False)),
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "opencv": cv2.__version__,
        }, ensure_ascii=False, sort_keys=True))
        return 0 if report["acceptance_status"] == "PASS" else 1
    except (OSError, ValueError, RuntimeError, ImportError, json.JSONDecodeError) as exc:
        print(f"verify_algorithm_replay: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
