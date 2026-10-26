"""WO-0016 continuous camera-to-DMA stream counter entry point.

Default mode is a host-only synthetic simulation. Physical access requires the
exact one-shot switches, a hash-pinned WO-0015 50 MHz manifest, and a fresh
board-side preflight. This module never downloads/reloads the Overlay.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import hmac
import itertools
import json
import math
import os
import queue
import re
import stat as stat_module
import sys
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping

import cv2
import numpy as np
from scale_parameters import (DEFAULT_THRESHOLD, REFERENCE_AREA, SOURCE_MIN_NET_AREA,
                              derive_lighting_parameters, validate_threshold)
from live_calibration import (LIGHTING_MAPPINGS, require_standard_camera_controls,
                              frame_geometry, resize_monochrome_into,
                              is_lighting_mapping)

def resolve_wo15_dependency_dir(script_dir: Path) -> Path:
    """Prefer the isolated dependency bundle deployed alongside this runner."""
    bundled = script_dir / "wo0015"
    if (bundled / "board_once.py").is_file():
        return bundled
    return script_dir.parent / "wo0015"


SCRIPT_DIR = Path(__file__).resolve().parent
WO15_DIR = resolve_wo15_dependency_dir(SCRIPT_DIR)
for _path in (SCRIPT_DIR, WO15_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import board_once
import board_preflight as preflight
from camera_adapter import gray320x240_to_rgb_packet
from packet_encoder import gray320x240_to_rgb_packet_into
from dashboard_state import DashboardState
from mjpeg_preview import (
    PREVIEW_FRAME_INTERVAL,
    PREVIEW_JPEG_QUALITY,
    MjpegFrameHub,
    MjpegLoopbackClient,
    MjpegPreviewServer,
    PeriodicMjpegPublisher,
    render_preview_jpeg,
)
from capture_dataset import (
    CaptureError, CaptureWriter, DEFAULT_MAX_BYTES, preflight_capture_target,
)
from stream_counter import (
    HEIGHT,
    RX_BYTES,
    TX_BYTES,
    WIDTH,
    BufferState,
    DoubleBufferStream,
    FrameInput,
    MaskCounter,
    allocate_ping_pong_buffers,
)
import lighting_profile
import physical_calibration_lock as calibration_lock
from calibration_runtime_gate import DEFAULT_WORKER_PYTHON

WO16_STAGE = "wo0016-stream"
WO17_CALIBRATION_STAGE = "wo0017-physical-calibration"
WO16_GUARD = Path("/var/lib/wo0016/stream-counter-consumed.json")
STAGEB2_READY_GUARD_DIR = Path("/var/lib/wo0016")
STAGEB2_ATTEMPT_DIR = STAGEB2_READY_GUARD_DIR / "attempts"
STAGEB2_ATTEMPT_STAGE = "wo0016-stageB2-attempt"
STAGEB2_VALID_FRAMES = 165
STAGEB2_EXPECTED_COUNT = 5
STAGEB2_MATERIAL_LABEL = "M6_nut_no_washer"
STAGEB2_STABILIZATION_DELAY_S = 3.0
STAGEB2_OPERATOR_PREARM_DELAY_S = 15.0
STAGEB2_THRESHOLD = 128
EXPECTED_FRAME_DMASR = 0x00001002
CMA_WATERMARK_KIB = 32768
PHYSICAL_CALIBRATION_SOURCE_FRAME_IDS = (
    0, 1, 2, 3, 4, 5, 10, 20, 30, 60, 90, 120, 150, 180, 210, 240, 270, 299,
)
AFI1_EXPECTED_READBACK = {
    "AFI1_RDCHAN_CTRL": 0x00000001,
    "AFI1_WRCHAN_CTRL": 0x00000F01,
}


def build_synthetic_masks(*, frame_count: int = 8, line_y: int = 120,
                           direction: int = 1) -> tuple[np.ndarray, ...]:
    """Build two separated moving objects plus a sub-threshold noise blob."""
    if isinstance(frame_count, bool) or not isinstance(frame_count, int) or frame_count < 5:
        raise ValueError("frame_count must be at least five for the synthetic crossing sequence")
    if isinstance(line_y, bool) or not isinstance(line_y, int) or not 44 <= line_y <= HEIGHT - 45:
        raise ValueError("synthetic line_y must leave a 44-pixel margin in the frame")
    if direction not in (-1, 1):
        raise ValueError("direction must be +1 or -1")
    ys = np.rint(np.linspace(line_y - 44 * direction,
                             line_y + 44 * direction, frame_count)).astype(np.int32)
    masks = []
    for center_y in ys:
        mask = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
        for center_x in (68, 252):
            mask[center_y - 3:center_y + 4, center_x - 3:center_x + 4] = 0xFF
        mask[line_y - 1:line_y + 2, 154:157] = 0xFF  # 9 px: below min_area=12
        masks.append(mask)
    return tuple(masks)


def run_mock_simulation(*, frame_count: int = 8, line_y: int = 120,
                        direction: int = 1, min_area: int = 12,
                        expected_count: int | None = None,
                        material_label: str | None = None,
                        counter_config: Mapping[str, Any] | None = None,
                        preprocessing_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Run synthetic Binary8 masks through the actual counter and JPEG renderer."""
    counter = (MaskCounter(**dict(counter_config)) if counter_config is not None else
               MaskCounter(line_y=line_y, direction=direction, min_area=min_area, max_step=24.0))
    masks = build_synthetic_masks(frame_count=frame_count, line_y=line_y, direction=direction)
    if counter_config is not None:
        masks = []
        positions = np.rint(np.linspace(96, 224, frame_count)).astype(int)
        if counter.direction < 0:
            positions = positions[::-1]
        for x in positions:
            mask = np.zeros((HEIGHT, WIDTH), np.uint8)
            for y in ((45,75,105,155,190) if counter.roi_top == 30 else (25,65,105,155,214)):
                mask[y-5:y+6, x-5:x+6] = 255
            masks.append(mask)
    hub = MjpegFrameHub()
    preview = MjpegPreviewServer(hub=hub, host="127.0.0.1", port=0)
    results = []
    start = time.perf_counter()
    preview.start()
    try:
        last_jpeg = None
        for frame_id, mask in enumerate(masks):
            timing = ({} if counter.motion_timebase_s is None else
                      {"capture_monotonic_ns": max(1, round((frame_id + 1) * counter.motion_timebase_s * 1e9))})
            result = counter.update(frame_id, mask, **timing)
            last_jpeg = render_preview_jpeg(mask, total_count=result.total_count,
                                            line_y=counter.line_y, count_axis=counter.count_axis,
                                            line_x=counter.line_x, hysteresis=counter.hysteresis)
            hub.publish(last_jpeg)
            results.append(result)
    finally:
        preview.close()
    elapsed = max(time.perf_counter() - start, 1e-9)
    predicted = results[-1].total_count if results else 0
    # Synthetic ground truth is kept outside MaskCounter and is used only for scoring.
    ground_truth = ((5 if counter_config is not None else 2)
                    if expected_count is None else expected_count)
    summary = {
        "schema": 1,
        "work_order": "WO-0016",
        "status": "MOCK_PASS" if predicted == ground_truth else "MOCK_COUNT_MISMATCH",
        "mode": "offline-synthetic",
        "board_accessed": False,
        "pynq_imported": False,
        "frame_geometry": [HEIGHT, WIDTH],
        "line_y": line_y,
        "min_area": counter.min_area,
        "frames_processed": len(results),
        "total_count": predicted,
        "ground_truth_count": ground_truth,
        "expected_count": ground_truth,
        "material_label": material_label,
        "count_error": predicted - ground_truth,
        "accuracy_percent": _count_accuracy(predicted, ground_truth),
        "accuracy": _count_accuracy_fraction(predicted, ground_truth),
        "roi_subtimings_ms": summarize_roi_subtimings_ms(results),
        "elapsed_seconds": elapsed,
        "throughput_fps": len(results) / elapsed,
        "preview_jpeg_sha256": hashlib.sha256(last_jpeg).hexdigest()
        if results else None,
        "hardware_fps": None,
        "note": "Synthetic Offline evidence only; it is not PYNQ, camera, conveyor, or board acceptance.",
    }
    if counter_config is not None:
        profile = "lighting-calibrated" if counter.roi_top == 30 else "full-width-mixed"
        summary.update({"counting_profile": profile,
                        "counting_config": dict(counter_config),
                        "preprocessing_config": (dict(preprocessing_config) if preprocessing_config is not None else
                            frame_geometry("lighting-calibrated" if profile == "lighting-calibrated" else "letterbox"))})
    return summary


def claim_once_guard(path: str | Path, identity: Mapping[str, Any]) -> dict[str, Any]:
    """Atomically consume a one-shot guard; an existing guard is never reset."""
    if not isinstance(identity, Mapping) or not identity:
        raise ValueError("guard identity must be a non-empty mapping")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    receipt = {**dict(identity), "consumed": True}
    fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(receipt, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            directory_fd = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except BaseException:
        # O_EXCL has consumed the marker; leave it in place after a write error.
        raise
    return receipt


def validate_non_consuming_guard(path: str | Path,
                                 identity: Mapping[str, Any], *,
                                 expected_uid: int = 0) -> dict[str, Any]:
    """Validate an armed Stage B-2 record by reading it without modifying it."""
    if not isinstance(identity, Mapping) or not identity:
        raise ValueError("stage B-2 READY guard identity must be non-empty")
    guard, raw = _read_private_json_record(
        path, label="stage B-2 READY guard", expected_uid=expected_uid
    )
    expected = {**dict(identity), "guard_status": "READY", "consumed": False}
    if not isinstance(guard, dict) or guard != expected:
        raise RuntimeError("stage B-2 READY guard identity/status does not match this run")
    return {"guard": guard, "sha256": hashlib.sha256(raw).hexdigest()}


def _read_private_json_record(path: str | Path, *, label: str,
                              expected_uid: int) -> tuple[dict[str, Any], bytes]:
    target = Path(path)
    try:
        parent_stat = target.parent.stat(follow_symlinks=False)
        before = target.stat(follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError(f"{label} is unavailable: {exc}") from exc
    if os.name == "posix" and (
            not stat_module.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != expected_uid
            or stat_module.S_IMODE(parent_stat.st_mode) & 0o077):
        raise RuntimeError(f"{label} directory must be private and owner-controlled")
    if (target.is_symlink() or not stat_module.S_ISREG(before.st_mode)
            or (os.name == "posix" and (before.st_uid != expected_uid
                                        or stat_module.S_IMODE(before.st_mode) != 0o600))):
        raise RuntimeError(f"{label} must be a private owner-controlled regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(target), flags)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                    or (os.name == "posix" and (opened.st_uid != expected_uid
                                                or stat_module.S_IMODE(opened.st_mode) != 0o600))):
                raise RuntimeError(f"{label} changed while it was being opened")
            raw = stream.read(8193)
            if len(raw) > 8192:
                raise RuntimeError(f"{label} exceeds its size limit")
    except OSError as exc:
        raise RuntimeError(f"{label} could not be read safely: {exc}") from exc
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(record, dict):
        raise RuntimeError(f"{label} must contain a JSON object")
    return record, raw


def stageb2_guard_identity(*, boot_id: str, manifest_sha256: str,
                           bit_sha256: str, hwh_sha256: str,
                           max_frames: int, expected_count: int | None,
                           material_label: str | None,
                           stabilization_delay_s: float) -> dict[str, Any]:
    """Require the fixed five-nut, 165-frame profile for no-consume Guard mode."""
    if (max_frames != STAGEB2_VALID_FRAMES
            or expected_count != STAGEB2_EXPECTED_COUNT
            or material_label != STAGEB2_MATERIAL_LABEL
            or stabilization_delay_s != STAGEB2_STABILIZATION_DELAY_S):
        raise ValueError(
            "--no-consume-guard is restricted to Stage B-2: 165 frames, "
            "3 seconds, target 5, M6_nut_no_washer"
        )
    return {
        "schema": 1,
        "work_order": "WO-0016",
        "stage": "wo0016-stageB2-nuts",
        "boot_id": boot_id,
        "manifest_sha256": manifest_sha256,
        "bit_sha256": bit_sha256,
        "hwh_sha256": hwh_sha256,
        "max_frames": max_frames,
        "expected_count": expected_count,
        "material_label": material_label,
        "stabilization_delay_s": stabilization_delay_s,
        "threshold": STAGEB2_THRESHOLD,
    }


