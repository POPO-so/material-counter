"""Replay hash-pinned camera-only clips through the offline split HMI path.

The inputs are Gray8 camera archives. This tool thresholds them locally at
128, runs the host counter and mock live-monitor state, and never opens a
camera or imports/initializes a PYNQ device. Its result is not PL Binary8 or
physical-accuracy acceptance evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable

import cv2
import numpy as np

from live_monitor_state import LiveMonitorState
from mjpeg_preview import render_preview_frame
from stream_counter import MaskCounter


THRESHOLD = 128
RECEIPT_NAME = "replay-receipt.json"
CAPTURE_SPECS = (
    {
        "name": "two-nut-pair",
        "directory": "wo_touching_pair_motion_20261024_03",
        "sha256": "0b55c6e1d5a43a4c69702dd89ef14be09c1673cdbc7b8d22b7bf5b8fbe40ce9e",
        "frame_stop": 901,
        "baseline_event_frames": (834,),
        "split_event_frames": (834, 834),
    },
    {
        "name": "three-nut-first-recording",
        "directory": "wo_touching_three_motion_20261024_02",
        "sha256": "7edb8fcc077a0592b957f485ae6d5b99989bdf61b2d0c17cff7d71afaa2ad9fa",
        "frame_stop": 100,
        "baseline_event_frames": (36,),
        "split_event_frames": (34, 37, 37),
    },
    {
        "name": "three-nut-rerecord",
        "directory": "wo_touching_three_motion_20261024_04",
        "sha256": "c534ad4334afa725337a03d5ed849e5ae81857b5c8f5bf1c1686b465884ed59d",
        "frame_stop": 80,
        "baseline_event_frames": (27,),
        "split_event_frames": (25, 28, 28),
    },
)
SOURCE_FILES = (
    "scripts/wo0016/verify_split_hmi_replay.py",
    "scripts/wo0016/run_stream_counter.py",
    "scripts/wo0016/run_live_monitor.py",
    "scripts/wo0016/stream_counter.py",
    "scripts/wo0016/split_touching.py",
    "scripts/wo0016/live_monitor_state.py",
    "scripts/wo0016/mjpeg_preview.py",
)


@dataclass(frozen=True)
class CaptureSpec:
    name: str
    directory: str
    sha256: str
    frame_stop: int
    baseline_event_frames: tuple[int, ...]
    split_event_frames: tuple[int, ...]
    frame_count: int = 1800

    @property
    def expected_baseline_count(self) -> int:
        return len(self.baseline_event_frames)

    @property
    def expected_split_count(self) -> int:
        return len(self.split_event_frames)


class ReplayValidationError(ValueError):
    """The replay completed but did not match the pinned expected result."""

    def __init__(self, message: str, report: dict[str, Any] | None = None):
        super().__init__(message)
        self.report = report


def _spec_from_mapping(item: CaptureSpec | dict[str, Any]) -> CaptureSpec:
    if isinstance(item, CaptureSpec):
        return item
    return CaptureSpec(
        name=item["name"], directory=item["directory"], sha256=item["sha256"],
        frame_stop=item["frame_stop"],
        baseline_event_frames=tuple(item["baseline_event_frames"]),
        split_event_frames=tuple(item["split_event_frames"]),
        frame_count=item.get("frame_count", 1800),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def threshold_gray8(gray: np.ndarray) -> np.ndarray:
    """Apply the documented >=128 optical threshold without changing order."""
    image = np.asarray(gray)
    if image.dtype != np.uint8 or image.ndim not in (2, 3):
        raise ValueError("Gray8 input must be a uint8 frame or frame sequence")
    if image.shape[-2:] != (240, 320):
        raise ValueError("Gray8 frames must have shape (240, 320)")
    return np.where(image >= THRESHOLD, 0xFF, 0).astype(np.uint8)


def _expected_record(spec: CaptureSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "directory": spec.directory,
        "expected_sha256": spec.sha256,
        "frame_window": [0, spec.frame_stop],
        "expected_baseline_count": spec.expected_baseline_count,
        "expected_baseline_event_frames": list(spec.baseline_event_frames),
        "expected_split_count": spec.expected_split_count,
        "expected_split_event_frames": list(spec.split_event_frames),
    }


def _runtime_identity(repo_root: Path) -> dict[str, Any]:
    source_hashes = {}
    for relative in SOURCE_FILES:
        path = repo_root / relative
        if not path.is_file():
            raise FileNotFoundError(f"required replay source is missing: {path}")
        source_hashes[relative] = _sha256(path)
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"could not bind replay to Git HEAD: {exc}") from exc
    return {
        "tested_head": head,
        "source_sha256": source_hashes,
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "opencv": cv2.__version__,
    }


def _profile_configs() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the actual finite and live full-width-mixed configurations."""
    # Importing these host-side parsers does not open the camera or initialize
    # PYNQ. Their device operations remain behind explicit board-mode methods.
    import run_live_monitor
    import run_stream_counter

    enabled_args = ("--counting-profile", "full-width-mixed", "--enable-touching-split")
    finite_enabled = run_stream_counter._parser().parse_args(list(enabled_args))
    live_enabled = run_live_monitor.parser().parse_args(["--enable-touching-split"])
    baseline_args = run_live_monitor.parser().parse_args([])
    enabled_finite_config = run_stream_counter.counting_configuration(finite_enabled)
    enabled_live_config = run_stream_counter.counting_configuration(live_enabled)
    baseline_config = run_stream_counter.counting_configuration(baseline_args)
    if enabled_finite_config != enabled_live_config:
        raise RuntimeError("finite and live full-width configurations do not match")
    if not enabled_live_config.get("enable_touching_split"):
        raise RuntimeError("full-width replay configuration must enable touching split")
    enabled_live_config["discard_finished_tracks"] = True
    baseline_config["discard_finished_tracks"] = True
    return baseline_config, enabled_live_config


