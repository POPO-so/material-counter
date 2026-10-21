"""WO-0016 continuous camera-to-DMA stream counter entry point.

Default mode is a host-only synthetic simulation. Physical access requires the
exact one-shot switches, a hash-pinned WO-0015 50 MHz manifest, and a fresh
board-side preflight. This module never downloads/reloads the Overlay.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import queue
import re
import stat as stat_module
import sys
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping

import cv2
import numpy as np

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
from mjpeg_preview import (
    PREVIEW_FRAME_INTERVAL,
    PREVIEW_JPEG_QUALITY,
    MjpegFrameHub,
    MjpegLoopbackClient,
    MjpegPreviewServer,
    PeriodicMjpegPublisher,
    render_preview_jpeg,
)
from stream_counter import (
    HEIGHT,
    RX_BYTES,
    TX_BYTES,
    WIDTH,
    BufferState,
    DoubleBufferStream,
    MaskCounter,
    allocate_ping_pong_buffers,
)

WO16_STAGE = "wo0016-stream"
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
                        material_label: str | None = None) -> dict[str, Any]:
    """Run synthetic Binary8 masks through the actual counter and JPEG renderer."""
    counter = MaskCounter(line_y=line_y, direction=direction,
                          min_area=min_area, max_step=24.0)
    hub = MjpegFrameHub()
    preview = MjpegPreviewServer(hub=hub, host="127.0.0.1", port=0)
    results = []
    start = time.perf_counter()
    preview.start()
    try:
        last_jpeg = None
        for frame_id, mask in enumerate(build_synthetic_masks(frame_count=frame_count,
                                                               line_y=line_y,
                                                               direction=direction)):
            result = counter.update(frame_id, mask)
            last_jpeg = render_preview_jpeg(mask, total_count=result.total_count,
                                            line_y=line_y)
            hub.publish(last_jpeg)
            results.append(result)
    finally:
        preview.close()
    elapsed = max(time.perf_counter() - start, 1e-9)
    predicted = results[-1].total_count if results else 0
    # Synthetic ground truth is kept outside MaskCounter and is used only for scoring.
    ground_truth = (2 if frame_count >= 2 else 0) if expected_count is None else expected_count
    return {
        "schema": 1,
        "work_order": "WO-0016",
        "status": "MOCK_PASS" if predicted == ground_truth else "MOCK_COUNT_MISMATCH",
        "mode": "offline-synthetic",
        "board_accessed": False,
        "pynq_imported": False,
        "frame_geometry": [HEIGHT, WIDTH],
        "line_y": line_y,
        "min_area": min_area,
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
              "max_step": args.max_step, "max_lateral_step": args.max_lateral_step,
              "hysteresis": args.hysteresis, "confirm_hits": args.confirm_hits,
              "component_cluster_dx": args.component_cluster_dx,
              "component_cluster_dy": args.component_cluster_dy,
              "cooldown_frames": args.cooldown_frames,
              "cooldown_lateral": args.cooldown_lateral,
              "edge_max_missing": args.edge_max_missing}
    MaskCounter(**config)  # Validate before consuming the one-shot attempt.
    return config


def stageb2_attempt_permit_identity(*, ready_identity: Mapping[str, Any],
                                    ready_guard_sha256: str,
                                    attempt_id: str,
                                    operator_prearm_delay_s: float,
                                    runner_sha256: str,
                                    counting_config: Mapping[str, Any] | None = None,
                                    counter_sha256: str | None = None) -> dict[str, Any]:
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
    extra = {}
    if counting_config is not None:
        MaskCounter(**dict(counting_config))
        if counter_sha256 is None or not re.fullmatch(r"[0-9a-f]{64}", counter_sha256):
            raise ValueError("counting configuration requires counter SHA-256")
        extra = {"counting_config": dict(counting_config), "counter_sha256": counter_sha256}
    return {
        **dict(ready_identity),
        "stage": STAGEB2_ATTEMPT_STAGE,
        "ready_guard_sha256": ready_guard_sha256,
        "attempt_id": attempt_id,
        "operator_prearm_delay_s": float(operator_prearm_delay_s),
        "runner_sha256": runner_sha256,
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
                         evidence_json: str | None) -> None:
    if execute is not True or approve_once is not True or authorize_stage != WO16_STAGE:
        raise ValueError("board mode requires --execute --authorize-stage wo0016-stream --approve-once")
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


async def camera_gray_frames(device: str, *, max_frames: int,
                             cv2_module: Any = cv2,
                             read_timeout_s: float = 5.0,
                             stabilization_delay_s: float = 0.0,
                             operator_prearm_delay_s: float = 0.0,
                             milestone_callback: Callable[[str, float], None] | None = None,
                             capture_timings_ms: list[float] | None = None,
                             steady_start_time: list[float] | None = None,
                             capture_metadata: dict[str, Any] | None = None
                             ) -> AsyncIterator[np.ndarray]:
    """Warm up and yield ordered Gray8 frames with one-frame camera prefetch."""
    if isinstance(max_frames, bool) or not isinstance(max_frames, int) or max_frames < 1:
        raise ValueError("max_frames must be a positive integer")
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
        def read_gray_frame(*, warmup: bool) -> tuple[np.ndarray, float]:
            capture_started = time.perf_counter()
            ok, frame = capture.read()
            if not ok or frame is None:
                prefix = "V4L2 warm-up" if warmup else "V4L2"
                raise RuntimeError(f"{prefix} returned no camera frame")
            image = np.asarray(frame)
            if warmup:
                return image, (time.perf_counter() - capture_started) * 1000.0
            if image.ndim == 3 and image.shape[2] in (1, 3, 4):
                image = image[:, :, 0]
            if image.dtype != np.uint8 or image.shape != (400, 640):
                raise RuntimeError(f"camera frame geometry is {image.shape}; expected (400, 640)")
            gray = cv2_module.resize(
                image, (WIDTH, HEIGHT), interpolation=cv2_module.INTER_NEAREST
            )
            gray = np.asarray(gray)
            if gray.dtype != np.uint8 or gray.shape != (HEIGHT, WIDTH):
                raise RuntimeError(
                    f"resized camera frame is {gray.dtype} {gray.shape}; "
                    f"expected uint8 {(HEIGHT, WIDTH)}"
                )
            return gray, (time.perf_counter() - capture_started) * 1000.0

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
                warmup, waiter = task
                try:
                    value = read_gray_frame(warmup=warmup)
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

        async def get_frame(*, warmup: bool) -> tuple[np.ndarray, float]:
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

        def prefetch(*, warmup: bool) -> None:
            nonlocal pending_waiter
            if tasks is None or loop is None or pending_waiter is not None:
                raise RuntimeError("camera prefetch slot is not available")
            pending_waiter = loop.create_future()
            tasks.put_nowait((warmup, pending_waiter))

        warmup_frames = 2
        for _ in range(warmup_frames):
            prefetch(warmup=True)
            await get_frame(warmup=True)
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
        emit_milestone("CAMERA_OPEN_READY_FOR_OPERATOR")
        _countdown_wait(float(operator_prearm_delay_s), label="OPERATOR_PREARM")
        emit_milestone("CAMERA_OPEN_AND_STABILIZING_START")
        _countdown_wait(float(stabilization_delay_s), label="STABILIZATION")
        if steady_start_time is not None:
            steady_start_time.clear()

        for frame_index in range(max_frames):
            if pending_waiter is None:
                if frame_index == 0:
                    emit_milestone("FRAME_0_CAPTURE_START_FEED_NOW")
                    if steady_start_time is not None:
                        steady_start_time[:] = [time.perf_counter()]
                prefetch(warmup=False)
            gray, capture_ms = await get_frame(warmup=False)
            if capture_timings_ms is not None:
                capture_timings_ms.append(capture_ms)
            if frame_index + 1 < max_frames:
                prefetch(warmup=False)
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


def execute_board_stream(args: argparse.Namespace) -> dict[str, Any]:
    """Run one finite, guarded streaming session against the already-loaded WO-0015 BIT."""
    require_board_opt_in(
        execute=args.execute,
        authorize_stage=args.authorize_stage,
        approve_once=args.approve_once,
        manifest=args.manifest,
        manifest_sha256=args.manifest_sha256,
        max_frames=args.max_frames,
        evidence_json=args.evidence_json,
    )
    process_start_monotonic = getattr(args, "process_start_monotonic", time.monotonic())
    milestone_path = Path(args.evidence_json).with_suffix(".milestones.jsonl")
    if milestone_path.exists():
        raise FileExistsError("milestone log path already exists; choose a new attempt result path")
    plan = preflight.validate_manifest(args.manifest, args.manifest_sha256)
    if not plan.ready_for_board:
        raise preflight.PreflightError("manifest is not marked ready_for_board")
    if plan.manifest.get("clock_mhz") != 50:
        raise preflight.PreflightError("WO-0016 is locked to the WO-0015 50 MHz image")
    stage1_receipt = board_once.require_overlay_pass_receipt(
        board_once.OVERLAY_PASS_RECEIPT, plan
    )
    live_boot = preflight.read_boot_id()
    if live_boot != plan.boot_id:
        raise preflight.PreflightError("live boot_id does not match the pinned WO-0015 manifest")
    meminfo = preflight.read_cma_meminfo()
    if meminfo["CmaFree"] < CMA_WATERMARK_KIB:
        raise preflight.PreflightError("CmaFree is below the WO-0016 pre-allocation watermark")
    admission = preflight.read_cma_admission(meminfo=meminfo)
    ownership = preflight.audit_process_ownership()
    manager = preflight.read_fpga_manager()
    guard_dir = WO16_GUARD.parent
    if guard_dir.is_symlink():
        raise preflight.PreflightError("WO-0016 guard directory must not be a symlink")
    guard_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    guard_stat = guard_dir.stat()
    if guard_stat.st_uid != 0 or guard_stat.st_mode & 0o077:
        raise preflight.PreflightError("WO-0016 guard directory must be root-owned and mode 0700")
    stageb2_ready_guard = None
    stageb2_attempt_permit = None
    attempt_path = None
    if args.no_consume_guard:
        if not getattr(args, "attempt_id", None):
            raise ValueError("--no-consume-guard requires a unique --attempt-id")
        identity = stageb2_guard_identity(
            boot_id=plan.boot_id,
            manifest_sha256=plan.manifest_sha256,
            bit_sha256=plan.bit_sha256,
            hwh_sha256=plan.hwh_sha256,
            max_frames=args.max_frames,
            expected_count=args.ground_truth_count,
            material_label=args.material_label,
            stabilization_delay_s=args.stabilization_delay_s,
        )
        ready_path = STAGEB2_READY_GUARD_DIR / f"stageb2-ready-{plan.boot_id}.json"
        stageb2_ready_guard = validate_non_consuming_guard(ready_path, identity)
        attempt_identity = stageb2_attempt_permit_identity(
            ready_identity=identity,
            ready_guard_sha256=stageb2_ready_guard["sha256"],
            attempt_id=args.attempt_id,
            operator_prearm_delay_s=args.operator_prearm_delay_s,
            runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            counting_config=counting_configuration(args),
            counter_sha256=hashlib.sha256(Path(__file__).with_name("stream_counter.py").read_bytes()).hexdigest(),
        )
        attempt_directory = STAGEB2_ATTEMPT_DIR / plan.boot_id / args.attempt_id
        permit_path = attempt_directory / "attempt-permit.json"
        stageb2_attempt_permit = validate_stageb2_attempt_permit(
            permit_path, attempt_identity
        )
        attempt_path = attempt_directory / "consumed.json"
        guard = claim_once_guard(attempt_path, {
            "schema": 1,
            "work_order": "WO-0016",
            "stage": STAGEB2_ATTEMPT_STAGE,
            "boot_id": plan.boot_id,
            "attempt_id": args.attempt_id,
            "manifest_sha256": plan.manifest_sha256,
            "bit_sha256": plan.bit_sha256,
            "hwh_sha256": plan.hwh_sha256,
            "ready_guard_sha256": stageb2_ready_guard["sha256"],
            "attempt_permit_sha256": stageb2_attempt_permit["sha256"],
            "max_frames": args.max_frames,
            "expected_count": args.ground_truth_count,
            "material_label": args.material_label,
            "stabilization_delay_s": args.stabilization_delay_s,
            "operator_prearm_delay_s": args.operator_prearm_delay_s,
        })
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
    tx_buffers: tuple[Any, ...] = ()
    rx_buffers: tuple[Any, ...] = ()
    stream: DoubleBufferStream | None = None
    preview: MjpegPreviewServer | None = None
    preview_publisher: PeriodicMjpegPublisher | None = None
    loopback_client: MjpegLoopbackClient | None = None
    dma = None
    released_buffer_ids: set[int] = set()
    evidence: dict[str, Any] = {
        "schema": 1,
        "work_order": "WO-0016",
        "status": "WO0016_RUNNING",
        "board_accessed": False,
        "boot_id": plan.boot_id,
        "manifest_sha256": plan.manifest_sha256,
        "bit_sha256": plan.bit_sha256,
        "hwh_sha256": plan.hwh_sha256,
        "clock_mhz": 50,
        "guard": guard,
        "guard_mode": ("READY Guard validate-only; attempt permit consumed-once"
                       if args.no_consume_guard else "consume-once"),
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
        "max_frames": args.max_frames,
        "camera_device": args.camera,
        "camera_mode": "MJPG 640x400 resized to Gray8 320x240",
        "xilinx_xrt": os.environ.get("XILINX_XRT"),
        "line_y": args.line_y,
        "counting_config": counting_configuration(args),
        "direction": args.direction,
        "min_area": args.min_area,
        "ground_truth_count": args.ground_truth_count,
        "expected_count": args.ground_truth_count,
        "material_label": args.material_label,
        "stabilization_delay_s": args.stabilization_delay_s,
        "operator_prearm_delay_s": (args.operator_prearm_delay_s
                                     if args.no_consume_guard else 0.0),
        "threshold": STAGEB2_THRESHOLD,
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
            if status & board_once.DMA_ERROR_MASK:
                raise preflight.PreflightError(f"{label} DMA begins with an error status")
            if not (status & board_once.DMA_HALTED) and not (status & board_once.DMA_IDLE):
                raise preflight.PreflightError(f"{label} DMA is already active")
        if api.cma_free_kib() < CMA_WATERMARK_KIB:
            raise preflight.PreflightError("CmaFree fell below watermark before allocation")

        def allocate_with_watermark(*, shape: tuple[int, ...], dtype: Any) -> Any:
            if api.cma_free_kib() < CMA_WATERMARK_KIB:
                raise preflight.PreflightError("CmaFree fell below watermark before a buffer allocation")
            buffer = api.allocate(int(np.prod(shape)))
            if api.cma_free_kib() < CMA_WATERMARK_KIB:
                raise preflight.PreflightError("CmaFree fell below watermark after a buffer allocation")
            return buffer

        tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocate_with_watermark)
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

        def verify_frame(frame_id: int, slot: Any) -> None:
            if int(dma.recvchannel.transferred) != RX_BYTES:
                raise RuntimeError(f"frame {frame_id} S2MM transferred length is not {RX_BYTES}")
            verify_frame_dmasr(api, frame_id, frame_dma_status)
            status = api.read32(preflight.CHECKER_BASE, board_once.PACKER_STATUS)
            accepted = api.read32(preflight.CHECKER_BASE, board_once.PACKER_ACCEPTED_PIXELS)
            sent = api.read32(preflight.CHECKER_BASE, board_once.PACKER_SENT_BEATS)
            error_code = api.read32(preflight.CHECKER_BASE, board_once.PACKER_ERROR_CODE)
            if status & board_once.PACKER_FAULT or status & board_once.PACKER_BUSY:
                raise RuntimeError(f"frame {frame_id} Packer is faulted or still busy")
            if not status & board_once.PACKER_DONE or accepted != RX_BYTES or sent != RX_BYTES // 4 or error_code:
                raise RuntimeError(f"frame {frame_id} Packer frame counters/status are inconsistent")

        hub = MjpegFrameHub()
        preview = MjpegPreviewServer(hub=hub, host=args.host, port=args.port).start()
        stage_timings_ms: dict[str, list[float]] = {stage: [] for stage in (
            "capture", "encode", "dma", "rx_sync", "rx_copy", "packer_check", "roi_count",
            "mjpeg_encode", "mjpeg_handoff", "mjpeg_background_encode", "postprocess")}
        preview_publisher = PeriodicMjpegPublisher(
            hub=hub, line_y=args.line_y, count_axis=args.count_axis, line_x=args.line_x,
            interval=PREVIEW_FRAME_INTERVAL, quality=PREVIEW_JPEG_QUALITY,
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

        counter = MaskCounter(**counting_configuration(args))
        stream = DoubleBufferStream(
            recv_channel=dma.recvchannel,
            send_channel=dma.sendchannel,
            tx_buffers=tx_buffers,
            rx_buffers=rx_buffers,
            counter=counter,
            packet_encoder=gray320x240_to_rgb_packet,
            packet_encoder_into=gray320x240_to_rgb_packet_into,
            before_transfer=arm_frame,
            before_rx_copy=lambda frame_id, slot: api.sync_from_device(slot.rx_buffer),
            after_transfer=verify_frame,
            on_frame=preview_publisher,
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
            milestone_callback=record_camera_milestone,
            capture_timings_ms=stage_timings_ms["capture"],
            steady_start_time=steady_start_time,
            capture_metadata=capture_metadata)))
        if not steady_start_time:
            raise RuntimeError("camera did not establish a steady-stream timing boundary")
        elapsed = max(time.perf_counter() - steady_start_time[0], 1e-9)
        preview_publisher.close()
        if loopback_client is not None:
            loopback_client.close()
        total_count = results[-1].total_count if results else 0
        accuracy = _count_accuracy(total_count, args.ground_truth_count)
        accuracy_fraction = _count_accuracy_fraction(total_count, args.ground_truth_count)
        s2mm_status = api.read32(preflight.DMA_BASE, 0x34)
        mm2s_status = api.read32(preflight.DMA_BASE, 0x04)
        for status, label in ((s2mm_status, "S2MM"), (mm2s_status, "MM2S")):
            if status & board_once.DMA_ERROR_MASK or not status & board_once.DMA_IDLE:
                raise RuntimeError(f"{label} is not error-free and idle at normal shutdown")
        evidence.update({
            "status": "BOARD_STREAM_COMPLETE",
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
            "frame_dma_status": frame_dma_status,
            "packer_output_frames_verified": len(results),
            "pynq_imported": True,
            "accuracy_formula": "1 - abs(total_count - expected_count) / max(expected_count, 1)",
            "note": "Measured only for the supplied finite live-camera interval; no automatic retry/reset/reload.",
        })
        _release_buffers(api, tx_buffers + rx_buffers, released_buffer_ids)
        evidence["cma_free_after_release_kib"] = api.cma_free_kib()
        evidence["cma_free_delta_kib"] = (evidence["cma_free_after_release_kib"]
                                             - evidence["cma_free_before_kib"])
        evidence["buffers_released"] = True
        _write_json_exclusive(Path(args.evidence_json), evidence)
        return evidence
    except BaseException as exc:
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
        if preview_publisher is not None:
            preview_publisher.close()
        if loopback_client is not None:
            loopback_client.close()
        if preview is not None:
            preview.close()
        if hold:
            api.fail_hold(args.evidence_json, evidence)
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
    for stage in ("capture", "encode", "dma", "rx_sync", "rx_copy", "packer_check", "roi_count",
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WO-0016 asynchronous double-buffer Binary8 counter")
    parser.add_argument("--mode", choices=("mock", "board"), default="mock")
    parser.add_argument("--mock-frames", type=int, default=8)
    parser.add_argument("--camera", default="/dev/video0")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--stabilization-delay-s", type=float, default=0.0)
    parser.add_argument("--line-y", type=int, default=120)
    parser.add_argument("--count-axis", choices=("x", "y"), default="y")
    parser.add_argument("--line-x", type=int, default=160)
    parser.add_argument("--max-lateral-step", type=float, default=20.0)
    parser.add_argument("--max-area", type=int)
    parser.add_argument("--direction", type=int, choices=(-1, 1), default=1)
    parser.add_argument("--min-area", type=int, default=12)
    parser.add_argument("--hysteresis", type=float, default=2.0)
    parser.add_argument("--confirm-hits", type=int, default=2)
    parser.add_argument("--max-step", type=float, default=24.0)
    parser.add_argument("--component-cluster-dx", type=float, default=0.0,
                        help="maximum pairwise centroid X spread; zero disables clustering")
    parser.add_argument("--component-cluster-dy", type=float, default=12.0)
    parser.add_argument("--cooldown-frames", type=int, default=0,
                        help="inclusive same-corridor count suppression window; zero disables")
    parser.add_argument("--cooldown-lateral", type=float, default=15.0)
    parser.add_argument("--edge-max-missing", type=int, default=0,
                        help="extra missing-frame budget for confirmed clipped X tracks only")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    process_start_monotonic = time.monotonic()
    parser = _parser()
    args = parser.parse_args(argv)
    args.process_start_monotonic = process_start_monotonic
    try:
        if args.no_consume_guard and args.mode != "board":
            raise ValueError("--no-consume-guard is only valid in explicitly authorized board mode")
        if args.no_consume_guard and not args.attempt_id:
            raise ValueError("--no-consume-guard requires a unique --attempt-id")
        if (args.no_consume_guard
                and args.operator_prearm_delay_s != STAGEB2_OPERATOR_PREARM_DELAY_S):
            raise ValueError(
                f"Stage B-2 Attempt #2 requires a {STAGEB2_OPERATOR_PREARM_DELAY_S:g}-second operator pre-arm window"
            )
        if args.mode == "mock":
            result = run_mock_simulation(frame_count=args.mock_frames,
                                         line_y=args.line_y,
                                         min_area=args.min_area,
                                         expected_count=args.ground_truth_count,
                                         material_label=args.material_label)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["status"] == "MOCK_PASS" else 1
        result = execute_board_stream(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, preflight.PreflightError) as exc:
        print(json.dumps({"status": "BOARD_STREAM_REJECTED", "board_accessed": False,
                          "pynq_imported": False, "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