def counting_configuration(args: argparse.Namespace) -> dict[str, Any]:
    config = {"count_axis": args.count_axis, "line_x": args.line_x,
              "line_y": args.line_y, "direction": args.direction,
              "min_area": args.min_area, "max_area": args.max_area,
              "max_bbox_width": args.max_bbox_width, "max_bbox_height": args.max_bbox_height,
              "max_step": args.max_step, "max_lateral_step": args.max_lateral_step,
              "hysteresis": args.hysteresis, "confirm_hits": args.confirm_hits,
              "component_cluster_dx": args.component_cluster_dx,
              "component_cluster_dy": args.component_cluster_dy,
              "component_cluster_gap": args.component_cluster_gap,
              "cooldown_frames": args.cooldown_frames,
              "cooldown_lateral": args.cooldown_lateral,
              "edge_max_missing": args.edge_max_missing,
              "prediction_max_missing": args.prediction_max_missing}
    if getattr(args, "counting_profile", "legacy") in ("full-width-mixed", "lighting-calibrated"):
        config.update({"roi_mode": "full", "guarded_merge": True,
                       "single_intact_area": 95, "max_merge_area": 260,
                       "max_split_area": 900, "max_merged_span": 24,
                       "strict_lateral_gate": True, "count_axis": "x", "line_x": 160,
                       "line_y": 120, "min_area": 12, "max_missing": 3,
                       "max_step": 48.0, "max_lateral_step": 10.0, "backward_tolerance": 4.0,
                       "component_cluster_gap": 14.0, "component_cluster_dx": 0.0,
                       "confirm_hits": 2, "prediction_max_missing": 3,
                       "edge_max_missing": 0, "cooldown_frames": 0})
    if getattr(args, "counting_profile", "legacy") == "lighting-calibrated":
        validate_threshold(getattr(args, "lighting_threshold", DEFAULT_THRESHOLD))
        parameters = derive_lighting_parameters(getattr(args, "reference_area", REFERENCE_AREA))
        parameters.pop("source_min_net_area")  # Source contours use their own area metric.
        if parameters["split_min_hole_area"] != 16:
            parameters["split_hole_area_cap"] = parameters["split_min_hole_area"]
        # Preserve the exact recorded default dictionary; the splitter's API default is 10.
        if parameters["split_min_hole_distance"] == 10:
            parameters.pop("split_min_hole_distance")
        # Explicit CLI pixel overrides retain their original meaning.
        for key, default in (("max_bbox_width", 85), ("max_bbox_height", 85), ("hysteresis", 2.0)):
            if getattr(args, key) != default or key in getattr(args, "_pixel_overrides", ()):
                parameters[key] = getattr(args, key)
        config.update(parameters, roi_top=30, roi_bottom=210, component_cluster_gap=0.0)
    elif (getattr(args, "reference_area", REFERENCE_AREA) != REFERENCE_AREA
          or getattr(args, "lighting_threshold", DEFAULT_THRESHOLD) != DEFAULT_THRESHOLD):
        raise ValueError("reference area and lighting threshold require lighting-calibrated profile")
    if getattr(args, "motion_timebase_s", None) is not None:
        config["motion_timebase_s"] = args.motion_timebase_s
    MaskCounter(**config)  # Validate before consuming the one-shot attempt.
    return config


def preprocessing_configuration(args: argparse.Namespace) -> dict[str, Any]:
    profile = getattr(args, "counting_profile", "legacy")
    if profile == "lighting-calibrated":
        area_filter = getattr(args, "lighting_area_filter", "source")
        if area_filter not in ("source", "output-roi"):
            raise ValueError("lighting area filter must be source or output-roi")
        mapping = ("lighting-calibrated" if area_filter == "source" else
                   "lighting-calibrated-output-roi")
    else:
        if getattr(args, "lighting_area_filter", "source") != "source":
            raise ValueError("--lighting-area-filter applies only to lighting-calibrated profile")
        mapping = "letterbox" if profile == "full-width-mixed" else "stretch"
    return frame_geometry(mapping, reference_area=getattr(args, "reference_area", REFERENCE_AREA),
                          threshold=getattr(args, "lighting_threshold", DEFAULT_THRESHOLD))


def effective_threshold(args):
    return (validate_threshold(getattr(args, "lighting_threshold", DEFAULT_THRESHOLD))
            if args.counting_profile == "lighting-calibrated" else STAGEB2_THRESHOLD)


def packet_encoders(args):
    """Bind one photometric threshold to both packet encoder variants for a run."""
    if args.counting_profile == "lighting-calibrated":
        threshold = effective_threshold(args)
        return lighting_profile.make_packet_encoders(threshold=threshold)
    return gray320x240_to_rgb_packet, gray320x240_to_rgb_packet_into


def counting_roi_metadata(result: Any, counter: MaskCounter) -> dict[str, Any]:
    return {"top": result.roi_top, "bottom_exclusive": result.roi_bottom,
            "line_y": counter.line_y, "line_x": counter.line_x,
            "count_axis": counter.count_axis}