def _detection_rows(detections: Iterable[Any]) -> list[dict[str, Any]]:
    return [asdict(detection) for detection in detections]


def _count_qualified_binary_components(mask: np.ndarray, min_area: int) -> int:
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    return int(np.count_nonzero(stats[1:count, cv2.CC_STAT_AREA] >= min_area))


def replay_gray_frames(gray_frames: np.ndarray, metadata: dict[str, np.ndarray],
                       spec: CaptureSpec | dict[str, Any]) -> dict[str, Any]:
    """Run baseline and split counters plus real mock HMI state on one clip."""
    spec = _spec_from_mapping(spec)
    frames = np.asarray(gray_frames)
    if frames.dtype != np.uint8 or frames.ndim != 3 or frames.shape[1:] != (240, 320):
        raise ValueError("camera archive must contain uint8 frames shaped (N, 240, 320)")
    if len(frames) < spec.frame_stop:
        raise ValueError(f"capture {spec.name} has {len(frames)} frames; needs {spec.frame_stop}")
    if spec.frame_stop < 1 or spec.frame_stop > spec.frame_count:
        raise ValueError(f"capture {spec.name} has an invalid replay window")
    required_metadata = ("monotonic_ns", "wall_ns", "read_ms")
    for key in required_metadata:
        values = np.asarray(metadata.get(key))
        if values.shape != (len(frames),):
            raise ValueError(f"capture metadata {key} must match the complete frame count")
        if key in ("monotonic_ns", "wall_ns") and values.dtype != np.int64:
            raise ValueError(f"capture metadata {key} must be int64")
        if key == "read_ms" and (values.dtype != np.float32
                                  or not np.isfinite(values).all()
                                  or np.any(values < 0)):
            raise ValueError("capture metadata read_ms must be finite nonnegative float32")
    monotonic = np.asarray(metadata["monotonic_ns"])
    wall = np.asarray(metadata["wall_ns"])
    if np.any(monotonic <= 0) or np.any(wall <= 0) or np.any(np.diff(monotonic) <= 0):
        raise ValueError("capture timestamps must be positive and monotonic_ns strictly increasing")

    baseline_config, split_config = _profile_configs()
    baseline = MaskCounter(**baseline_config)
    counter = MaskCounter(**split_config)
    monitor = LiveMonitorState(mode="mock")
    rows = []
    previews = {}
    for frame_id in range(spec.frame_stop):
        gray = frames[frame_id]
        mask = threshold_gray8(gray)
        base_result = baseline.update(frame_id, mask)
        result = counter.update(frame_id, mask)
        event_count_before = len(monitor.events)
        monitor_result = monitor.complete_frame(result)
        dashboard = monitor.snapshot()["dashboard"]
        monitor_events = list(monitor.events)
        current_monitor_events = monitor_events[event_count_before:]
        for event in result.events:
            matching = [entry for entry in current_monitor_events
                        if entry.get("track_id") == event.track_id]
            track = next((item for item in monitor_result.tracks
                          if item.get("track_id") == event.track_id), None)
            if len(matching) != 1 or track is None:
                raise RuntimeError(
                    f"live telemetry is missing same-frame track {event.track_id} at frame {frame_id}"
                )
            detection = track.get("detection", {})
            velocity = track.get("forward_velocity")
            if not monitor._valid_velocity(velocity):
                velocity = None
            entry = matching[0]
            if (entry.get("id") != frame_id + 1
                    or entry.get("area_px") != detection.get("area")
                    or entry.get("px_per_frame") != velocity
                    or entry.get("inferred_gap") is not event.inferred_gap):
                raise RuntimeError(
                    f"live telemetry does not match track {event.track_id} at frame {frame_id}"
                )
        if result.events:
            overlay = render_preview_frame(
                mask, total_count=monitor_result.total_count,
                line_y=counter.line_y, count_axis=counter.count_axis,
                line_x=counter.line_x, source_gray=gray,
                hysteresis=counter.hysteresis, label="OFFLINE TOTAL",
                pip_label="THRESHOLD 128", tracks=monitor_result.tracks,
                roi_top=20, roi_bottom=220,
            )
            ok, encoded = cv2.imencode(".png", overlay)
            if not ok:
                raise OSError(f"could not encode overlay for {spec.name} frame {frame_id}")
            previews[frame_id] = encoded.tobytes()
        split_children = [detection for detection in result.raw_detections
                          if detection in counter._split_origins]
        rows.append({
            "frame_id": frame_id,
            "monotonic_ns": int(monotonic[frame_id]),
            "wall_ns": int(wall[frame_id]),
            "read_ms": float(metadata["read_ms"][frame_id]),
            "threshold": THRESHOLD,
            "raw_binary8_components": _count_qualified_binary_components(
                mask[20:220], counter.min_area),
            "baseline": {
                "precluster_detections": _detection_rows(base_result.raw_detections),
                "detections": _detection_rows(base_result.detections),
                "events": [asdict(event) for event in base_result.events],
                "total_count": base_result.total_count,
            },
            "split_enabled": {
                "split_children": _detection_rows(split_children),
                "precluster_detections": _detection_rows(result.raw_detections),
                "detections": _detection_rows(result.detections),
                "associations": list(counter.last_associations),
                "events": [asdict(event) for event in result.events],
                "total_count": result.total_count,
                "tracks": list(result.tracks),
                "rejected_detections": _detection_rows(counter.rejected_detections),
            },
            "monitor": {
                "source": dashboard["source"], "mode": dashboard["mode"],
                "phase": dashboard["phase"], "frames_processed": dashboard["frames_processed"],
                "total_count": dashboard["total_count"], "detections": dashboard["detections"],
                "camera": dashboard["camera"], "fpga": dashboard["fpga"],
                "dma": dashboard["dma"], "cma_free_kib": dashboard["cma_free_kib"],
                "processing_fps": dashboard["processing_fps"],
                "fps_kind": dashboard["fps_kind"],
                "px_per_frame": dashboard["px_per_frame"],
                "px_per_second": dashboard["px_per_second"],
                "cm_per_second": dashboard["cm_per_second"],
                "mm_per_pixel": dashboard["mm_per_pixel"],
                "speed_kind": dashboard["speed_kind"],
                "last_crossing_area_px": dashboard["last_crossing_area_px"],
                "events_added": current_monitor_events,
            },
        })

    baseline_events = tuple(event["frame_id"] for row in rows
                            for event in row["baseline"]["events"])
    split_events = tuple(event["frame_id"] for row in rows
                         for event in row["split_enabled"]["events"])
    baseline_count = baseline.total_count
    split_count = counter.total_count
    expected_baseline = tuple(sorted(spec.baseline_event_frames))
    expected_split = tuple(sorted(spec.split_event_frames))
    actual_baseline = tuple(sorted(baseline_events))
    actual_split = tuple(sorted(split_events))
    report = {
        "name": spec.name,
        "directory": spec.directory,
        "frame_window": [0, spec.frame_stop],
        "threshold": THRESHOLD,
        "expected_baseline_count": spec.expected_baseline_count,
        "expected_baseline_event_frames": list(expected_baseline),
        "baseline_total": baseline_count,
        "baseline_event_frames": list(baseline_events),
        "baseline_historical_event_frames_match": actual_baseline == expected_baseline,
        "expected_split_count": spec.expected_split_count,
        "expected_split_event_frames": list(expected_split),
        "split_enabled_total": split_count,
        "split_enabled_event_frames": list(split_events),
        "split_historical_event_frames_match": actual_split == expected_split,
        "split_event_frame_match_note": "Historical frames are reported as a comparison only; the strict acceptance gate here is the expected total count.",
        "frame_count": len(rows),
        "frames": rows,
        "preview_frames": sorted(previews),
        "previews": previews,
    }
    if baseline_count != spec.expected_baseline_count:
        raise ReplayValidationError(
            f"{spec.name} baseline expected count {spec.expected_baseline_count}; "
            f"got {baseline_count} with event frames {list(actual_baseline)}", report,
        )
    if split_count != spec.expected_split_count:
        raise ReplayValidationError(
            f"{spec.name} split-enabled expected count {spec.expected_split_count}; "
            f"got {split_count} with event frames {list(actual_split)}", report,
        )
    return report