def stageb2_attempt_permit_identity(*, ready_identity: Mapping[str, Any],
                                    ready_guard_sha256: str,
                                    attempt_id: str,
                                    operator_prearm_delay_s: float,
                                    runner_sha256: str,
                                    gate_launcher_sha256: str,
                                    counting_config: Mapping[str, Any] | None = None,
                                    counter_sha256: str | None = None,
                                    capture_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bind one named Stage B-2 invocation to the unchanged READY Guard."""
    if (not isinstance(attempt_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,32}", attempt_id, flags=re.ASCII) is None):
        raise ValueError("attempt id must contain 1-32 ASCII letters, digits, '_' or '-'")
    if (isinstance(operator_prearm_delay_s, bool)
            or not isinstance(operator_prearm_delay_s, (int, float))
            or not math.isfinite(float(operator_prearm_delay_s))
            or float(operator_prearm_delay_s) != STAGEB2_OPERATOR_PREARM_DELAY_S):
        raise ValueError(
            f"Stage B-2 Attempt #2 requires a {STAGEB2_OPERATOR_PREARM_DELAY_S:g}-second operator pre-arm window"
        )
    if not isinstance(ready_identity, Mapping) or not ready_identity:
        raise ValueError("stage B-2 READY identity must be non-empty")
    if not re.fullmatch(r"[0-9a-f]{64}", ready_guard_sha256):
        raise ValueError("READY Guard SHA-256 must be lowercase hexadecimal")
    if not re.fullmatch(r"[0-9a-f]{64}", runner_sha256):
        raise ValueError("runner SHA-256 must be lowercase hexadecimal")
    if not re.fullmatch(r"[0-9a-f]{64}", gate_launcher_sha256):
        raise ValueError("operator gate launcher SHA-256 must be lowercase hexadecimal")
    extra = {}
    if counting_config is not None:
        MaskCounter(**dict(counting_config))
        if counter_sha256 is None or not re.fullmatch(r"[0-9a-f]{64}", counter_sha256):
            raise ValueError("counting configuration requires counter SHA-256")
        extra = {"counting_config": dict(counting_config), "counter_sha256": counter_sha256}
    if capture_config is not None:
        capture_value = dict(capture_config)
        output_directory = capture_value.get("output_directory")
        max_bytes = capture_value.get("max_bytes")
        if (capture_value.get("enabled") is not True
                or not isinstance(output_directory, str)
                or not Path(output_directory).is_absolute()
                or isinstance(max_bytes, bool) or not isinstance(max_bytes, int)
                or max_bytes < 1 or capture_value.get("queue_depth") != 2):
            raise ValueError("capture configuration must bind an absolute path, capacity and queue depth")
        extra["capture_config"] = capture_value
    return {
        **dict(ready_identity),
        "stage": STAGEB2_ATTEMPT_STAGE,
        "ready_guard_sha256": ready_guard_sha256,
        "attempt_id": attempt_id,
        "operator_prearm_delay_s": float(operator_prearm_delay_s),
        "runner_sha256": runner_sha256,
        "gate_launcher_sha256": gate_launcher_sha256,
        **extra,
    }


def validate_stageb2_attempt_permit(path: str | Path,
                                    identity: Mapping[str, Any], *,
                                    expected_uid: int = 0) -> dict[str, Any]:
    """Read and verify a private, unconsumed permit without modifying it."""
    if not isinstance(identity, Mapping) or not identity:
        raise ValueError("stage B-2 attempt permit identity must be non-empty")
    permit, raw = _read_private_json_record(
        path, label="stage B-2 attempt permit", expected_uid=expected_uid
    )
    expected = {**dict(identity), "permit_status": "AUTHORIZED", "consumed": False}
    if permit != expected:
        raise RuntimeError("stage B-2 attempt permit identity/status does not match this run")
    return {"permit": permit, "sha256": hashlib.sha256(raw).hexdigest()}


def _emit_milestone(name: str, monotonic_value: float, log_path: str | Path, *,
                    boot_id: str) -> dict[str, Any]:
    """Write a flushed operator-visible marker and durable JSONL timestamp."""
    record = {
        "boot_id": boot_id,
        "milestone": name,
        "monotonic": float(monotonic_value),
    }
    print(f"[MILESTONE] {name} monotonic={float(monotonic_value):.6f}", flush=True)
    target = Path(log_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return record


def _countdown_wait(duration_s: float, *, label: str) -> None:
    """Wait against monotonic time, flushing each whole-second countdown tick."""
    if (isinstance(duration_s, bool) or not isinstance(duration_s, (int, float))
            or not math.isfinite(float(duration_s)) or duration_s < 0):
        raise ValueError("countdown duration must be finite and nonnegative")
    duration = float(duration_s)
    if duration == 0:
        return
    deadline = time.monotonic() + duration
    while True:
        remaining = max(0, math.ceil(deadline - time.monotonic()))
        if remaining <= 0:
            return
        print(f"[{label}] {remaining} seconds remaining", flush=True)
        next_tick = deadline - (remaining - 1)
        time.sleep(max(0.0, min(1.0, next_tick - time.monotonic())))


def verify_frame_dmasr(api: Any, frame_id: int,
                       records: list[dict[str, Any]]) -> None:
    """Record and strictly validate both AXI DMA status registers per frame."""
    mm2s = api.read32(preflight.DMA_BASE, 0x04)
    s2mm = api.read32(preflight.DMA_BASE, 0x34)
    records.append({"frame_id": frame_id, "mm2s": f"0x{mm2s:08x}",
                    "s2mm": f"0x{s2mm:08x}"})
    if mm2s != EXPECTED_FRAME_DMASR or s2mm != EXPECTED_FRAME_DMASR:
        raise RuntimeError(
            f"frame {frame_id} DMA status mismatch: "
            f"MM2S=0x{mm2s:08x}, S2MM=0x{s2mm:08x}"
        )


def require_board_opt_in(*, execute: bool, authorize_stage: str | None,
                         approve_once: bool, manifest: str | None,
                         manifest_sha256: str | None, max_frames: int,
                         evidence_json: str | None,
                         calibration: bool = False) -> None:
    expected_stage = WO17_CALIBRATION_STAGE if calibration else WO16_STAGE
    if execute is not True or approve_once is not True or authorize_stage != expected_stage:
        raise ValueError(
            f"board mode requires --execute --authorize-stage {expected_stage} --approve-once"
        )
    if not manifest or not manifest_sha256:
        raise ValueError("board mode requires a WO-0015 50 MHz manifest and its reviewed SHA-256")
    if max_frames < 1:
        raise ValueError("board mode requires a finite positive --max-frames limit")
    if not evidence_json or not Path(evidence_json).is_absolute():
        raise ValueError("board mode requires an absolute --evidence-json path")
    if Path(evidence_json).exists():
        raise FileExistsError("evidence path already exists; choose a new result path")


async def _daemon_call_with_timeout(function: Any, *, timeout_s: float,
                                    thread_name: str) -> Any:
    """Await a blocking device call without letting a stuck call block asyncio shutdown."""
    loop = asyncio.get_running_loop()
    completed = loop.create_future()

    def finish(value: Any = None, error: BaseException | None = None) -> None:
        if completed.done():
            return
        if error is None:
            completed.set_result(value)
        else:
            completed.set_exception(error)

    def invoke() -> None:
        try:
            value = function()
            try:
                loop.call_soon_threadsafe(finish, value, None)
            except RuntimeError:
                pass
        except BaseException as exc:
            try:
                loop.call_soon_threadsafe(finish, None, exc)
            except RuntimeError:
                pass

    threading.Thread(target=invoke, name=thread_name, daemon=True).start()
    return await asyncio.wait_for(completed, timeout=timeout_s)


async def _camera_gray_frames_impl(device: str, *, max_frames: int | None,
                                   cv2_module: Any = cv2,
                                   read_timeout_s: float = 5.0,
                                   stabilization_delay_s: float = 0.0,
                                   operator_prearm_delay_s: float = 0.0,
                                   operator_gate: Any | None = None,
                                   milestone_callback: Callable[[str, float], None] | None = None,
                                   capture_timings_ms: list[float] | None = None,
                                   mapping_timings_ms: list[float] | None = None,
                                   steady_start_time: list[float] | None = None,
                                   capture_metadata: dict[str, Any] | None = None,
                                   camera_controls_callback: Callable[[dict[str, Any]], None] | None = None,
                                   include_capture_metadata: bool = False,
                                   source_frame_ids: tuple[int, ...] = (),
                                   frame_mapping: str = "stretch",
                                   binary_threshold: int = DEFAULT_THRESHOLD,
                                   reference_area: float = REFERENCE_AREA,
                                   live_stop_requested: Callable[[], bool] | None = None
                                   ) -> AsyncIterator[np.ndarray | FrameInput]:
    """Internal capture loop; board callers must use camera_gray_frames()."""
    geometry = frame_geometry(frame_mapping, reference_area=reference_area, threshold=binary_threshold)
    continuous = max_frames is None and callable(live_stop_requested)
    if live_stop_requested is not None and not callable(live_stop_requested):
        raise ValueError("live_stop_requested must be callable")
    if camera_controls_callback is not None and not callable(camera_controls_callback):
        raise ValueError("camera_controls_callback must be callable")
    if not continuous and (isinstance(max_frames, bool)
                           or not isinstance(max_frames, int) or max_frames < 1):
        raise ValueError("max_frames must be a positive integer")
    if (not isinstance(source_frame_ids, tuple)
            or any(isinstance(frame_id, bool) or not isinstance(frame_id, int)
                   or max_frames is None or not 0 <= frame_id < max_frames
                   for frame_id in source_frame_ids)
            or len(set(source_frame_ids)) != len(source_frame_ids)):
        raise ValueError("source_frame_ids must be unique integer indexes within max_frames")
    if (isinstance(read_timeout_s, bool) or not isinstance(read_timeout_s, (int, float))
            or not math.isfinite(float(read_timeout_s)) or read_timeout_s <= 0):
        raise ValueError("read_timeout_s must be finite and positive")
    if (isinstance(stabilization_delay_s, bool)
            or not isinstance(stabilization_delay_s, (int, float))
            or not math.isfinite(float(stabilization_delay_s))
            or stabilization_delay_s < 0):
        raise ValueError("stabilization_delay_s must be finite and nonnegative")
    if (isinstance(operator_prearm_delay_s, bool)
            or not isinstance(operator_prearm_delay_s, (int, float))
            or not math.isfinite(float(operator_prearm_delay_s))
            or operator_prearm_delay_s < 0):
        raise ValueError("operator_prearm_delay_s must be finite and nonnegative")

    def emit_milestone(name: str, at: float | None = None) -> float:
        timestamp = time.monotonic() if at is None else float(at)
        if milestone_callback is not None:
            milestone_callback(name, timestamp)
        return timestamp

    capture = cv2_module.VideoCapture(device, cv2_module.CAP_V4L2)
    failed = False
    primary_error: BaseException | None = None
    released = False
    release_attempted = False
    loop: asyncio.AbstractEventLoop | None = None
    tasks: queue.Queue | None = None
    worker: threading.Thread | None = None
    pending_waiter: asyncio.Future | None = None
    stop_worker = object()

    async def release_camera() -> BaseException | None:
        nonlocal released, release_attempted
        if release_attempted:
            return None
        release_attempted = True
        try:
            await _daemon_call_with_timeout(
                capture.release, timeout_s=float(read_timeout_s),
                thread_name="wo0016-camera-release",
            )
        except BaseException as exc:
            return exc
        released = True
        return None

    try:
        if not capture.isOpened():
            raise RuntimeError(f"cannot open V4L2 camera device {device}")
        camera_open_monotonic = emit_milestone("CAMERA_OPENED")
        if capture_metadata is not None:
            capture_metadata["camera_open_monotonic"] = camera_open_monotonic
        requested_fourcc = cv2_module.VideoWriter_fourcc(*"MJPG")
        requested = (
            (cv2_module.CAP_PROP_FOURCC, requested_fourcc),
            (cv2_module.CAP_PROP_FRAME_WIDTH, 640),
            (cv2_module.CAP_PROP_FRAME_HEIGHT, 400),
            (cv2_module.CAP_PROP_FPS, 60),
        )
        if any(not capture.set(prop, value) for prop, value in requested):
            raise RuntimeError("V4L2 rejected the configured MJPG 640x400 60 FPS capture mode")
        buffer_size_property = getattr(cv2_module, "CAP_PROP_BUFFERSIZE", None)
        if (buffer_size_property is not None
                and not capture.set(buffer_size_property, 1)):
            raise RuntimeError("V4L2 rejected the single-frame camera buffer setting")
        actual_fourcc = int(round(capture.get(cv2_module.CAP_PROP_FOURCC)))
        actual_format = "".join(chr((actual_fourcc >> (8 * byte)) & 0xFF)
                                for byte in range(4))
        actual_width = int(round(capture.get(cv2_module.CAP_PROP_FRAME_WIDTH)))
        actual_height = int(round(capture.get(cv2_module.CAP_PROP_FRAME_HEIGHT)))
        if (actual_format != "MJPG" or actual_width != 640 or actual_height != 400):
            raise RuntimeError(
                "V4L2 negotiated "
                f"{actual_format!r} {actual_width}x{actual_height}; expected MJPG 640x400"
            )
        actual_fps = float(capture.get(cv2_module.CAP_PROP_FPS))
        if not math.isfinite(actual_fps) or actual_fps < 50.0:
            raise RuntimeError(
                f"V4L2 negotiated {actual_format!r} {actual_width}x{actual_height} "
                f"fps {actual_fps}; at least 50 fps is required for the steady stream"
            )
        applied_camera_controls = None
        if is_lighting_mapping(frame_mapping):
            applied_camera_controls = lighting_profile.apply_camera_controls(device)
        def read_gray_frame(*, warmup: bool,
                            source_frame_id: int | None = None
                            ) -> tuple[np.ndarray, float, int, int, np.ndarray | None]:
            capture_started = time.perf_counter()
            ok, frame = capture.read()
            if not ok or frame is None:
                prefix = "V4L2 warm-up" if warmup else "V4L2"
                raise RuntimeError(f"{prefix} returned no camera frame")
            capture_monotonic_ns = time.monotonic_ns() if include_capture_metadata else 0
            capture_wall_time_ns = time.time_ns() if include_capture_metadata else 0
            image = np.asarray(frame)
            if warmup:
                return (image, (time.perf_counter() - capture_started) * 1000.0,
                        capture_monotonic_ns, capture_wall_time_ns, None)
            if image.ndim == 3 and image.shape[2] in (1, 3, 4):
                image = image[:, :, 0]
            if image.dtype != np.uint8 or image.shape != (400, 640):
                raise RuntimeError(f"camera frame geometry is {image.shape}; expected (400, 640)")
            source_gray = (np.array(image, copy=True, order="C")
                           if source_frame_id in source_frame_ids else None)
            mapping_started = time.perf_counter()
            if frame_mapping in ("letterbox", *LIGHTING_MAPPINGS):
                # A fresh buffer is required: the next read is prefetched before yield.
                gray = np.empty((HEIGHT, WIDTH), np.uint8)
                resize_monochrome_into(image, gray, mapping=frame_mapping,
                                       threshold=binary_threshold,
                                       source_min_net_area=geometry.get("source_min_net_contour_area_configured", SOURCE_MIN_NET_AREA))
            else:
                gray = cv2_module.resize(
                    image, (WIDTH, HEIGHT), interpolation=cv2_module.INTER_NEAREST)
            if mapping_timings_ms is not None:
                mapping_timings_ms.append((time.perf_counter() - mapping_started) * 1000.0)
            gray = np.asarray(gray)
            if gray.dtype != np.uint8 or gray.shape != (HEIGHT, WIDTH):
                raise RuntimeError(
                    f"resized camera frame is {gray.dtype} {gray.shape}; "
                    f"expected uint8 {(HEIGHT, WIDTH)}"
                )
            return (gray, (time.perf_counter() - capture_started) * 1000.0,
                    capture_monotonic_ns, capture_wall_time_ns, source_gray)

        loop = asyncio.get_running_loop()
        tasks = queue.Queue(maxsize=1)

        def settle_waiter(waiter: asyncio.Future, value: Any = None,
                          error: BaseException | None = None) -> None:
            if waiter.done():
                return
            if error is None:
                waiter.set_result(value)
            else:
                waiter.set_exception(error)

        def worker_main() -> None:
            while True:
                task = tasks.get()
                if task is stop_worker:
                    return
                warmup, waiter, source_frame_id = task
                try:
                    value = read_gray_frame(warmup=warmup,
                                            source_frame_id=source_frame_id)
                except BaseException as exc:
                    try:
                        loop.call_soon_threadsafe(settle_waiter, waiter, None, exc)
                    except RuntimeError:
                        return
                else:
                    try:
                        loop.call_soon_threadsafe(settle_waiter, waiter, value, None)
                    except RuntimeError:
                        return

        worker = threading.Thread(
            target=worker_main, name="wo0016-camera-prefetch", daemon=True
        )
        worker.start()

        async def get_frame(*, warmup: bool
                             ) -> tuple[np.ndarray, float, int, int, np.ndarray | None]:
            nonlocal pending_waiter
            if pending_waiter is None:
                raise RuntimeError("camera prefetch slot is empty")
            done, _ = await asyncio.wait(
                (pending_waiter,), timeout=float(read_timeout_s)
            )
            if not done:
                prefix = "V4L2 warm-up read" if warmup else "V4L2 camera read"
                raise RuntimeError(f"{prefix} timed out; no retry was attempted")
            frame_result = await pending_waiter
            pending_waiter = None
            return frame_result

        def prefetch(*, warmup: bool, source_frame_id: int | None = None) -> None:
            nonlocal pending_waiter
            if tasks is None or loop is None or pending_waiter is not None:
                raise RuntimeError("camera prefetch slot is not available")
            pending_waiter = loop.create_future()
            tasks.put_nowait((warmup, pending_waiter, source_frame_id))

        warmup_frames = 2
        for _ in range(warmup_frames):
            prefetch(warmup=True)
            await get_frame(warmup=True)
        if is_lighting_mapping(frame_mapping):
            controls = lighting_profile.verify_camera_controls(device)
            if applied_camera_controls is not None:
                controls["gain"]["set_response"] = applied_camera_controls["gain"]["set_response"]
            if capture_metadata is not None:
                capture_metadata["hardware_applied"] = applied_camera_controls["required_controls"]
                capture_metadata["camera_control_status"] = controls
            if camera_controls_callback is not None:
                camera_controls_callback(controls)
        if capture_metadata is not None:
            capture_metadata.update({
                "camera_fps_requested": 60,
                "camera_fps_actual": actual_fps,
                "camera_warmup_frames": warmup_frames,
                "camera_buffer_size": 1 if buffer_size_property is not None else None,
                "camera_prefetch_depth": 1,
                "camera_width": actual_width,
                "camera_height": actual_height,
                "camera_output_width": WIDTH,
                "camera_output_height": HEIGHT,
            })
            if frame_mapping in ("letterbox", *LIGHTING_MAPPINGS):
                capture_metadata["frame_geometry"] = geometry
        emit_milestone("CAMERA_OPEN_READY_FOR_OPERATOR")
        if operator_gate is not None:
            operator_gate.wait_for_operator()
        elif operator_prearm_delay_s > 0:
            raise RuntimeError("operator gate session is required before sampling")
        _countdown_wait(float(operator_prearm_delay_s), label="OPERATOR_PREARM")
        emit_milestone("CAMERA_OPEN_AND_STABILIZING_START")
        _countdown_wait(float(stabilization_delay_s), label="STABILIZATION")
        if steady_start_time is not None:
            steady_start_time.clear()

        frame_indexes = itertools.count() if continuous else range(max_frames)
        for frame_index in frame_indexes:
            if live_stop_requested is not None and live_stop_requested():
                break
            if pending_waiter is None:
                if frame_index == 0:
                    emit_milestone("FRAME_0_CAPTURE_START_FEED_NOW")
                    if steady_start_time is not None:
                        steady_start_time[:] = [time.perf_counter()]
                prefetch(warmup=False, source_frame_id=frame_index)
            (gray, capture_ms, capture_monotonic_ns,
             capture_wall_time_ns, source_gray) = await get_frame(warmup=False)
            if capture_timings_ms is not None:
                capture_timings_ms.append(capture_ms)
            if (continuous or frame_index + 1 < max_frames) and not (
                    live_stop_requested is not None and live_stop_requested()):
                prefetch(warmup=False, source_frame_id=frame_index + 1)
            if include_capture_metadata:
                yield FrameInput(gray, frame_index, capture_monotonic_ns,
                                 capture_wall_time_ns, capture_ms, source_gray)
            else:
                yield gray
    except GeneratorExit:
        raise
    except BaseException as exc:
        failed = True
        primary_error = exc
        raise
    finally:
        cleanup_errors = []
        if pending_waiter is not None and not pending_waiter.done():
            if failed:
                release_error = await release_camera()
                if release_error is not None:
                    cleanup_errors.append(f"camera release failed: {release_error}")
            else:
                done, _ = await asyncio.wait(
                    (pending_waiter,), timeout=float(read_timeout_s)
                )
                if not done:
                    release_error = await release_camera()
                    if release_error is not None:
                        cleanup_errors.append(f"camera release failed: {release_error}")
        if worker is not None and tasks is not None:
            try:
                tasks.put_nowait(stop_worker)
            except queue.Full:
                try:
                    await asyncio.to_thread(
                        tasks.put, stop_worker, True, float(read_timeout_s)
                    )
                except queue.Full:
                    cleanup_errors.append("camera prefetch task slot stayed full at close")
            await asyncio.to_thread(worker.join, float(read_timeout_s))
            if worker.is_alive() and not release_attempted:
                release_error = await release_camera()
                if release_error is not None:
                    cleanup_errors.append(f"camera release failed: {release_error}")
                await asyncio.to_thread(worker.join, float(read_timeout_s))
            if worker.is_alive():
                cleanup_errors.append("camera prefetch worker did not stop after bounded release")

        if pending_waiter is not None and pending_waiter.done():
            try:
                pending_waiter.result()
            except BaseException as exc:
                if exc is not primary_error:
                    cleanup_errors.append(f"prefetched camera read failed during close: {exc}")

        if not release_attempted:
            release_error = await release_camera()
            if release_error is not None:
                cleanup_errors.append(f"camera release failed: {release_error}")

        if cleanup_errors:
            message = "; ".join(cleanup_errors)
            if primary_error is not None and hasattr(primary_error, "add_note"):
                primary_error.add_note(message)
            else:
                raise RuntimeError(message)


async def camera_gray_frames(device: str, *, max_frames: int,
                             operator_gate: Any,
                             cv2_module: Any = cv2,
                             read_timeout_s: float = 5.0,
                             stabilization_delay_s: float = 0.0,
                             operator_prearm_delay_s: float = 0.0,
                             milestone_callback: Callable[[str, float], None] | None = None,
                             capture_timings_ms: list[float] | None = None,
                             mapping_timings_ms: list[float] | None = None,
                             steady_start_time: list[float] | None = None,
                             capture_metadata: dict[str, Any] | None = None,
                             include_capture_metadata: bool = False,
                             source_frame_ids: tuple[int, ...] = (),
                             frame_mapping: str = "stretch",
                             binary_threshold: int = DEFAULT_THRESHOLD,
                             reference_area: float = REFERENCE_AREA
                             ) -> AsyncIterator[np.ndarray | FrameInput]:
    """Yield camera frames only through a live, authenticated operator session."""
    import operator_gate_launcher
    if (not isinstance(operator_gate, operator_gate_launcher.OperatorGate)
            or not operator_gate_launcher.is_registered_operator_gate_session(operator_gate)):
        raise ValueError("camera capture requires a live operator control server session")
    if source_frame_ids and not include_capture_metadata:
        raise ValueError("original source-frame retention requires capture metadata")
    if not is_lighting_mapping(frame_mapping):
        require_standard_camera_controls(device)
    first_frame = True
    async for frame in _camera_gray_frames_impl(
            device, max_frames=max_frames, cv2_module=cv2_module,
            read_timeout_s=read_timeout_s,
            stabilization_delay_s=stabilization_delay_s,
            operator_prearm_delay_s=operator_prearm_delay_s,
            operator_gate=operator_gate, milestone_callback=milestone_callback,
            capture_timings_ms=capture_timings_ms,
            mapping_timings_ms=mapping_timings_ms,
            steady_start_time=steady_start_time,
            capture_metadata=capture_metadata,
            include_capture_metadata=include_capture_metadata,
            source_frame_ids=source_frame_ids, frame_mapping=frame_mapping,
            binary_threshold=binary_threshold, reference_area=reference_area):
        if first_frame:
            (lighting_profile.verify_camera_controls if is_lighting_mapping(frame_mapping)
             else require_standard_camera_controls)(device)
            first_frame = False
        yield frame


def validate_operator_gate_launcher_sha256(value: str | None,
                                           operator_gate: Any | None) -> str:
    """Require the authenticated operator-gate context and its reviewed launcher."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Stage B-2 board mode requires the operator gate launcher")
    import operator_gate_launcher
    if (not isinstance(operator_gate, operator_gate_launcher.OperatorGate)
            or not operator_gate_launcher.is_registered_operator_gate_session(operator_gate)):
        raise ValueError("Stage B-2 board mode requires a live operator control server session")
    gate_path = SCRIPT_DIR / "operator_gate_launcher.py"
    actual = hashlib.sha256(gate_path.read_bytes()).hexdigest()
    if not hmac.compare_digest(value, actual):
        raise RuntimeError("operator gate launcher SHA-256 does not match the current file")
    return actual


def formal_runtime_sha256() -> dict[str, str]:
    names = ("run_stream_counter.py", "operator_gate_launcher.py", "stream_counter.py",
             "split_touching.py", "lighting_profile.py", "scale_parameters.py", "dashboard_state.py", "dashboard.html",
             "capture_dataset.py", "live_calibration.py",
             "packet_encoder.py", "mjpeg_preview.py", "wo0017_capture_profile.py",
             "physical_calibration_lock.py", "prepare_physical_calibration.py",
             "calibration_runtime_gate.py", "start_physical_calibration_standby.py")
    paths = {name: SCRIPT_DIR / name for name in names}
    paths.update({"wo0015/" + name: WO15_DIR / name
                  for name in ("board_once.py", "board_preflight.py", "camera_adapter.py",
                               "prepare_vectors.py")})
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}


def formal_ready_path(boot_id: str, profile: str, ready_id: str | None = None) -> Path:
    base = Path("/var/lib/wo0017") / boot_id / profile
    if ready_id is None:
        return base / "ready.json"
    if not isinstance(ready_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,32}", ready_id, re.ASCII) is None:
        raise ValueError("WO-0017 READY id requires 1-32 ASCII letters, digits, '_' or '-'")
    return base / f"ready-{ready_id}.json"


def formal_capture_context(args: argparse.Namespace, plan: Any) -> dict[str, Any] | None:
    profile = getattr(args, "wo0017_profile", None)
    if profile is None:
        return None
    if (not args.capture_dir or not args.no_consume_guard or not args.attempt_id
            or args.mode != "board"):
        raise ValueError("WO-0017 requires raw capture and independent one-shot mode")
    if not args.expected_boot_id or not args.stage1_receipt or not args.stage1_receipt_sha256:
        raise ValueError("WO-0017 requires explicit Boot and hash-pinned Stage 1 receipt")
    if plan.boot_id != args.expected_boot_id:
        raise ValueError("WO-0017 approved Boot does not match manifest")
    from wo0017_capture_profile import profile_identity, validate_stage1_receipt
    receipt = validate_stage1_receipt(args.stage1_receipt, args.stage1_receipt_sha256, plan)
    identity = profile_identity(
        profile, boot_id=plan.boot_id, manifest_sha256=plan.manifest_sha256,
        bit_sha256=plan.bit_sha256, hwh_sha256=plan.hwh_sha256,
        max_frames=args.max_frames, expected_count=args.ground_truth_count,
        material_label=args.material_label, stabilization_delay_s=args.stabilization_delay_s,
        stage1_receipt_sha256=args.stage1_receipt_sha256)
    runtime = formal_runtime_sha256()
    identity.update({"runtime_sha256": runtime,
                     "counting_config": counting_configuration(args),
                     "capture_config": {"enabled": True,
                         "output_directory": str(Path(args.capture_dir).expanduser().resolve(strict=False)),
                         "max_bytes": args.capture_max_bytes, "queue_depth": 2, "schema_version": 1}})
    ready_id = getattr(args, "wo0017_ready_id", None)
    ready_path = formal_ready_path(plan.boot_id, profile, ready_id)
    if ready_id is not None:
        identity["ready_id"] = ready_id
    if getattr(args, "counting_profile", "legacy") in ("full-width-mixed", "lighting-calibrated"):
        identity["preprocessing_config"] = preprocessing_configuration(args)
        identity["threshold"] = effective_threshold(args)
    base = Path("/var/lib/wo0017") / plan.boot_id / profile
    return {"identity": identity, "receipt": receipt,
            "ready_path": ready_path, "attempt_root": base / "attempts",
            "runtime_sha256": runtime}


def require_formal_xrt_environment(args: argparse.Namespace) -> None:
    if getattr(args, "wo0017_profile", None) and os.environ.get("XILINX_XRT") != "/usr":
        raise ValueError("WO-0017 requires XILINX_XRT=/usr in the launch process before READY or Permit consumption")


def calibration_initial_dmasr(args: argparse.Namespace) -> int:
    """Pin the explicitly selected, error-free initial DMA baseline."""
    value = getattr(args, "calibration_initial_dmasr", 0x00000001)
    if type(value) is not int or value not in (0x00000001, 0x00001002):
        raise ValueError("calibration initial DMASR must be 0x1 or 0x1002")
    return value


def validate_calibration_options(args: argparse.Namespace) -> None:
    """Constrain the non-formal calibration mode to its fixed one-shot contract."""
    if getattr(args, "physical_calibration", False) is not True:
        raise ValueError("physical calibration mode is not enabled")
    if args.mode != "board":
        raise ValueError("physical calibration requires board mode")
    worker_python = getattr(args, "worker_python", None)
    if not isinstance(worker_python, str) or not worker_python.startswith("/"):
        raise ValueError("physical calibration requires an absolute PYNQ worker Python path")
    if getattr(args, "wo0017_profile", None) or getattr(args, "no_consume_guard", False):
        raise ValueError("physical calibration cannot use a formal profile or formal attempt guard")
    if args.authorize_stage != WO17_CALIBRATION_STAGE:
        raise ValueError(f"physical calibration requires --authorize-stage {WO17_CALIBRATION_STAGE}")
    if args.execute is not True or args.approve_once is not True:
        raise ValueError("physical calibration requires --execute --approve-once")
    if args.counting_profile != "full-width-mixed":
        raise ValueError("physical calibration requires --counting-profile full-width-mixed")
    if args.max_frames != 300:
        raise ValueError("physical calibration requires exactly 300 frames")
    calibration_initial_dmasr(args)
    if args.stabilization_delay_s != 3.0 or args.operator_prearm_delay_s != 0.0:
        raise ValueError("physical calibration requires a 3-second stabilization and no extra pre-arm")
    if args.camera != "/dev/video0":
        raise ValueError("physical calibration is pinned to /dev/video0")
    if not args.manifest or not args.manifest_sha256:
        raise ValueError("physical calibration requires a hash-pinned WO-0015 manifest")
    if not args.expected_boot_id or not args.stage1_receipt or not args.stage1_receipt_sha256:
        raise ValueError("physical calibration requires a pinned Boot ID and Stage 1 receipt")
    if not args.capture_dir:
        raise ValueError("physical calibration requires dual-array raw frame capture")
    if (not args.evidence_json or not Path(args.evidence_json).is_absolute()
            or not args.calibration_lock or not Path(args.calibration_lock).is_absolute()):
        raise ValueError("physical calibration evidence and lock paths must be absolute")
    if (not isinstance(getattr(args, "calibration_id", None), str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}",
                            args.calibration_id, re.ASCII) is None):
        raise ValueError("calibration id must contain 1-64 ASCII letters, digits, '_' or '-'")
    config = counting_configuration(args)
    required = {
        "count_axis": "x", "line_x": 160, "roi_mode": "full", "guarded_merge": True,
        "single_intact_area": 95, "max_merge_area": 260,
        "max_split_area": 900, "max_merged_span": 24,
        "max_step": 48.0, "max_lateral_step": 10.0, "backward_tolerance": 4.0,
        "component_cluster_gap": 14.0,
    }
    if any(config.get(name) != value for name, value in required.items()):
        raise ValueError("physical calibration counting configuration differs from full-width contract")


def _read_standard_camera_profile(device: str) -> dict[str, Any]:
    require_standard_camera_controls(device)
    command = ["v4l2-ctl", "-d", device, "--get-fmt-video", "--get-parm"]
    if getattr(os, "geteuid", lambda: -1)() != 0:
        command = ["sudo", "-n"] + command
    output = subprocess.run(command, check=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=5).stdout
    requirements = {
        "resolution": r"Width/Height\s*:\s*640/400",
        "pixel_format": r"Pixel Format\s*:\s*'MJPG'",
        "fps": r"Frames per second\s*:\s*60(?:\.0+)?\s*\(60/1\)",
    }
    for label, pattern in requirements.items():
        if re.search(pattern, output) is None:
            raise RuntimeError(f"camera {label} readback does not match 640x400 MJPG@60: {output}")
    return {"resolution": [640, 400], "pixel_format": "MJPG", "fps": 60.0,
            "auto_exposure": 1, "exposure_time_absolute": 50, "brightness": 0,
            "readback": output}


def _require_camera_device_unowned(device: str) -> dict[str, Any]:
    command = ["fuser", device]
    if getattr(os, "geteuid", lambda: -1)() != 0:
        command = ["sudo", "-n"] + command
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=5)
    if result.returncode == 0:
        raise RuntimeError(f"camera device is already owned: {result.stdout.strip()}")
    if result.returncode != 1:
        raise RuntimeError(f"camera ownership query failed: {result.stderr.strip()}")
    return {"device": device, "owners": [], "read_only": True}


def physical_calibration_context(args: argparse.Namespace, plan: Any) -> dict[str, Any]:
    validate_calibration_options(args)
    if plan.boot_id != args.expected_boot_id:
        raise ValueError("physical calibration Boot ID does not match the pinned manifest")
    from wo0017_capture_profile import validate_stage1_receipt
    receipt = validate_stage1_receipt(args.stage1_receipt,
                                      args.stage1_receipt_sha256, plan)
    capture = preflight_capture_target(
        args.capture_dir, expected_frames=300, max_bytes=args.capture_max_bytes,
        source_frame_count=len(PHYSICAL_CALIBRATION_SOURCE_FRAME_IDS))
    runtime = formal_runtime_sha256()
    lock_path = calibration_lock.calibration_lock_path(plan.boot_id, args.calibration_id)
    requested_lock_path = Path(args.calibration_lock).expanduser().resolve(strict=False)
    if requested_lock_path != lock_path.resolve(strict=False):
        raise ValueError("calibration lock path must be the new Boot-ID-scoped calibration path")
    identity = {
        "schema": 1,
        "work_order": "WO-0017",
        "stage": "physical-calibration",
        "calibration_id": args.calibration_id,
        "boot_id": plan.boot_id,
        "manifest_sha256": plan.manifest_sha256,
        "bit_sha256": plan.bit_sha256,
        "hwh_sha256": plan.hwh_sha256,
        "stage1_receipt_sha256": args.stage1_receipt_sha256,
        "max_frames": 300,
        "threshold": STAGEB2_THRESHOLD,
        "counting_profile": "full-width-mixed",
        "counting_config": counting_configuration(args),
        "preprocessing_config": preprocessing_configuration(args),
        "camera_profile": {"resolution": [640, 400], "pixel_format": "MJPG",
                            "fps": 60, "auto_exposure": 1,
                            "exposure_time_absolute": 50, "brightness": 0},
        "capture_config": {"enabled": True, "dual_arrays": ["gray8", "binary8"],
                            "output_directory": capture["path"],
                            "max_bytes": args.capture_max_bytes,
                            "queue_depth": 0, "schema_version": 1,
                            "write_mode": "deferred_memory", "flush_timeout_s": 300.0,
                            "source_resolution": [640, 400],
                            "source_frame_ids": list(PHYSICAL_CALIBRATION_SOURCE_FRAME_IDS),
                            "source_archive": "frames_source640x400.npz"},
        "evidence_json": str(Path(args.evidence_json).expanduser().resolve(strict=False)),
        "worker_python": args.worker_python,
        "runtime_sha256": runtime,
    }
    if calibration_initial_dmasr(args) != 0x00000001:
        identity["initial_dmasr"] = f"0x{calibration_initial_dmasr(args):08x}"
    return {"identity": identity, "receipt": receipt, "runtime_sha256": runtime,
            "lock_path": lock_path, "consumed_path": lock_path.parent / "consumed.json",
            "capture_preflight": capture}