def _load_capture(capture_dir: Path, spec: CaptureSpec) -> dict[str, Any]:
    archive_path = capture_dir / "camera_gray_only.npz"
    manifest_path = capture_dir / "manifest.json"
    if not archive_path.is_file():
        raise FileNotFoundError(f"missing camera archive: {archive_path}")
    archive_sha256 = _sha256(archive_path)
    if archive_sha256 != spec.sha256:
        raise ValueError(
            f"{spec.name} archive SHA-256 mismatch: expected {spec.sha256}, got {archive_sha256}"
        )
    manifest_sha256 = None
    if manifest_path.is_file():
        manifest_bytes = manifest_path.read_bytes()
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        try:
            manifest = json.loads(manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{spec.name} supplied manifest is not valid UTF-8 JSON") from exc
        if (manifest.get("status") != "COMPLETE"
                or manifest.get("scope") != "CAMERA_ONLY_NOT_PL_BINARY8"
                or manifest.get("compact") is not True
                or manifest.get("archive") != archive_path.name
                or manifest.get("archive_sha256") != archive_sha256
                or manifest.get("frame_count") != spec.frame_count):
            raise ValueError(f"{spec.name} supplied manifest does not match the pinned compact camera capture")
    try:
        with np.load(archive_path, allow_pickle=False) as stored:
            required = {"input_gray320x240", "monotonic_ns", "wall_ns", "read_ms"}
            missing = sorted(required - set(stored.files))
            if missing:
                raise ValueError(f"{spec.name} camera archive is missing arrays: {missing}")
            if "source_gray640x400" in stored.files:
                raise ValueError(f"{spec.name} compact archive unexpectedly contains source frames")
            gray = np.array(stored["input_gray320x240"], copy=True)
            metadata = {key: np.array(stored[key], copy=True)
                        for key in ("monotonic_ns", "wall_ns", "read_ms")}
    except (OSError, ValueError, KeyError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(spec.name):
            raise
        raise ValueError(f"could not read {spec.name} camera archive: {exc}") from exc
    if (gray.dtype != np.uint8 or gray.shape != (spec.frame_count, 240, 320)
            or any(values.shape != (spec.frame_count,) for values in metadata.values())):
        raise ValueError(f"{spec.name} camera archive has unexpected frame or metadata geometry")
    return {
        "gray": gray,
        "metadata": metadata,
        "archive_path": archive_path,
        "archive_sha256": archive_sha256,
        "manifest_path": manifest_path,
        "manifest_sha256": manifest_sha256,
    }


def _capture_paths(capture_root: Path, specs: tuple[CaptureSpec, ...]) -> list[dict[str, Any]]:
    missing = []
    for spec in specs:
        directory = capture_root / spec.directory
        files = [directory / "camera_gray_only.npz"]
        missing_files = [str(path) for path in files if not path.is_file()]
        if missing_files:
            missing.append({**_expected_record(spec), "missing_paths": missing_files})
    return missing


def _base_receipt(repo_root: Path, specs: tuple[CaptureSpec, ...]) -> dict[str, Any]:
    return {
        "scope": "OFFLINE_CAMERA_GRAY8_THRESHOLD_REPLAY",
        "status": "NOT_RUN",
        "threshold": THRESHOLD,
        "expected_captures": [_expected_record(spec) for spec in specs],
        "physical_accuracy": "NOT_VERIFIED",
        "board_fps": None,
        "hardware_actions": 0,
        "runtime": _runtime_identity(repo_root),
    }


def _write_receipt(output: Path, receipt: dict[str, Any], previews: dict[str, bytes] | None = None
                   ) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=False, exist_ok=False)
    if previews:
        preview_dir = output / "overlays"
        preview_dir.mkdir()
        for relative, data in sorted(previews.items()):
            path = preview_dir / relative
            path.write_bytes(data)
            receipt.setdefault("overlays", []).append({
                "path": path.relative_to(output).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
    target = output / RECEIPT_NAME
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2,
                                  sort_keys=True) + "\n", encoding="utf-8")


def _validate_output_path(capture_root: Path, output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output must be a new output directory: {output}")
    if output == capture_root or capture_root in output.parents:
        raise ValueError("output directory must be outside the capture evidence root")


def _single_object_masks(kind: str) -> Iterable[np.ndarray]:
    for center_x in range(130, 181, 2):
        mask = np.zeros((240, 320), dtype=np.uint8)
        if kind == "round":
            cv2.circle(mask, (center_x, 120), 12, 0xFF, -1)
            cv2.circle(mask, (center_x, 120), 4, 0, -1)
        elif kind == "hex":
            points = np.array([
                (center_x - 12, 120), (center_x - 6, 110), (center_x + 6, 110),
                (center_x + 12, 120), (center_x + 6, 130), (center_x - 6, 130),
            ], dtype=np.int32)
            cv2.fillPoly(mask, [points], 0xFF)
            cv2.circle(mask, (center_x, 120), 3, 0, -1)
        else:
            raise ValueError(f"unknown single-object control: {kind}")
        yield mask


def run_single_nut_controls() -> dict[str, Any]:
    """Run synthetic round and hex hollow controls; these are not recordings."""
    _, config = _profile_configs()
    controls = {}
    for kind in ("round", "hex"):
        counter = MaskCounter(**config)
        raw_counts = []
        child_counts = []
        false_split_frames = []
        for frame_id, mask in enumerate(_single_object_masks(kind)):
            result = counter.update(frame_id, mask)
            raw_counts.append(_count_qualified_binary_components(mask, counter.min_area))
            child_counts.append(len(result.raw_detections))
            if len(result.raw_detections) > 1:
                false_split_frames.append(frame_id)
        if counter.total_count != 1 or false_split_frames:
            raise ReplayValidationError(
                f"synthetic {kind} single-nut control expected one count and zero splits; "
                f"got count {counter.total_count}, split frames {false_split_frames}"
            )
        controls[kind] = {
            "source": "SYNTHETIC_CONTROL_NOT_RECORDED",
            "frame_count": len(raw_counts),
            "max_raw_detections_per_frame": max(raw_counts, default=0),
            "max_child_detections_per_frame": max(child_counts, default=0),
            "false_split_frames": false_split_frames,
            "total_count": counter.total_count,
            "counter_config": config,
        }
    return controls


def verify_capture_root(capture_root: str | Path, output: str | Path, *,
                        capture_specs: Iterable[CaptureSpec | dict[str, Any]] = CAPTURE_SPECS
                        ) -> dict[str, Any]:
    """Verify and write a new result directory, or write a non-pass receipt."""
    specs = tuple(_spec_from_mapping(item) for item in capture_specs)
    repo_root = Path(__file__).resolve().parents[2]
    root = Path(capture_root).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    _validate_output_path(root, output_path)
    receipt = _base_receipt(repo_root, specs)
    receipt["capture_root"] = str(root)
    if not root.is_dir():
        receipt["status"] = "NOT_RUN_MISSING_INPUT"
        receipt["missing_inputs"] = [
            {**_expected_record(spec), "missing_paths": [
                str(root / spec.directory / "camera_gray_only.npz"),
            ]} for spec in specs
        ]
        _write_receipt(output_path, receipt)
        return receipt

    missing = _capture_paths(root, specs)
    if missing:
        receipt["status"] = "NOT_RUN_MISSING_INPUT"
        receipt["missing_inputs"] = missing
        _write_receipt(output_path, receipt)
        return receipt

    try:
        loaded = [_load_capture(root / spec.directory, spec) for spec in specs]
    except (OSError, ValueError) as exc:
        receipt["status"] = "NOT_RUN_INPUT_INVALID"
        receipt["errors"] = [str(exc)]
        _write_receipt(output_path, receipt)
        return receipt

    capture_reports = []
    preview_files: dict[str, bytes] = {}
    input_records = []
    try:
        for spec, data in zip(specs, loaded, strict=True):
            input_records.append({
                "name": spec.name, "archive": str(data["archive_path"]),
                "archive_sha256": data["archive_sha256"],
                "manifest": str(data["manifest_path"]),
                "manifest_sha256": data["manifest_sha256"],
                "frame_window": [0, spec.frame_stop],
            })
            report = replay_gray_frames(data["gray"], data["metadata"], spec)
            capture_reports.append({key: value for key, value in report.items() if key != "previews"})
            for frame_id, png_bytes in report["previews"].items():
                preview_files[f"{spec.name}-frame-{frame_id:04d}.png"] = png_bytes
        single_controls = run_single_nut_controls()
    except ReplayValidationError as exc:
        receipt["status"] = "FAIL_EXPECTATION_MISMATCH"
        receipt["errors"] = [str(exc)]
        if exc.report is not None:
            capture_reports.append({key: value for key, value in exc.report.items()
                                    if key != "previews"})
            for frame_id, png_bytes in exc.report["previews"].items():
                preview_files[f"{exc.report['name']}-frame-{frame_id:04d}.png"] = png_bytes
        receipt["completed_capture_reports"] = capture_reports
        if input_records:
            receipt["verified_inputs"] = input_records
        _write_receipt(output_path, receipt, preview_files)
        return receipt
    except (OSError, ValueError, RuntimeError) as exc:
        receipt["status"] = "FAIL_REPLAY_ERROR"
        receipt["errors"] = [str(exc)]
        receipt["completed_capture_reports"] = capture_reports
        if input_records:
            receipt["verified_inputs"] = input_records
        _write_receipt(output_path, receipt)
        return receipt

    receipt.update({
        "status": "OFFLINE_REPLAY_MATCH",
        "input_scope": "CAMERA_ONLY_NOT_PL_BINARY8",
        "threshold_semantics": "Gray8 >= 128 becomes Binary8 0xFF; otherwise 0x00",
        "counter_config": _profile_configs()[1],
        "verified_inputs": input_records,
        "captures": capture_reports,
        "single_nut_controls": single_controls,
        "visualization_note": "Overlays render source Gray8, threshold mask PIP, live mock telemetry, and actual counter tracks.",
    })
    for report in receipt["captures"]:
        report["previews"] = [
            f"overlays/{report['name']}-frame-{frame_id:04d}.png"
            for frame_id in report["preview_frames"]
        ]
    _write_receipt(output_path, receipt, preview_files)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", required=True, type=Path,
                        help="directory containing the three hash-pinned camera-only captures")
    parser.add_argument("--output", required=True, type=Path,
                        help="new output directory; existing paths are never replaced")
    args = parser.parse_args(argv)
    try:
        receipt = verify_capture_root(args.capture_root, args.output)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({key: receipt[key] for key in (
        "status", "capture_root", "hardware_actions", "physical_accuracy",
    )}, ensure_ascii=False))
    return 0 if receipt["status"] == "OFFLINE_REPLAY_MATCH" else 2


if __name__ == "__main__":
    raise SystemExit(main())