def validate_calibration_board_state(args: argparse.Namespace, *,
                                     gate_launcher_sha256: str,
                                     operator_gate: Any | None = None,
                                     require_lock: bool = True) -> dict[str, Any]:
    """Read-only admission for the calibration standby; never imports PYNQ or opens V4L2."""
    validate_calibration_options(args)
    if os.environ.get("XILINX_XRT") != "/usr":
        raise ValueError("physical calibration requires XILINX_XRT=/usr")
    require_board_opt_in(execute=args.execute, authorize_stage=args.authorize_stage,
                         approve_once=args.approve_once, manifest=args.manifest,
                         manifest_sha256=args.manifest_sha256, max_frames=args.max_frames,
                         evidence_json=args.evidence_json, calibration=True)
    plan = preflight.validate_manifest(args.manifest, args.manifest_sha256,
                                       approved_boot_id=args.expected_boot_id)
    if not plan.ready_for_board or plan.manifest.get("clock_mhz") != 50:
        raise preflight.PreflightError("calibration manifest is not board-ready at 50 MHz")
    if preflight.read_boot_id() != plan.boot_id:
        raise preflight.PreflightError("calibration live Boot ID differs from the pinned manifest")
    meminfo = preflight.read_cma_meminfo()
    if meminfo["CmaFree"] <= CMA_WATERMARK_KIB:
        raise preflight.PreflightError("calibration CmaFree must be strictly above 32768 KiB")
    admission = preflight.read_cma_admission(meminfo=meminfo)
    ownership = preflight.audit_process_ownership()
    manager = preflight.read_fpga_manager()
    camera_owner = _require_camera_device_unowned(args.camera)
    camera_profile = _read_standard_camera_profile(args.camera)
    mm2s_status = board_once.read_phys32_readonly(preflight.DMA_BASE + 0x04)
    s2mm_status = board_once.read_phys32_readonly(preflight.DMA_BASE + 0x34)
    expected_dmasr = calibration_initial_dmasr(args)
    if mm2s_status != expected_dmasr or s2mm_status != expected_dmasr:
        raise preflight.PreflightError(
            f"calibration requires pinned initial DMASR=0x{expected_dmasr:08x}; "
            f"MM2S=0x{mm2s_status:08x}, S2MM=0x{s2mm_status:08x}"
        )
    calibration = physical_calibration_context(args, plan)
    lock_data = None
    if require_lock:
        lock_data = calibration_lock.validate_ready_calibration_lock(
            calibration["lock_path"], calibration["identity"])
        if calibration["consumed_path"].exists() or calibration["consumed_path"].is_symlink():
            raise RuntimeError("calibration lock has already been consumed; no retry is allowed")
        if not hmac.compare_digest(gate_launcher_sha256,
                                   calibration["runtime_sha256"]["operator_gate_launcher.py"]):
            raise RuntimeError("calibration lock does not bind the current operator gate launcher")
        if operator_gate is not None:
            operator_gate.bind_control_sha256(lock_data["sha256"], label="calibration lock")
    return {"plan": plan, "calibration": calibration, "lock": lock_data,
            "meminfo": meminfo, "cma_admission": admission.as_evidence(),
            "process_ownership": ownership, "fpga_manager": manager,
            "camera_ownership": camera_owner, "camera_profile": camera_profile,
            "dma_status": {"mm2s": mm2s_status, "s2mm": s2mm_status},
            "pynq_imported": False, "camera_opened": False,
            "dma_transfer_started": False}


def validate_calibration_prelaunch(args: argparse.Namespace,
                                   gate_launcher_sha256: str,
                                   operator_gate: Any) -> dict[str, Any]:
    state = validate_calibration_board_state(
        args, gate_launcher_sha256=gate_launcher_sha256,
        operator_gate=operator_gate, require_lock=True)
    return {"status": "CALIBRATION_STANDBY_READY",
            "calibration_id": args.calibration_id,
            "boot_id": state["plan"].boot_id,
            "lock_path": str(state["calibration"]["lock_path"]),
            "lock_sha256": state["lock"]["sha256"],
            "cma_free_kib": state["meminfo"]["CmaFree"],
            "dma_status": {key: f"0x{value:08x}"
                           for key, value in state["dma_status"].items()},
            "camera_profile": state["camera_profile"],
            "camera_ownership": state["camera_ownership"],
            "pynq_imported": False, "camera_opened": False,
            "dma_transfer_started": False}


def claim_calibration_after_operator_click(args: argparse.Namespace) -> dict[str, Any]:
    """Consume the independent calibration lock immediately after the 8081 click."""
    validate_calibration_options(args)
    plan = preflight.validate_manifest(args.manifest, args.manifest_sha256,
                                       approved_boot_id=args.expected_boot_id)
    calibration = physical_calibration_context(args, plan)
    if (calibration["consumed_path"].exists()
            or calibration["consumed_path"].is_symlink()):
        raise RuntimeError("calibration lock has already been consumed; no retry is allowed")
    return calibration_lock.claim_calibration_lock_once(
        calibration["lock_path"], calibration["identity"])


def validate_formal_prelaunch(args: argparse.Namespace, launcher_sha256: str) -> dict[str, Any]:
    """Read-only admission to the browser standby; never import PYNQ or claim an attempt."""
    if not args.wo0017_profile:
        raise ValueError("formal prelaunch requires WO-0017 profile")
    require_board_opt_in(execute=args.execute, authorize_stage=args.authorize_stage,
                        approve_once=args.approve_once, manifest=args.manifest,
                        manifest_sha256=args.manifest_sha256, max_frames=args.max_frames,
                        evidence_json=args.evidence_json)
    require_formal_xrt_environment(args)
    plan = preflight.validate_manifest(args.manifest, args.manifest_sha256,
                                       approved_boot_id=args.expected_boot_id)
    formal = formal_capture_context(args, plan)
    if preflight.read_boot_id() != plan.boot_id:
        raise ValueError("formal prelaunch current Boot mismatch")
    if preflight.read_cma_meminfo()["CmaFree"] <= CMA_WATERMARK_KIB:
        raise ValueError("formal prelaunch CMA is not above watermark")
    capture = preflight_capture_target(args.capture_dir, expected_frames=args.max_frames,
                                       max_bytes=args.capture_max_bytes)
    ready = validate_non_consuming_guard(formal["ready_path"], formal["identity"])
    identity = stageb2_attempt_permit_identity(
        ready_identity=formal["identity"], ready_guard_sha256=ready["sha256"],
        attempt_id=args.attempt_id, operator_prearm_delay_s=args.operator_prearm_delay_s,
        runner_sha256=formal["runtime_sha256"]["run_stream_counter.py"],
        gate_launcher_sha256=launcher_sha256, counting_config=counting_configuration(args),
        counter_sha256=formal["runtime_sha256"]["stream_counter.py"],
        capture_config={"enabled": True, "output_directory": capture["path"],
                        "max_bytes": args.capture_max_bytes, "queue_depth": 2, "schema_version": 1})
    identity.update({"stage": f"wo0017-{args.wo0017_profile}-attempt",
                     "runtime_sha256": formal["runtime_sha256"]})
    attempt = formal["attempt_root"] / args.attempt_id
    if (attempt / "consumed.json").exists():
        raise ValueError("formal attempt is already consumed")
    return validate_stageb2_attempt_permit(attempt / "attempt-permit.json", identity)


def manifest_validation_options(args: argparse.Namespace, *,
                               calibration_mode: bool = False) -> dict[str, str]:
    """Pass the approved Boot ID for WO-0017 and independent calibration runs."""
    if calibration_mode or getattr(args, "wo0017_profile", None):
        approved_boot_id = getattr(args, "expected_boot_id", None)
        if not approved_boot_id:
            raise ValueError("board run requires an explicit approved Boot ID")
        return {"approved_boot_id": approved_boot_id}
    return {}


def defer_capture_writes_until_close(args: argparse.Namespace, *,
                                     calibration_mode: bool = False) -> bool:
    """Keep formal B-2 sampling free of per-frame filesystem latency."""
    return (calibration_mode
            or getattr(args, "wo0017_profile", None) == "b2")


def execute_board_stream(args: argparse.Namespace, *,
                         operator_gate_sha256: str | None = None,
                         operator_gate: Any | None = None) -> dict[str, Any]:
    """Run one finite, guarded streaming session against the already-loaded WO-0015 BIT."""
    calibration_mode = bool(getattr(args, "physical_calibration", False))
    frozen_counting_config = counting_configuration(args)
    deferred_capture_writes = defer_capture_writes_until_close(
        args, calibration_mode=calibration_mode
    )
    gate_launcher_sha256 = validate_operator_gate_launcher_sha256(
        operator_gate_sha256, operator_gate
    )
    require_board_opt_in(
        execute=args.execute,
        authorize_stage=args.authorize_stage,
        approve_once=args.approve_once,
        manifest=args.manifest,
        manifest_sha256=args.manifest_sha256,
        max_frames=args.max_frames,
        evidence_json=args.evidence_json,
        calibration=calibration_mode,
    )
    require_formal_xrt_environment(args)
    if calibration_mode and os.environ.get("XILINX_XRT") != "/usr":
        raise ValueError("physical calibration requires XILINX_XRT=/usr")
    capture_dir = getattr(args, "capture_dir", None)
    capture_max_bytes = getattr(args, "capture_max_bytes", DEFAULT_MAX_BYTES)
    source_frame_count = (len(PHYSICAL_CALIBRATION_SOURCE_FRAME_IDS)
                          if calibration_mode else 0)
    capture_preflight = (preflight_capture_target(
        capture_dir, expected_frames=args.max_frames, max_bytes=capture_max_bytes,
        source_frame_count=source_frame_count
    ) if capture_dir else None)
    process_start_monotonic = getattr(args, "process_start_monotonic", time.monotonic())
    milestone_path = Path(args.evidence_json).with_suffix(".milestones.jsonl")
    if milestone_path.exists():
        raise FileExistsError("milestone log path already exists; choose a new attempt result path")
    manifest_options = manifest_validation_options(args, calibration_mode=calibration_mode)
    plan = preflight.validate_manifest(args.manifest, args.manifest_sha256, **manifest_options)
    if not plan.ready_for_board:
        raise preflight.PreflightError("manifest is not marked ready_for_board")
    if plan.manifest.get("clock_mhz") != 50:
        raise preflight.PreflightError("WO-0016 is locked to the WO-0015 50 MHz image")
    formal = formal_capture_context(args, plan)
    calibration = physical_calibration_context(args, plan) if calibration_mode else None
    stage1_receipt = (formal["receipt"] if formal else
                      calibration["receipt"] if calibration else
                      board_once.require_overlay_pass_receipt(
                          board_once.OVERLAY_PASS_RECEIPT, plan))
    live_boot = preflight.read_boot_id()
    if live_boot != plan.boot_id:
        raise preflight.PreflightError("live boot_id does not match the pinned WO-0015 manifest")
    meminfo = preflight.read_cma_meminfo()
    if ((calibration_mode and meminfo["CmaFree"] <= CMA_WATERMARK_KIB)
            or (not calibration_mode and meminfo["CmaFree"] < CMA_WATERMARK_KIB)):
        raise preflight.PreflightError(
            "CmaFree must be strictly above 32768 KiB for calibration" if calibration_mode
            else "CmaFree is below the WO-0016 pre-allocation watermark"
        )
    admission = preflight.read_cma_admission(meminfo=meminfo)
    ownership = preflight.audit_process_ownership()
    manager = preflight.read_fpga_manager()
    calibration_camera_profile = None
    calibration_camera_owner = None
    calibration_dma_status = None
    if calibration_mode:
        calibration_camera_owner = _require_camera_device_unowned(args.camera)
        calibration_camera_profile = _read_standard_camera_profile(args.camera)
        calibration_dma_status = {
            "mm2s": board_once.read_phys32_readonly(preflight.DMA_BASE + 0x04),
            "s2mm": board_once.read_phys32_readonly(preflight.DMA_BASE + 0x34),
        }
        if any(status != calibration_initial_dmasr(args) for status in calibration_dma_status.values()):
            raise preflight.PreflightError(
                f"calibration initial DMASR must remain exactly 0x{calibration_initial_dmasr(args):08x} after operator click"
            )
    guard_dir = (formal["ready_path"].parent if formal else
                 calibration["lock_path"].parent if calibration else WO16_GUARD.parent)
    if guard_dir.is_symlink():
        raise preflight.PreflightError("WO-0016 guard directory must not be a symlink")
    guard_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    guard_stat = guard_dir.stat()
    if guard_stat.st_uid != 0 or guard_stat.st_mode & 0o077:
        raise preflight.PreflightError("WO-0016 guard directory must be root-owned and mode 0700")
    stageb2_ready_guard = None
    stageb2_attempt_permit = None
    attempt_path = None
    calibration_lock_record = None
    if args.no_consume_guard:
        if not getattr(args, "attempt_id", None):
            raise ValueError("--no-consume-guard requires a unique --attempt-id")
        identity = formal["identity"] if formal else stageb2_guard_identity(
            boot_id=plan.boot_id,
            manifest_sha256=plan.manifest_sha256,
            bit_sha256=plan.bit_sha256,
            hwh_sha256=plan.hwh_sha256,
            max_frames=args.max_frames,
            expected_count=args.ground_truth_count,
            material_label=args.material_label,
            stabilization_delay_s=args.stabilization_delay_s,
        )
        ready_path = (formal["ready_path"] if formal else
                      STAGEB2_READY_GUARD_DIR / f"stageb2-ready-{plan.boot_id}.json")
        stageb2_ready_guard = validate_non_consuming_guard(ready_path, identity)
        attempt_identity = stageb2_attempt_permit_identity(
            ready_identity=identity,
            ready_guard_sha256=stageb2_ready_guard["sha256"],
            attempt_id=args.attempt_id,
            operator_prearm_delay_s=args.operator_prearm_delay_s,
            runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            gate_launcher_sha256=gate_launcher_sha256,
            counting_config=frozen_counting_config,
            counter_sha256=hashlib.sha256(Path(__file__).with_name("stream_counter.py").read_bytes()).hexdigest(),
            capture_config=({
                "enabled": True,
                "output_directory": capture_preflight["path"],
                "max_bytes": capture_max_bytes,
                "queue_depth": 2,
                "schema_version": 1,
            } if capture_preflight is not None else None),
        )
        if formal:
            attempt_identity.update({"stage": f"wo0017-{args.wo0017_profile}-attempt",
                                     "runtime_sha256": formal["runtime_sha256"]})
        attempt_directory = (formal["attempt_root"] / args.attempt_id if formal else
                             STAGEB2_ATTEMPT_DIR / plan.boot_id / args.attempt_id)
        permit_path = attempt_directory / "attempt-permit.json"
        stageb2_attempt_permit = validate_stageb2_attempt_permit(
            permit_path, attempt_identity
        )
        if operator_gate is not None:
            operator_gate.bind_permit_sha256(stageb2_attempt_permit["sha256"])
        attempt_path = attempt_directory / "consumed.json"
        guard = claim_once_guard(attempt_path, {
            "schema": 1,
            "work_order": "WO-0017" if formal else "WO-0016",
            "stage": attempt_identity["stage"],
            "boot_id": plan.boot_id,
            "attempt_id": args.attempt_id,
            "manifest_sha256": plan.manifest_sha256,
            "bit_sha256": plan.bit_sha256,
            "hwh_sha256": plan.hwh_sha256,
            "ready_guard_sha256": stageb2_ready_guard["sha256"],
            "attempt_permit_sha256": stageb2_attempt_permit["sha256"],
            "gate_launcher_sha256": gate_launcher_sha256,
            "max_frames": args.max_frames,
            "expected_count": args.ground_truth_count,
            "material_label": args.material_label,
            "stabilization_delay_s": args.stabilization_delay_s,
            "operator_prearm_delay_s": args.operator_prearm_delay_s,
        })
    elif calibration_mode:
        ready_lock = calibration_lock.validate_ready_calibration_lock(
            calibration["lock_path"], calibration["identity"])
        if operator_gate is not None:
            operator_gate.bind_control_sha256(ready_lock["sha256"],
                                              label="calibration lock")
        calibration_lock_record = calibration_lock.validate_consumed_calibration_lock(
            calibration["lock_path"], calibration["identity"])["receipt"]
        attempt_path = calibration["consumed_path"]
        guard = calibration_lock_record
    else:
        guard = claim_once_guard(WO16_GUARD, {
            "schema": 1,
            "work_order": "WO-0016",
            "stage": WO16_STAGE,
            "boot_id": plan.boot_id,
            "manifest_sha256": plan.manifest_sha256,
            "bit_sha256": plan.bit_sha256,
            "hwh_sha256": plan.hwh_sha256,
            "max_frames": args.max_frames,
        })

    api = board_once.PynqBoardAPI(plan, stage=WO16_STAGE, cma_ranges=admission.ranges)
    dashboard = DashboardState(source="pl_dma")
    runtime_state = {"status": "RUNNING"}
    tx_buffers: tuple[Any, ...] = ()
    rx_buffers: tuple[Any, ...] = ()
    stream: DoubleBufferStream | None = None
    capture_writer: CaptureWriter | None = None
    preview: MjpegPreviewServer | None = None
    preview_publisher: PeriodicMjpegPublisher | None = None
    loopback_client: MjpegLoopbackClient | None = None
    dma = None
    released_buffer_ids: set[int] = set()
    evidence: dict[str, Any] = {
        "schema": 1,
        "work_order": "WO-0017" if formal else "WO-0016",
        "stage": "physical-calibration" if calibration_mode else None,
        "status": "WO0017_PHYSICAL_CALIBRATION_RUNNING" if calibration_mode else "WO0016_RUNNING",
        "board_accessed": False,
        "boot_id": plan.boot_id,
        "manifest_sha256": plan.manifest_sha256,
        "bit_sha256": plan.bit_sha256,
        "hwh_sha256": plan.hwh_sha256,
        "clock_mhz": 50,
        "guard": guard,
        "guard_mode": ("independent calibration lock consumed-once" if calibration_mode else
                       "READY Guard validate-only; attempt permit consumed-once"
                       if args.no_consume_guard else "consume-once"),
        "calibration_lock": (None if calibration is None else {
            "path": str(calibration["lock_path"]),
            "sha256": hashlib.sha256(calibration_lock._json_bytes({
                **calibration["identity"], "lock_status": "READY", "consumed": False
            })).hexdigest(),
            "consumed_marker_path": str(calibration["consumed_path"]),
            "consumed_marker": calibration_lock_record,
        }),
        "formal_acceptance_permit": None if calibration_mode else None,
        "stageb2_ready_guard": stageb2_ready_guard,
        "stageb2_attempt_id": args.attempt_id if args.no_consume_guard else None,
        "stageb2_attempt_permit": stageb2_attempt_permit,
        "attempt_marker_path": str(attempt_path) if attempt_path is not None else None,
        "cma_admission": admission.as_evidence(),
        "stage1_overlay_pass_receipt": stage1_receipt,
        "process_ownership": ownership,
        "fpga_manager": manager,
        "cma_free_before_kib": meminfo["CmaFree"],
        "cma_watermark_kib": CMA_WATERMARK_KIB,
        "camera_profile_pre_start": calibration_camera_profile,
        "camera_ownership_pre_start": calibration_camera_owner,
        "dma_status_pre_pynq": calibration_dma_status,
        "max_frames": args.max_frames,
        "camera_device": args.camera,
        "camera_mode": "MJPG 640x400 mapped to Gray8 320x240",
        "xilinx_xrt": os.environ.get("XILINX_XRT"),
        "line_y": frozen_counting_config["line_y"],
        "counting_config": frozen_counting_config,
        "frame_geometry": preprocessing_configuration(args),
        "direction": args.direction,
        "min_area": frozen_counting_config["min_area"],
        "ground_truth_count": args.ground_truth_count,
        "expected_count": args.ground_truth_count,
        "material_label": args.material_label,
        "stabilization_delay_s": args.stabilization_delay_s,
        "operator_prearm_delay_s": (args.operator_prearm_delay_s
                                     if args.no_consume_guard else 0.0),
        "threshold": effective_threshold(args),
        "frames_processed": 0,
        "process_start_monotonic": process_start_monotonic,
        "overlay_ready_monotonic": None,
        "camera_open_monotonic": None,
        "frame_0_start_monotonic": None,
        "milestone_log": str(milestone_path),
    }
    try:
        _emit_milestone("PROCESS_START", process_start_monotonic,
                        milestone_path, boot_id=plan.boot_id)
        audit = api.audit_environment(require_empty_shutdown=False, require_loaded_bit=True)
        if audit["boot_id"] != plan.boot_id:
            raise preflight.PreflightError("boot_id changed during WO-0016 preflight")
        evidence["runtime_audit"] = audit
        api.open_mmio()
        pynq = api._pynq
        overlay = pynq.Overlay(str(plan.bit_path), download=False)
        api._overlay = overlay
        evidence["overlay_ready_monotonic"] = time.monotonic()
        _emit_milestone("OVERLAY_READY_DOWNLOAD_FALSE",
                        evidence["overlay_ready_monotonic"], milestone_path,
                        boot_id=plan.boot_id)
        checker_signature = api.read32(preflight.CHECKER_BASE, 0x04)
        packer_signature = api.read32(preflight.CHECKER_BASE, board_once.PACKER_SIGNATURE)
        if checker_signature != preflight.PACKET_MAGIC or packer_signature != preflight.PACKER_SIGNATURE:
            raise preflight.PreflightError("loaded Checker/Packer signatures do not match WO-0015")
        evidence["checker_signature"] = f"0x{checker_signature:08x}"
        evidence["packer_signature"] = f"0x{packer_signature:08x}"
        afi = board_once.read_afi1_snapshot(api, "wo0016-preflight")
        evidence["afi1_exact_readback"] = verify_afi1_readback(afi)
        evidence["afi1_snapshot"] = afi
        for offset, label in ((0x04, "MM2S"), (0x34, "S2MM")):
            status = api.read32(preflight.DMA_BASE, offset)
            if calibration_mode and status != calibration_initial_dmasr(args):
                raise preflight.PreflightError(
                    f"{label} initial DMASR changed from pinned calibration baseline 0x{calibration_initial_dmasr(args):08x}: "
                    f"0x{status:08x}"
                )
            if status & board_once.DMA_ERROR_MASK:
                raise preflight.PreflightError(f"{label} DMA begins with an error status")
            if not (status & board_once.DMA_HALTED) and not (status & board_once.DMA_IDLE):
                raise preflight.PreflightError(f"{label} DMA is already active")
        if api.cma_free_kib() < CMA_WATERMARK_KIB:
            raise preflight.PreflightError("CmaFree fell below watermark before allocation")

        def allocate_with_watermark(*, shape: tuple[int, ...], dtype: Any) -> Any:
            if api.cma_free_kib() < CMA_WATERMARK_KIB:
                raise preflight.PreflightError("CmaFree fell below watermark before a buffer allocation")

            return api.allocate(int(np.prod(shape)))

        def verify_allocation_watermark(_buffer: Any) -> None:
            if api.cma_free_kib() < CMA_WATERMARK_KIB:
                raise preflight.PreflightError("CmaFree fell below watermark after a buffer allocation")

        tx_buffers, rx_buffers = allocate_ping_pong_buffers(
            allocate_with_watermark, free=api.free,
            after_allocate=verify_allocation_watermark,
        )
        buffer_gates = []
        for index in range(2):
            tx_address = api.device_address(tx_buffers[index])
            rx_address = api.device_address(rx_buffers[index])
            buffer_gates.append(preflight.validate_dma_buffer_pair(
                tx_address=tx_address,
                tx_size=int(memoryview(tx_buffers[index]).nbytes),
                rx_address=rx_address,
                rx_size=int(memoryview(rx_buffers[index]).nbytes),
                cma_ranges=admission.ranges,
                hp0_range=plan.hp0_range,
                hp1_range=plan.hp1_range,
            ))
        evidence["buffer_gates"] = buffer_gates
        evidence["buffer_layout"] = {
            "tx_bytes_per_slot": TX_BYTES,
            "rx_bytes_per_slot": RX_BYTES,
            "slot_count": 2,
            "allocation_count": 4,
            "cma_free_after_allocation_kib": api.cma_free_kib(),
        }

        if capture_preflight is not None:
            capture_writer = CaptureWriter(
                capture_dir,
                expected_frames=args.max_frames,
                config={
                    "algorithm": "baseline",
                    **frozen_counting_config,
                    "threshold": effective_threshold(args),
                    "input_resolution": [WIDTH, HEIGHT],
                    "mask_resolution": [WIDTH, HEIGHT],
                    "roi_bounds_convention": "[top,bottom), clipped to image",
                    "frame_geometry": preprocessing_configuration(args),
                },
                max_bytes=capture_max_bytes,
                queue_depth=2,
                defer_writes_until_close=deferred_capture_writes,
                source_frame_ids=(PHYSICAL_CALIBRATION_SOURCE_FRAME_IDS
                                  if calibration_mode else ()),
                metadata={
                    "work_order": "WO-0017 Phase 1B",
                    "input_stage": "decoded first camera channel mapped to FPGA Gray8 input; geometry recorded in config",
                    "camera_requested_mode": "MJPG 640x400 60 FPS; actual negotiated mode recorded in runner evidence",
                    "mask_source": "same S2MM RX DMA slot and software Frame ID as MM2S input",
                    "mask_frame_id_echoed_by_hardware": False,
                    "capacity_preflight": capture_preflight,
                },
            )
            evidence["capture_directory"] = capture_preflight["path"]
            evidence["capture_status"] = "IN_PROGRESS"
            evidence["raw_capture_storage"] = {
                "memory_kind": "ordinary NumPy process RAM; not PYNQ/CMA allocation",
                "array_shape": list(capture_writer.raw_gray8.shape),
                "dtype": "uint8",
                "gray_bytes": int(capture_writer.raw_gray8.nbytes),
                "binary_bytes": int(capture_writer.raw_binary8.nbytes),
                "recording": "np.copyto after S2MM completion and frame-status validation",
                "archive_written_after_sampling": True,
                "write_mode": ("deferred_memory" if deferred_capture_writes else "streaming_queue"),
                "per_frame_files_written_during_sampling": not deferred_capture_writes,
            }

        # Accessing the PYNQ DMA driver starts its channels; this is after all static,
        # process, Overlay identity, CMA and physical-span gates have passed.
        dma = overlay.axi_dma_0
        checker = api._mmio[preflight.CHECKER_BASE]

        def arm_frame(frame_id: int, slot: Any) -> None:
            status = api.read32(preflight.CHECKER_BASE, board_once.PACKER_STATUS)
            if status & (board_once.PACKER_BUSY | board_once.PACKER_FAULT):
                raise RuntimeError(f"Packer is busy or faulted before frame {frame_id}")
            if api.read32(preflight.CHECKER_BASE, board_once.PACKER_ERROR_CODE):
                raise RuntimeError(f"Packer error register is nonzero before frame {frame_id}")
            api.sync_to_device(slot.tx_buffer)
            slot.rx_buffer[:] = np.uint8(0xA5)
            api.sync_to_device(slot.rx_buffer)
            api.write32(preflight.CHECKER_BASE, board_once.PACKER_ARM, 1)

        frame_dma_status: list[dict[str, Any]] = []
        packer_frame_status: list[dict[str, Any]] = []

        def verify_frame(frame_id: int, slot: Any) -> None:
            if int(dma.recvchannel.transferred) != RX_BYTES:
                raise RuntimeError(f"frame {frame_id} S2MM transferred length is not {RX_BYTES}")
            verify_frame_dmasr(api, frame_id, frame_dma_status)
            dma_record = dict(frame_dma_status[-1])
            if slot.capture_metadata is not None:
                slot.capture_metadata["dma_status"] = dma_record
            status = api.read32(preflight.CHECKER_BASE, board_once.PACKER_STATUS)
            accepted = api.read32(preflight.CHECKER_BASE, board_once.PACKER_ACCEPTED_PIXELS)
            sent = api.read32(preflight.CHECKER_BASE, board_once.PACKER_SENT_BEATS)
            error_code = api.read32(preflight.CHECKER_BASE, board_once.PACKER_ERROR_CODE)
            if status & board_once.PACKER_FAULT or status & board_once.PACKER_BUSY:
                raise RuntimeError(f"frame {frame_id} Packer is faulted or still busy")
            if not status & board_once.PACKER_DONE or accepted != RX_BYTES or sent != RX_BYTES // 4 or error_code:
                raise RuntimeError(f"frame {frame_id} Packer frame counters/status are inconsistent")
            packer_record = {
                "frame_id": frame_id,
                "status": f"0x{status:08x}",
                "accepted_pixels": accepted,
                "sent_beats": sent,
                "error_code": error_code,
                "validation": "PASS",
            }
            packer_frame_status.append(packer_record)
            if slot.capture_metadata is not None:
                slot.capture_metadata["packer_status"] = dict(packer_record)

        hub = MjpegFrameHub()
        preview = MjpegPreviewServer(
            hub=hub, host=args.host, port=args.port,
            state_provider=lambda: {**runtime_state, "dashboard": dashboard.snapshot()},
        ).start()
        stage_timings_ms: dict[str, list[float]] = {stage: [] for stage in (
            "capture", "mapping", "encode", "dma", "rx_sync", "rx_copy", "packer_check", "roi_count",
            "raw_capture_copy", "mjpeg_encode", "mjpeg_handoff",
            "mjpeg_background_encode", "postprocess")}
        preview_publisher = PeriodicMjpegPublisher(
            hub=hub, line_y=frozen_counting_config["line_y"],
            count_axis=frozen_counting_config["count_axis"],
            line_x=frozen_counting_config["line_x"],
            interval=PREVIEW_FRAME_INTERVAL, quality=PREVIEW_JPEG_QUALITY,
            hysteresis=args.hysteresis,
        )
        stage_timings_ms["mjpeg_encode"] = preview_publisher.background_encode_timings_ms
        stage_timings_ms["mjpeg_background_encode"] = (
            preview_publisher.background_encode_timings_ms
        )
        stage_timings_ms["mjpeg_handoff"] = preview_publisher.handoff_timings_ms
        if args.measure_preview_client:
            loopback_client = MjpegLoopbackClient(
                f"http://127.0.0.1:{preview.port}/stream.mjpg",
                timeout_s=min(1.0, args.timeout_s),
            ).start()

        counter = MaskCounter(**frozen_counting_config)
        def capture_frame(result: Any, slot: Any) -> None:
            if capture_writer is None or slot.capture_gray is None or slot.capture_mask is None:
                raise CaptureError("capture writer or raw source/mask slot snapshot is unavailable")
            metadata = {
                **(slot.capture_metadata or {}),
                "frame_id": result.frame_id,
                "dma_slot_index": slot.index,
                "dma_complete_monotonic_ns": slot.dma_complete_monotonic_ns,
                "camera_mode_readback": dict(capture_metadata),
                "software_pairing": {
                    "input_sequence_equals_dma_frame_id": True,
                    "input_and_output_use_same_slot": True,
                    "mask_echoes_frame_id": False,
                    "mask_source": "S2MM output copied from this slot after its DMA completion and cache sync",
                },
                "resolution": {"width": WIDTH, "height": HEIGHT,
                               "dtype": "uint8", "input": "Gray8", "mask": "Binary8"},
                "threshold": effective_threshold(args),
                "algorithm_config": dict(frozen_counting_config),
                "roi": counting_roi_metadata(result, counter),
                "raw_detections": [asdict(item) for item in result.raw_detections],
                "detections": [asdict(item) for item in result.detections],
                "associations": list(counter.last_associations),
                "tracks": list(counter.tracking_snapshot()),
                "events": [asdict(item) for item in result.events],
                "suppressed_events": [asdict(item) for item in result.suppressed_events],
                "total_count": result.total_count,
                "frame_result": {
                    "roi_foreground_pixels": result.roi_foreground_pixels,
                    "glare_rejected_count": result.glare_rejected_count,
                    "glare_area_px": result.glare_area_px,
                    "ccl_executed": result.ccl_executed,
                },
            }
            capture_writer.submit(result.frame_id, slot.capture_gray, slot.capture_mask, metadata)

        packet_encoder, packet_encoder_into = packet_encoders(args)
        stream = DoubleBufferStream(
            recv_channel=dma.recvchannel,
            send_channel=dma.sendchannel,
            tx_buffers=tx_buffers,
            rx_buffers=rx_buffers,
            counter=counter,
            packet_encoder=packet_encoder,
            packet_encoder_into=packet_encoder_into,
            before_transfer=arm_frame,
            before_rx_copy=lambda frame_id, slot: api.sync_from_device(slot.rx_buffer),
            after_transfer=verify_frame,
            on_frame=dashboard.on_frame,
            preview_callback=preview_publisher,
            raw_capture_callback=(capture_writer.record_raw_frame
                                  if capture_writer is not None else None),
            source_capture_callback=(capture_writer.record_source_frame
                                     if capture_writer is not None and calibration_mode else None),
            capture_callback=(capture_frame if capture_writer is not None else None),
            stage_timings_ms=stage_timings_ms,
            transfer_timeout_s=args.timeout_s,
        )
        steady_start_time: list[float] = []
        capture_metadata: dict[str, Any] = {}

        def record_camera_milestone(name: str, at: float) -> None:
            evidence_key = {
                "CAMERA_OPENED": "camera_open_monotonic",
                "FRAME_0_CAPTURE_START_FEED_NOW": "frame_0_start_monotonic",
            }.get(name)
            if evidence_key is not None:
                evidence[evidence_key] = at
            _emit_milestone(name, at, milestone_path, boot_id=plan.boot_id)

        results = asyncio.run(stream.run(camera_gray_frames(
            args.camera, max_frames=args.max_frames, cv2_module=cv2,
            read_timeout_s=args.timeout_s,
            stabilization_delay_s=args.stabilization_delay_s,
            operator_prearm_delay_s=(args.operator_prearm_delay_s
                                     if args.no_consume_guard else 0.0),
            operator_gate=operator_gate,
            milestone_callback=record_camera_milestone,
            capture_timings_ms=stage_timings_ms["capture"],
            mapping_timings_ms=stage_timings_ms["mapping"],
            steady_start_time=steady_start_time,
            capture_metadata=capture_metadata,
            include_capture_metadata=(capture_writer is not None or counter.motion_timebase_s is not None),
            source_frame_ids=(PHYSICAL_CALIBRATION_SOURCE_FRAME_IDS
                              if calibration_mode else ()),
            frame_mapping=preprocessing_configuration(args)["mapping"],
            binary_threshold=effective_threshold(args),
            reference_area=getattr(args, "reference_area", REFERENCE_AREA))))
        if not steady_start_time:
            raise RuntimeError("camera did not establish a steady-stream timing boundary")
        dashboard.set_phase("complete", "实板计数会话已完成")
        runtime_state["status"] = "COMPLETE"
        # Confirm the final hardware state before any potentially blocking writer drain.
        s2mm_status = api.read32(preflight.DMA_BASE, 0x34)
        mm2s_status = api.read32(preflight.DMA_BASE, 0x04)
        for status, label in ((s2mm_status, "S2MM"), (mm2s_status, "MM2S")):
            if status & board_once.DMA_ERROR_MASK or not status & board_once.DMA_IDLE:
                raise RuntimeError(f"{label} is not error-free and idle at normal shutdown")
        elapsed = max(time.perf_counter() - steady_start_time[0], 1e-9)
        total_count = results[-1].total_count if results else 0
        accuracy = _count_accuracy(total_count, args.ground_truth_count)
        accuracy_fraction = _count_accuracy_fraction(total_count, args.ground_truth_count)
        preview_publisher.close()
        if loopback_client is not None:
            loopback_client.close()
        if capture_writer is not None:
            capture_result = capture_writer.close()
            evidence["capture"] = capture_result
            evidence["capture_status"] = capture_result["status"]
            if capture_result["status"] != "COMPLETE":
                raise CaptureError("requested evidence capture is incomplete")
        final_camera_profile = (_read_standard_camera_profile(args.camera)
                                if calibration_mode else None)
        final_camera_ownership = (_require_camera_device_unowned(args.camera)
                                  if calibration_mode else None)
        evidence.update({
            "status": ("PHYSICAL_CALIBRATION_COMPLETE" if calibration_mode
                       else "BOARD_STREAM_COMPLETE"),
            "board_accessed": True,
            "frames_processed": len(results),
            "total_count": total_count,
            "ground_truth_count": args.ground_truth_count,
            "expected_count": args.ground_truth_count,
            "material_label": args.material_label,
            "count_error": (None if args.ground_truth_count is None
                            else total_count - args.ground_truth_count),
            "accuracy_percent": accuracy,
            "accuracy": accuracy_fraction,
            "elapsed_seconds": elapsed,
            "throughput_fps": len(results) / elapsed,
            "hardware_fps": len(results) / elapsed,
            **summarize_stage_timings_ms(stage_timings_ms),
            "roi_subtimings_ms": summarize_roi_subtimings_ms(results),
            **capture_metadata,
            "preview_frame_interval": preview_publisher.interval,
            "preview_jpeg_quality": preview_publisher.quality,
            "preview_jpeg_frames_encoded": preview_publisher.encoded_frames,
            "preview_encode_errors": tuple(preview_publisher.encode_errors),
            "preview_client_frames_received": (None if loopback_client is None
                                                else loopback_client.frames_received),
            "preview_client_fps": (None if loopback_client is None
                                   else loopback_client.preview_client_fps),
            "preview_client_error": (None if loopback_client is None
                                     else loopback_client.error),
            "preview_url": f"http://{args.host}:{preview.port}/",
            "dma_status_final": {"mm2s": mm2s_status, "s2mm": s2mm_status},
            "camera_profile_final": final_camera_profile,
            "camera_ownership_final": final_camera_ownership,
            "frame_dma_status": frame_dma_status,
            "packer_frame_status": packer_frame_status,
            "packer_output_frames_verified": len(results),
            "pynq_imported": True,
            "accuracy_formula": (None if calibration_mode else
                                 "1 - abs(total_count - expected_count) / max(expected_count, 1)"),
            "note": ("Calibration/preview measurement only; no formal Permit, acceptance decision, or retry."
                     if calibration_mode else
                     "Measured only for the supplied finite live-camera interval; no automatic retry/reset/reload."),
        })
        _release_buffers(api, tx_buffers + rx_buffers, released_buffer_ids)
        evidence["cma_free_after_release_kib"] = api.cma_free_kib()
        evidence["cma_free_delta_kib"] = (evidence["cma_free_after_release_kib"]
                                             - evidence["cma_free_before_kib"])
        evidence["buffers_released"] = True
        _write_json_exclusive(Path(args.evidence_json), evidence)
        return evidence
    except BaseException as exc:
        dashboard.set_phase("failed", f"计数失败：{type(exc).__name__}: {exc}")
        runtime_state["status"] = "FAILED"
        dma_safe_to_release = _dma_buffers_safe_to_release(api, dma, stream)
        hold = not dma_safe_to_release
        evidence.update({
            "status": "BOARD_STREAM_FAIL_HOLD" if hold else "BOARD_STREAM_FAILED",
            "board_accessed": bool(api.board_accessed),
            "pynq_imported": bool(api._pynq is not None),
            "buffers_released": False,
            "hold_reason": ("DMA ownership or channel-idle state is uncertain; retain all CMA buffers"
                            if hold else None),
            "error": f"{type(exc).__name__}: {exc}",
        })
        if hold:
            if capture_writer is not None and not capture_writer._closed:
                evidence["capture_status"] = "INCOMPLETE_FAIL_HOLD"
                evidence["capture_error"] = (
                    "capture was interrupted before writer finalization because DMA safety requires fail-hold; "
                    "the dataset manifest remains non-COMPLETE and must not be replayed"
                )
            # fail_hold may wait indefinitely by design. Never wait on storage or
            # close preview resources before establishing the DMA fail-hold.
            api.fail_hold(args.evidence_json, evidence)
        if capture_writer is not None:
            try:
                capture_result = capture_writer.close()
                evidence["capture"] = capture_result
                evidence["capture_status"] = capture_result["status"]
            except BaseException as capture_error:
                evidence["capture_status"] = "INCOMPLETE"
                evidence["capture_error"] = f"{type(capture_error).__name__}: {capture_error}"
        if preview_publisher is not None:
            preview_publisher.close()
        if loopback_client is not None:
            loopback_client.close()
        if preview is not None:
            preview.close()
        if tx_buffers or rx_buffers:
            _release_buffers(api, tx_buffers + rx_buffers, released_buffer_ids)
            evidence["buffers_released"] = True
            try:
                evidence["cma_free_after_release_kib"] = api.cma_free_kib()
                evidence["cma_free_delta_kib"] = (evidence["cma_free_after_release_kib"]
                                                     - evidence["cma_free_before_kib"])
            except BaseException:
                evidence["cma_free_after_release_kib"] = None
        if not Path(args.evidence_json).exists():
            _write_json_exclusive(Path(args.evidence_json), evidence)
        raise
    finally:
        if preview_publisher is not None:
            preview_publisher.close()
        if loopback_client is not None:
            loopback_client.close()
        if preview is not None:
            preview.close()


def _release_buffers(api: Any, buffers: tuple[Any, ...], released: set[int] | None = None) -> None:
    if released is None:
        released = set()
    for buffer in buffers:
        identity = id(buffer)
        if identity in released:
            continue
        api.free(buffer)
        released.add(identity)


def _dma_buffers_safe_to_release(api: Any, dma: Any,
                                 stream: DoubleBufferStream | None) -> bool:
    """Release CMA only when no transfer could still own the backing memory."""
    if dma is None:
        return True
    if stream is not None and stream.dma_may_be_active:
        return False
    try:
        s2mm_status = api.read32(preflight.DMA_BASE, 0x34)
        mm2s_status = api.read32(preflight.DMA_BASE, 0x04)
    except BaseException:
        return False
    safe_mask = board_once.DMA_IDLE
    for status in (s2mm_status, mm2s_status):
        if status & board_once.DMA_ERROR_MASK or not status & safe_mask:
            return False
    return True


def verify_afi1_readback(snapshot: Mapping[str, Any]) -> dict[str, str]:
    """Require the exact Stage 1 AFI1 readback before constructing AXI DMA."""
    if not isinstance(snapshot, Mapping) or snapshot.get("afi1_status") != "READ":
        raise preflight.PreflightError("AFI1 was not readable after the Stage 1 Overlay PASS")
    registers = snapshot.get("registers")
    if not isinstance(registers, Mapping):
        raise preflight.PreflightError("AFI1 register snapshot is malformed")
    actual: dict[str, str] = {}
    for name, expected in AFI1_EXPECTED_READBACK.items():
        register = registers.get(name)
        value = register.get("value") if isinstance(register, Mapping) else None
        if isinstance(value, bool) or not isinstance(value, int) or value != expected:
            rendered = "unavailable" if value is None else f"0x{value:08x}"
            raise preflight.PreflightError(
                f"{name} is {rendered}; expected exact Stage 1 value 0x{expected:08x}"
            )
        actual[name] = f"0x{value:08x}"
    return actual


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _count_accuracy(predicted: int, ground_truth: int | None) -> float | None:
    if ground_truth is None:
        return None
    if isinstance(ground_truth, bool) or not isinstance(ground_truth, int) or ground_truth < 0:
        raise ValueError("ground_truth_count must be a nonnegative integer")
    if ground_truth == 0:
        return 100.0 if predicted == 0 else 0.0
    return round(max(0.0, 100.0 * (1.0 - abs(predicted - ground_truth) / ground_truth)), 4)


def _count_accuracy_fraction(predicted: int, expected_count: int | None) -> float | None:
    if expected_count is None:
        return None
    if (isinstance(expected_count, bool) or not isinstance(expected_count, int)
            or expected_count < 0):
        raise ValueError("expected_count must be a nonnegative integer")
    return 1.0 - abs(predicted - expected_count) / max(expected_count, 1)


def summarize_roi_subtimings_ms(results: tuple[Any, ...] | list[Any]) -> dict[str, Any]:
    """Summarize the per-update validation, projection, CCL, and tracking timers."""
    names = ("validate_ms", "projection_ms", "ccl_ms", "track_ms")
    summary: dict[str, Any] = {}
    for name in names:
        samples = [float(result.roi_subtimings_ms[name]) for result in results
                   if name in result.roi_subtimings_ms]
        summary[name] = round(sum(samples) / len(samples), 3) if samples else None
        summary[f"{name.removesuffix('_ms')}_timing_samples"] = len(samples)
    ccl_results = [result for result in results if result.ccl_executed]
    ccl_times = [float(result.roi_subtimings_ms["ccl_ms"]) for result in ccl_results]
    summary["ccl_invocations"] = len(ccl_results)
    summary["ccl_avg_ms_when_invoked"] = (
        round(sum(ccl_times) / len(ccl_times), 3) if ccl_times else None
    )
    summary["roi_foreground_pixels_max"] = max(
        (int(result.roi_foreground_pixels) for result in results), default=0
    )
    return summary


def summarize_stage_timings_ms(timings: Mapping[str, list[float]]) -> dict[str, Any]:
    """Return per-frame average stage durations and sample counts in milliseconds."""
    result: dict[str, Any] = {}
    for stage in ("capture", "mapping", "encode", "dma", "rx_sync", "rx_copy", "packer_check", "roi_count",
                  "mjpeg_encode", "mjpeg_handoff", "mjpeg_background_encode", "postprocess"):
        samples = timings.get(stage, [])
        result[f"avg_{stage}_ms"] = (
            round(sum(samples) / len(samples), 3) if samples else None
        )
        result[f"{stage}_timing_samples"] = len(samples)
    return result


def _nonnegative_count_argument(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected count must be an integer") from exc
    if result < 0:
        raise argparse.ArgumentTypeError("expected count must be nonnegative")
    return result


class PixelOverrideAction(argparse.Action):
    """Remember explicit pixel overrides even when they equal parser defaults."""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        selected = set(getattr(namespace, "_pixel_overrides", ()))
        selected.add(self.dest)
        namespace._pixel_overrides = tuple(sorted(selected))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WO-0016 asynchronous double-buffer Binary8 counter")
    parser.add_argument("--mode", choices=("mock", "board"), default="mock")
    parser.add_argument("--counting-profile", choices=("legacy", "full-width-mixed", "lighting-calibrated"), default="legacy",
                        help="lighting-calibrated uses camera-angle calibrated ROI/threshold/area settings; full-width-mixed retains the earlier full-frame profile")
    parser.add_argument("--reference-area", type=float, default=REFERENCE_AREA,
                        help="isolated flat M6 nut area in output pixels; 111 preserves the recorded profile")
    parser.add_argument("--lighting-threshold", type=int, default=DEFAULT_THRESHOLD,
                        help="lighting Gray8 foreground is >T; fixed PL threshold stays unchanged")
    parser.add_argument("--motion-timebase-s", type=float,
                        help="opt in to host capture-time displacement gates; nominal seconds per frame")
    parser.add_argument("--lighting-area-filter", choices=("source", "output-roi"), default="source",
                        help="source runs 640x360 RETR_CCOMP net-area filtering; output-roi skips it and uses the existing 320x180 connected-component >=15px gate")
    parser.add_argument("--wo0017-profile", choices=("b1", "b2"))
    parser.add_argument("--wo0017-ready-id",
                        help="new immutable READY record ID; omitting it retains the original ready.json")
    parser.add_argument("--physical-calibration", action="store_true",
                        help="use the independent one-shot calibration lock; no formal Permit is read or consumed")
    parser.add_argument("--calibration-id",
                        help="unique calibration identity scoped below the current Boot ID")
    parser.add_argument("--calibration-initial-dmasr", type=lambda value: int(value, 0),
                        choices=(0x00000001, 0x00001002), default=0x00000001,
                        help="exact initial DMA status bound into the calibration lock")
    parser.add_argument("--calibration-lock",
                        help="absolute path to the private Boot-ID-scoped calibration lock")
    parser.add_argument("--worker-python", default=DEFAULT_WORKER_PYTHON,
                        help="exact PYNQ Python executable pinned to this calibration lock")
    parser.add_argument("--expected-boot-id")
    parser.add_argument("--stage1-receipt")
    parser.add_argument("--stage1-receipt-sha256")
    parser.add_argument("--mock-frames", type=int, default=8)
    parser.add_argument("--camera", default="/dev/video0")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--stabilization-delay-s", type=float, default=0.0)
    parser.add_argument("--line-y", type=int, default=120)
    parser.add_argument("--count-axis", choices=("x", "y"), default="y")
    parser.add_argument("--line-x", type=int, default=160)
    parser.add_argument("--max-lateral-step", type=float, default=20.0)
    parser.add_argument("--max-area", type=int, default=900)
    parser.add_argument("--max-bbox-width", type=int, default=85, action=PixelOverrideAction)
    parser.add_argument("--max-bbox-height", type=int, default=85, action=PixelOverrideAction)
    parser.add_argument("--direction", type=int, choices=(-1, 1), default=1)
    parser.add_argument("--min-area", type=int, default=12)
    parser.add_argument("--hysteresis", type=float, default=2.0, action=PixelOverrideAction)
    parser.add_argument("--confirm-hits", type=int, default=2)
    parser.add_argument("--max-step", type=float, default=24.0)
    parser.add_argument("--component-cluster-dx", type=float, default=0.0,
                        help="maximum pairwise centroid X spread; zero disables clustering")
    parser.add_argument("--component-cluster-dy", type=float, default=12.0)
    parser.add_argument("--component-cluster-gap", type=float, default=16.0,
                        help="maximum Euclidean edge gap between bounding boxes for union-find clustering")
    parser.add_argument("--cooldown-frames", type=int, default=0,
                        help="inclusive same-corridor count suppression window; zero disables")
    parser.add_argument("--cooldown-lateral", type=float, default=15.0)
    parser.add_argument("--edge-max-missing", type=int, default=0,
                        help="extra missing-frame budget for confirmed clipped X tracks only")
    parser.add_argument("--prediction-max-missing", type=int, default=2,
                        help="bounded missed-frame association budget for confirmed armed X tracks")
    parser.add_argument("--ground-truth-count", "--expected-count", dest="ground_truth_count",
                        type=_nonnegative_count_argument)
    parser.add_argument("--material-label")
    parser.add_argument("--measure-preview-client", action="store_true")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--timeout-s", type=float, default=5.0)
    parser.add_argument("--manifest")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorize-stage")
    parser.add_argument("--approve-once", action="store_true")
    parser.add_argument("--no-consume-guard", action="store_true",
                        help="validate the current Stage B-2 READY guard without modifying it; a separate single-attempt record is still consumed")
    parser.add_argument("--attempt-id",
                        help="unique Stage B-2 permit identity, such as attempt-2")
    parser.add_argument("--operator-prearm-delay-s", type=float,
                        default=STAGEB2_OPERATOR_PREARM_DELAY_S,
                        help="flushed operator countdown after camera warm-up and before the 3-second stabilization")
    parser.add_argument("--evidence-json", "--output-json", dest="evidence_json")
    parser.add_argument("--capture-dir",
                        help="opt-in lossless Gray8 input + raw Binary8 mask dataset directory")
    parser.add_argument("--capture-max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                        help="maximum total bytes for the optional evidence dataset")
    return parser


def main(argv: list[str] | None = None, *,
         operator_gate_sha256: str | None = None,
         operator_gate: Any | None = None) -> int:
    process_start_monotonic = time.monotonic()
    parser = _parser()
    args = parser.parse_args(argv)
    args.process_start_monotonic = process_start_monotonic
    try:
        if args.physical_calibration:
            validate_calibration_options(args)
        if args.no_consume_guard and args.mode != "board":
            raise ValueError("--no-consume-guard is only valid in explicitly authorized board mode")
        if args.no_consume_guard and not args.attempt_id:
            raise ValueError("--no-consume-guard requires a unique --attempt-id")
        if (args.no_consume_guard
                and args.operator_prearm_delay_s != STAGEB2_OPERATOR_PREARM_DELAY_S):
            raise ValueError(
                f"Stage B-2 Attempt #2 requires a {STAGEB2_OPERATOR_PREARM_DELAY_S:g}-second operator pre-arm window"
            )
        if args.mode == "mock" and args.capture_dir:
            raise ValueError("--capture-dir is available only for an explicitly authorized board stream")
        if args.mode == "mock":
            result = run_mock_simulation(frame_count=args.mock_frames,
                                         line_y=args.line_y,
                                         min_area=args.min_area,
                                         expected_count=args.ground_truth_count,
                                         material_label=args.material_label,
                                         counter_config=(counting_configuration(args)
                                             if args.counting_profile in ("full-width-mixed", "lighting-calibrated") else None),
                                         preprocessing_config=preprocessing_configuration(args))
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["status"] == "MOCK_PASS" else 1
        result = execute_board_stream(args, operator_gate_sha256=operator_gate_sha256,
                                      operator_gate=operator_gate)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, preflight.PreflightError) as exc:
        print(json.dumps({"status": "BOARD_STREAM_REJECTED", "board_accessed": False,
                          "pynq_imported": False, "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
