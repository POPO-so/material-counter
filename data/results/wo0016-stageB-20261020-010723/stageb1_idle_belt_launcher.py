"""One-shot WO-0016 Stage B-1 idle-belt sampler.

This wrapper instruments the already-deployed WO-0016 runner without changing
the tracked project sources. The underlying runner still performs the normal
manifest, same-boot receipt, process, AFI1, CMA, DMA-address, Packer, and
buffer-ownership gates. This wrapper gives Stage B-1 its own consumed guard and
records optical/Binary8 metrics for every sampled frame.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
from typing import Any, AsyncIterator

import numpy as np

import run_stream_counter as runner
from stageb1_hwh import read_threshold


EXPECTED_BOOT_ID = "58e6af6c-6660-4d44-b0c5-e18f4a564202"
EXPECTED_MANIFEST_SHA256 = "e07316b9398166c6fc7a459faaca271eaf32170c18b4deaa4c16208bcaa933f2"
EXPECTED_BIT_SHA256 = "a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420"
EXPECTED_HWH_SHA256 = "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6"
STAGEB1_STAGE = "wo0016-stageB1-idle-belt-baseline"
STAGEB1_GUARD = Path(
    f"/var/lib/wo0016/stageb1-idle-belt-{EXPECTED_BOOT_ID}-rightward-xaxis-consumed.json"
)
GENERIC_GUARD = Path("/var/lib/wo0016/stream-counter-consumed.json")
CMA_WATERMARK_KIB = 32_768
# Rightward image motion crosses the vertical line at center x=160. Count only
# inside the centered 49-row horizontal ROI lane y=96..144. The existing
# WO-0016 counter is Y-oriented, so _RightwardMaskCounter crops this lane and
# transposes it before passing the mask to the unchanged counter implementation.
LINE_X = 160
RUNNER_TRANSPOSED_LINE_Y = LINE_X
ROI_TOP = 96
ROI_BOTTOM_EXCLUSIVE = 145
ROI_HEIGHT = ROI_BOTTOM_EXCLUSIVE - ROI_TOP
THRESHOLD = 128
REQUESTED_SAMPLE_DURATION_S = 7.5
REQUIRED_FRAMES = 115  # 114 intervals at TARGET_FPS are about 7.52 seconds.
EXPECTED_DMA_STATUS = 0x00001002
BELT_SPEED_MM_S = 160.0
BELT_LOOP_MM = 500.0
TARGET_FPS = 15.15
HANDOFF_STABILIZATION_DELAY_S = 3.0

OPTICAL_FRAMES: list[dict[str, Any]] = []
LUMINANCE_FRAMES: list[dict[str, Any]] = []
SNAPSHOT_IMAGES: dict[str, np.ndarray] = {}
FRAME_RESULTS: list[Any] = []
FRAME_DMASR: list[dict[str, Any]] = []
STREAM_TIMINGS: dict[str, list[float]] = {}
ACTIVE_STREAMS: list[Any] = []
RUNNER_ERROR: str | None = None
PREFLIGHT: dict[str, Any] = {}

_original_camera_gray_frames = runner.camera_gray_frames
_original_stream = runner.DoubleBufferStream
_original_counter = runner.MaskCounter
_original_claim_once_guard = runner.claim_once_guard
_original_execute_board_stream = runner.execute_board_stream
_original_fail_hold = runner.board_once.PynqBoardAPI.fail_hold


class _RightwardMaskCounter:
    """Adapt rightward image motion to the existing Y-oriented counter."""

    def __init__(self, *, line_y: int, min_area: int, direction: int,
                 hysteresis: float, confirm_hits: int, max_step: float):
        if line_y != RUNNER_TRANSPOSED_LINE_Y or direction != 1:
            raise RuntimeError("rightward counting requires transposed y=160, direction=+1")
        self._counter = _original_counter(
            width=ROI_HEIGHT,
            height=320,
            line_y=RUNNER_TRANSPOSED_LINE_Y,
            roi_half_height=160,
            min_area=min_area,
            direction=direction,
            hysteresis=hysteresis,
            confirm_hits=confirm_hits,
            max_step=max_step,
        )

    def update(self, frame_id: int, mask: np.ndarray) -> Any:
        image = np.asarray(mask)
        if image.dtype != np.uint8 or image.shape != (240, 320):
            raise RuntimeError("rightward counter adapter requires a uint8 240x320 mask")
        horizontal_lane = image[ROI_TOP:ROI_BOTTOM_EXCLUSIVE, :]
        if horizontal_lane.shape != (ROI_HEIGHT, 320):
            raise RuntimeError("rightward counter adapter produced an unexpected ROI lane")
        return self._counter.update(frame_id, np.ascontiguousarray(horizontal_lane.T))


class _ObservedCapture:
    """Pass through V4L2 reads while measuring BGR-derived optical luminance."""

    def __init__(self, *args: Any, **kwargs: Any):
        self._capture = runner.cv2.VideoCapture(*args, **kwargs)
        self._read_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._capture, name)

    def read(self) -> tuple[bool, Any]:
        ok, frame = self._capture.read()
        if ok and frame is not None and self._read_count >= 2:
            image = np.asarray(frame)
            if image.dtype != np.uint8 or image.shape[:2] != (400, 640):
                raise RuntimeError(
                    f"raw camera frame has unexpected geometry/type: {image.shape}/{image.dtype}"
                )
            if image.ndim == 3 and image.shape[2] == 3:
                luminance = runner.cv2.cvtColor(image, runner.cv2.COLOR_BGR2GRAY)
                luminance_source = "OpenCV COLOR_BGR2GRAY from decoded V4L2 MJPG"
            elif image.ndim == 3 and image.shape[2] in (1, 4):
                luminance = image[:, :, 0]
                luminance_source = "raw camera channel 0 fallback"
            elif image.ndim == 2:
                luminance = image
                luminance_source = "camera already supplied Gray8"
            else:
                raise RuntimeError(f"unsupported raw camera frame shape: {image.shape}")
            resized = runner.cv2.resize(
                luminance, (320, 240), interpolation=runner.cv2.INTER_NEAREST
            )
            roi = resized[ROI_TOP:ROI_BOTTOM_EXCLUSIVE, :]
            frame_id = len(LUMINANCE_FRAMES)
            LUMINANCE_FRAMES.append({
                "frame_id": frame_id,
                "full_frame_mean": float(np.mean(resized, dtype=np.float64)),
                "roi_mean": float(np.mean(roi, dtype=np.float64)),
                "roi_pixel_peak": int(np.max(roi)),
                "source": luminance_source,
            })
            if frame_id in (0, 50, REQUIRED_FRAMES - 1):
                SNAPSHOT_IMAGES[f"luminance-frame-{frame_id:03d}"] = resized.copy()
        self._read_count += 1
        return ok, frame


class _ObservedCV2:
    """cv2 proxy that replaces only VideoCapture and delegates all other APIs."""

    def __getattr__(self, name: str) -> Any:
        if name == "VideoCapture":
            return _ObservedCapture
        return getattr(runner.cv2, name)


_OBSERVED_CV2 = _ObservedCV2()


def _run_readonly(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    result = {
        "argv": command,
        "returncode": int(completed.returncode),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }
    if completed.returncode != 0:
        raise RuntimeError(
            f"read-only preflight command failed ({command[0]}): "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return result


def _read_cma_free_kib() -> int:
    text = Path("/proc/meminfo").read_text(encoding="ascii")
    match = re.search(r"^CmaFree:\s*(\d+)\s+kB$", text, flags=re.MULTILINE)
    if match is None:
        raise RuntimeError("CmaFree is unavailable in /proc/meminfo")
    return int(match.group(1))


def _read_camera_preflight(args: Any) -> dict[str, Any]:
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if boot_id != EXPECTED_BOOT_ID:
        raise RuntimeError(f"live boot_id {boot_id} differs from approved boot")
    if os.geteuid() != 0:
        raise RuntimeError("Stage B-1 launcher must run with the authorized root context")
    if (args.mode != "board" or not args.execute or not args.approve_once
            or args.authorize_stage != runner.WO16_STAGE):
        raise RuntimeError("Stage B-1 requires the explicit one-shot board authorization flags")
    if (args.manifest_sha256 != EXPECTED_MANIFEST_SHA256
            or args.manifest is None or not Path(args.manifest).is_file()):
        raise RuntimeError("Stage B-1 requires the reviewed WO-0015 manifest path and SHA-256")
    plan = runner.preflight.validate_manifest(args.manifest, args.manifest_sha256)
    if (plan.boot_id != EXPECTED_BOOT_ID or plan.bit_sha256 != EXPECTED_BIT_SHA256
            or plan.hwh_sha256 != EXPECTED_HWH_SHA256 or not plan.ready_for_board):
        raise RuntimeError("WO-0015 manifest identity does not match the approved same-boot image")
    actual_threshold = read_threshold(plan.hwh_path)
    if actual_threshold != THRESHOLD:
        raise RuntimeError("reviewed HWH does not confirm the Stage B-1 threshold of 128")
    evidence_path = Path(args.evidence_json) if args.evidence_json else None
    if evidence_path is None or not evidence_path.is_absolute():
        raise RuntimeError("Stage B-1 requires an absolute evidence JSON path")
    if not evidence_path.parent.is_dir():
        raise RuntimeError("Stage B-1 evidence directory must already exist")
    if evidence_path.exists():
        raise FileExistsError(f"refusing to overwrite Stage B-1 evidence: {evidence_path}")
    if evidence_path.with_suffix(evidence_path.suffix + ".stageb1-tmp").exists():
        raise FileExistsError("refusing to reuse an existing Stage B-1 evidence staging file")
    for frame_id in (0, 50, REQUIRED_FRAMES - 1):
        for kind in ("pipeline", "luminance"):
            snapshot = evidence_path.with_name(
                f"{evidence_path.stem}-{kind}-frame-{frame_id:03d}.png"
            )
            if snapshot.exists():
                raise FileExistsError(f"refusing to overwrite Stage B-1 snapshot: {snapshot}")
    if (args.line_y != RUNNER_TRANSPOSED_LINE_Y or args.direction != 1 or args.min_area != 12
            or args.hysteresis != 2.0 or args.confirm_hits != 2 or args.max_step != 24.0
            or args.max_frames != REQUIRED_FRAMES):
        raise RuntimeError("Stage B-1 parameters differ from the reviewed 115-frame, 7.5-second profile")
    if args.ground_truth_count != 0 or args.camera != "/dev/video0":
        raise RuntimeError("Stage B-1 requires /dev/video0 and an empty-belt count of zero")
    if STAGEB1_GUARD.exists():
        raise FileExistsError(f"Stage B-1 one-shot guard already exists: {STAGEB1_GUARD}")

    cma_free_kib = _read_cma_free_kib()
    if cma_free_kib < CMA_WATERMARK_KIB:
        raise RuntimeError(
            f"pre-capture CmaFree={cma_free_kib} KiB is below {CMA_WATERMARK_KIB} KiB"
        )

    fuser_path = shutil.which("fuser")
    if fuser_path is None:
        raise RuntimeError("fuser is required to verify /dev/video0 is released")
    fuser = subprocess.run([fuser_path, "-v", "/dev/video0"],
                           capture_output=True, text=True, check=False)
    if fuser.stdout.strip() or fuser.stderr.strip():
        raise RuntimeError(
            "/dev/video0 still has an owner after the preview stop: "
            f"{fuser.stdout.strip()} {fuser.stderr.strip()}".strip()
        )

    ss_path = shutil.which("ss")
    if ss_path is None:
        raise RuntimeError("ss is required to verify TCP/8080 is free")
    ss = subprocess.run([ss_path, "-H", "-ltn", "sport = :8080"],
                        capture_output=True, text=True, check=False)
    if ss.returncode != 0 or ss.stdout.strip():
        raise RuntimeError(
            f"TCP/8080 is not confirmed free: {ss.stdout.strip()} {ss.stderr.strip()}".strip()
        )

    v4l2 = shutil.which("v4l2-ctl")
    if v4l2 is None:
        raise RuntimeError("v4l2-ctl is required for camera-mode/exposure readback")
    fmt = _run_readonly([v4l2, "-d", args.camera, "--get-fmt-video"])
    parm = _run_readonly([v4l2, "-d", args.camera, "--get-parm"])
    auto_exposure = _run_readonly(
        [v4l2, "-d", args.camera, "--get-ctrl=auto_exposure"]
    )
    exposure_time = _run_readonly(
        [v4l2, "-d", args.camera, "--get-ctrl=exposure_time_absolute"]
    )

    if not re.search(r"Width/Height\s*:\s*640/400", fmt["stdout"]):
        raise RuntimeError("V4L2 readback is not 640x400")
    if not re.search(r"Pixel Format\s*:\s*'MJPG'", fmt["stdout"]):
        raise RuntimeError("V4L2 readback is not MJPG")
    auto_value = re.search(r"auto_exposure\s*:\s*(\d+)", auto_exposure["stdout"])
    exposure_value = re.search(
        r"exposure_time_absolute\s*:\s*(\d+)", exposure_time["stdout"]
    )
    if auto_value is None or int(auto_value.group(1)) != 1:
        raise RuntimeError("camera auto_exposure is not confirmed as manual mode 1")
    if exposure_value is None or int(exposure_value.group(1)) != 50:
        raise RuntimeError("camera exposure_time_absolute is not locked at 50 (5.0 ms)")

    generic_guard = None
    if GENERIC_GUARD.is_file():
        raw = GENERIC_GUARD.read_bytes()
        generic_guard = {
            "path": str(GENERIC_GUARD),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content": json.loads(raw.decode("utf-8")),
            "reused": False,
        }

    return {
        "boot_id": boot_id,
        "manifest_sha256": plan.manifest_sha256,
        "bit_sha256": plan.bit_sha256,
        "hwh_sha256": plan.hwh_sha256,
        "hwh_threshold": actual_threshold,
        "hwh_path": str(plan.hwh_path),
        "evidence_path": str(evidence_path),
        "cma_free_kib_before_capture": cma_free_kib,
        "camera_device": args.camera,
        "v4l2_format": fmt,
        "v4l2_stream_parameters": parm,
        "v4l2_auto_exposure": auto_exposure,
        "v4l2_exposure_time_absolute": exposure_time,
        "exposure_locked": True,
        "exposure_time_ms": 5.0,
        "preview_port_8080_free": True,
        "video0_unowned": True,
        "prior_generic_guard_preserved": generic_guard,
        "stageb1_guard_path": str(STAGEB1_GUARD),
        "stageb1_guard_absent_before_run": True,
    }


def _claim_stageb1_guard(path: str | Path, identity: Any) -> dict[str, Any]:
    if Path(path) != STAGEB1_GUARD:
        raise RuntimeError("runner attempted to consume an unexpected one-shot guard path")
    if identity.get("boot_id") != EXPECTED_BOOT_ID:
        raise RuntimeError("Stage B-1 guard boot_id differs from the approved boot")
    stageb1_identity = {
        **dict(identity),
        "stage": STAGEB1_STAGE,
        "sampling_kind": "idle_belt_baseline_rightward_x_axis",
        "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "hwh_parser_sha256": hashlib.sha256(
            Path(__file__).with_name("stageb1_hwh.py").read_bytes()
        ).hexdigest(),
    }
    return _original_claim_once_guard(path, stageb1_identity)


async def _observed_camera_gray_frames(*args: Any, **kwargs: Any) -> AsyncIterator[np.ndarray]:
    started: float | None = None
    frame_id = 0
    kwargs["cv2_module"] = _OBSERVED_CV2
    async for gray in _original_camera_gray_frames(*args, **kwargs):
        frame = np.asarray(gray)
        if frame.dtype != np.uint8 or frame.shape != (240, 320):
            raise RuntimeError(f"Stage B-1 Gray8 frame has unexpected shape/type: {frame.shape}/{frame.dtype}")
        pacing_started = started
        if pacing_started is None:
            pacing_started = time.perf_counter()
            started = pacing_started
        deadline = pacing_started + frame_id / TARGET_FPS
        delay = deadline - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        if frame_id >= len(LUMINANCE_FRAMES):
            raise RuntimeError("raw BGR luminance telemetry is missing for a pipeline frame")
        luminance = LUMINANCE_FRAMES[frame_id]
        roi = frame[ROI_TOP:ROI_BOTTOM_EXCLUSIVE, :]
        row_means = np.mean(roi, axis=1, dtype=np.float64)
        peak_local_y, peak_x = np.unravel_index(int(np.argmax(roi)), roi.shape)
        brightest_row = int(np.argmax(row_means))
        now = time.perf_counter()
        roi_peak_value = int(roi[peak_local_y, peak_x])
        OPTICAL_FRAMES.append({
            "frame_id": frame_id,
            "frame_offset_ms": round((now - started) * 1000.0, 3),
            "gray_full_frame_mean": float(np.mean(frame, dtype=np.float64)),
            "gray_roi_mean": float(np.mean(roi, dtype=np.float64)),
            "gray_roi_pixel_peak": roi_peak_value,
            "gray_roi_pixel_peak_x": int(peak_x),
            "gray_roi_pixel_peak_y": int(ROI_TOP + peak_local_y),
            "gray_roi_brightest_row_mean": float(row_means[brightest_row]),
            "gray_roi_brightest_row_y": int(ROI_TOP + brightest_row),
            "optical_luminance_full_frame_mean": luminance["full_frame_mean"],
            "optical_luminance_roi_mean": luminance["roi_mean"],
            "optical_luminance_roi_pixel_peak": luminance["roi_pixel_peak"],
            "optical_luminance_source": luminance["source"],
        })
        if frame_id in (0, 50, REQUIRED_FRAMES - 1):
            SNAPSHOT_IMAGES[f"pipeline-frame-{frame_id:03d}"] = frame.copy()
        frame_id += 1
        yield gray


class ObservedDoubleBufferStream(_original_stream):
    def __init__(self, *args: Any, **kwargs: Any):
        original_after_transfer = kwargs.get("after_transfer")
        if original_after_transfer is None:
            raise RuntimeError("Stage B-1 requires the Packer verification callback")
        import pynq

        dma_mmio = pynq.MMIO(0x40400000, 0x10000)

        def verify_and_read_dmasr(frame_id: int, slot: Any) -> None:
            callback_error: BaseException | None = None
            try:
                original_after_transfer(frame_id, slot)
            except BaseException as exc:
                callback_error = exc
            mm2s = int(dma_mmio.read(0x04))
            s2mm = int(dma_mmio.read(0x34))
            FRAME_DMASR.append({
                "frame_id": int(frame_id),
                "mm2s": f"0x{mm2s:08x}",
                "s2mm": f"0x{s2mm:08x}",
                "pass": mm2s == EXPECTED_DMA_STATUS and s2mm == EXPECTED_DMA_STATUS,
            })
            if callback_error is not None:
                raise callback_error
            if mm2s != EXPECTED_DMA_STATUS or s2mm != EXPECTED_DMA_STATUS:
                raise RuntimeError(
                    f"frame {frame_id} DMA status mismatch: "
                    f"MM2S=0x{mm2s:08x}, S2MM=0x{s2mm:08x}"
                )

        kwargs["after_transfer"] = verify_and_read_dmasr
        super().__init__(*args, **kwargs)
        original_update = self.counter.update

        def observe_update(frame_id: int, mask: np.ndarray) -> Any:
            result = original_update(frame_id, mask)
            FRAME_RESULTS.append(result)
            return result

        self.counter.update = observe_update
        ACTIVE_STREAMS.append(self)

    async def run(self, frames: Any) -> tuple[Any, ...]:
        results = await super().run(frames)
        if len(results) != len(FRAME_RESULTS):
            raise RuntimeError("per-frame counting telemetry differs from completed stream results")
        if self.stage_timings_ms is not None:
            for name, values in self.stage_timings_ms.items():
                STREAM_TIMINGS[name] = [float(value) for value in values]
        return results


def _summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"samples": 0, "mean": None, "rms": None, "ptp": None,
                "min": None, "max": None}
    mean = statistics.fmean(values)
    return {
        "samples": len(values),
        "mean": mean,
        "rms": math.sqrt(statistics.fmean((value - mean) ** 2 for value in values)),
        "ptp": max(values) - min(values),
        "min": min(values),
        "max": max(values),
    }


def _time_distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"samples": 0, "mean_ms": None, "p50_ms": None, "p95_ms": None,
                "max_ms": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "samples": len(values),
        "mean_ms": statistics.fmean(values),
        "p50_ms": statistics.median(values),
        "p95_ms": ordered[p95_index],
        "max_ms": max(values),
    }


def _result_records() -> list[dict[str, Any]]:
    optical_by_id = {int(frame["frame_id"]): frame for frame in OPTICAL_FRAMES}
    records: list[dict[str, Any]] = []
    for result in FRAME_RESULTS:
        optical = optical_by_id.get(int(result.frame_id), {})
        component_areas = sorted(int(detection.area) for detection in result.detections)
        records.append({
            **optical,
            "frame_id": int(result.frame_id),
            "fg_pixels": int(result.roi_foreground_pixels),
            "components_after_min_area": len(result.detections),
            "component_areas_px": component_areas,
            "max_component_area_px": (max(component_areas) if component_areas else 0),
            "ccl_executed": bool(result.ccl_executed),
            "total_count": int(result.total_count),
            "counting_axis": "x",
            "roi_left": 0,
            "roi_right_exclusive": 320,
            "roi_top": ROI_TOP,
            "roi_bottom_exclusive": ROI_BOTTOM_EXCLUSIVE,
            "roi_timings_ms": {
                key: float(value) for key, value in result.roi_subtimings_ms.items()
            },
        })
    return records


def _component_area_summary() -> dict[str, Any]:
    areas = [
        int(detection.area)
        for result in FRAME_RESULTS
        for detection in result.detections
    ]
    histogram = Counter(areas)
    return {
        "definition": "exact area frequency for connected components surviving min_area",
        "area_unit": "pixels",
        "min_area_px": 12,
        "components_observed": len(areas),
        "area_frequency": [
            {"area_px": int(area), "components": int(count)}
            for area, count in sorted(histogram.items())
        ],
        "max_component_area": (max(areas) if areas else 0),
    }


def _augment_evidence(path: Path, *, runner_returncode: int) -> int:
    if path.is_file():
        evidence = json.loads(path.read_text(encoding="utf-8"))
    else:
        evidence = {
            "schema": 1,
            "work_order": "WO-0016",
            "status": "BOARD_STREAM_REJECTED",
            "board_accessed": bool(getattr(runner.board_once, "_REAL_BOARD_ACCESS_OCCURRED", False)),
            "pynq_imported": bool(getattr(runner.board_once, "_PYNQ_IMPORTED", False)),
            "boot_id": EXPECTED_BOOT_ID,
            "manifest_sha256": None,
            "frames_processed": len(FRAME_RESULTS),
            "buffers_released": None,
            "runner_error": RUNNER_ERROR,
        }

    records = _result_records()
    whole_means = [float(frame["gray_full_frame_mean"]) for frame in OPTICAL_FRAMES]
    roi_means = [float(frame["gray_roi_mean"]) for frame in OPTICAL_FRAMES]
    luminance_whole_means = [
        float(frame["optical_luminance_full_frame_mean"]) for frame in OPTICAL_FRAMES
    ]
    luminance_roi_means = [
        float(frame["optical_luminance_roi_mean"]) for frame in OPTICAL_FRAMES
    ]
    fg_pixels = [int(result.roi_foreground_pixels) for result in FRAME_RESULTS]
    components = [len(result.detections) for result in FRAME_RESULTS]
    counts = [int(result.total_count) for result in FRAME_RESULTS]
    roi_peaks = OPTICAL_FRAMES
    max_peak = max(roi_peaks, key=lambda frame: int(frame["gray_roi_pixel_peak"]), default=None)
    max_bright_row = max(
        roi_peaks, key=lambda frame: float(frame["gray_roi_brightest_row_mean"]), default=None
    )
    offsets = [float(frame["frame_offset_ms"]) for frame in OPTICAL_FRAMES]
    elapsed = (offsets[-1] / 1000.0) if len(offsets) >= 2 else None
    observed_fps = ((len(OPTICAL_FRAMES) - 1) / elapsed
                    if elapsed is not None and elapsed > 0 else None)
    estimated_belt_revolutions = (
        elapsed * BELT_SPEED_MM_S / BELT_LOOP_MM
        if elapsed is not None else None
    )
    dmasr_pass_count = sum(1 for frame in FRAME_DMASR if frame["pass"])
    frame_count = int(evidence.get("frames_processed", len(FRAME_RESULTS)) or 0)
    count_zero = bool(counts) and all(count == 0 for count in counts)
    exact_dmasr = (len(FRAME_DMASR) == frame_count and dmasr_pass_count == len(FRAME_DMASR))
    cma_after = evidence.get("cma_free_after_release_kib")
    cma_pass = isinstance(cma_after, int) and cma_after >= CMA_WATERMARK_KIB
    buffers_released = evidence.get("buffers_released") is True
    packer_frames = int(evidence.get("packer_output_frames_verified", 0) or 0)
    packer_pass = packer_frames == frame_count
    camera_fps = evidence.get("camera_fps_actual")
    camera_mode_pass = (
        evidence.get("camera_width") == 640
        and evidence.get("camera_height") == 400
        and isinstance(camera_fps, (int, float))
        and camera_fps >= 50.0
    )
    duration_s = evidence.get("elapsed_seconds")
    duration_pass = isinstance(duration_s, (int, float)) and 5.0 <= duration_s <= 10.0
    fps_pass = (isinstance(observed_fps, (int, float))
                and 0.9 * TARGET_FPS <= observed_fps <= 1.1 * TARGET_FPS)
    roi_summary = _summarize(roi_means)
    full_summary = _summarize(whole_means)
    luminance_roi_summary = _summarize(luminance_roi_means)
    luminance_full_summary = _summarize(luminance_whole_means)
    bg_target_pass = (roi_summary["mean"] is not None
                      and 20.0 <= roi_summary["mean"] <= 45.0)
    component_noise_clear = bool(components) and max(components) == 0
    threshold_margin_clear = (max_peak is not None
                              and int(max_peak["gray_roi_pixel_peak"]) < THRESHOLD)
    sample_complete = (frame_count == REQUIRED_FRAMES
                       and len(OPTICAL_FRAMES) == REQUIRED_FRAMES
                       and len(FRAME_RESULTS) == REQUIRED_FRAMES
                       and len(LUMINANCE_FRAMES) >= REQUIRED_FRAMES)
    stageb1_pass = (runner_returncode == 0 and evidence.get("status") == "BOARD_STREAM_COMPLETE"
                    and sample_complete and count_zero and exact_dmasr and cma_pass
                    and buffers_released and packer_pass and camera_mode_pass
                    and duration_pass and fps_pass and bg_target_pass
                    and component_noise_clear and threshold_margin_clear)

    snapshot_records = []
    for key, image in sorted(SNAPSHOT_IMAGES.items()):
        ok, encoded = runner.cv2.imencode(".png", image)
        if not ok:
            raise RuntimeError(f"failed to encode representative sample frame {key}")
        kind, frame_label = key.split("-frame-", maxsplit=1)
        frame_id = int(frame_label)
        sidecar = path.with_name(
            f"{path.stem}-{kind}-frame-{frame_id:03d}.png"
        )
        blob = encoded.tobytes()
        fd = os.open(str(sidecar), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(blob)
            output.flush()
            os.fsync(output.fileno())
        snapshot_records.append({
            "path": str(sidecar),
            "kind": kind,
            "frame_id": frame_id,
            "sha256": hashlib.sha256(blob).hexdigest(),
            "bytes": len(blob),
            "shape": list(image.shape),
        })
    component_area_summary = _component_area_summary()

    evidence.update({
        "stageb1_kind": STAGEB1_STAGE,
        "stageb1_preflight": PREFLIGHT,
        "stageb1_threshold": THRESHOLD,
        "stageb1_threshold_source": "WO-0015 50 MHz HWH, SHA-256 bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6",
        "stageb1_min_area": 12,
        "stageb1_roi": {
            "geometry": "49x320 Binary8 horizontal lane centered in the image",
            "counting_axis": "x",
            "line_x": LINE_X,
            "motion_direction": "rightward (+x)",
            "roi_left": 0,
            "roi_right_exclusive": 320,
            "roi_top": ROI_TOP,
            "roi_bottom_exclusive": ROI_BOTTOM_EXCLUSIVE,
        },
        "bg_mean": {
            "full_frame_gray8": full_summary["mean"],
            "roi_gray8": roi_summary["mean"],
            "target_roi_range": [20, 45],
            "optical_luminance_full_frame_gray8": luminance_full_summary["mean"],
            "optical_luminance_roi_gray8": luminance_roi_summary["mean"],
        },
        "bg_rms": {
            "definition": "population RMS of per-frame Gray8 means around their temporal mean",
            "full_frame_gray8": full_summary["rms"],
            "roi_gray8": roi_summary["rms"],
            "optical_luminance_full_frame_gray8": luminance_full_summary["rms"],
            "optical_luminance_roi_gray8": luminance_roi_summary["rms"],
        },
        "bg_ptp": {
            "definition": "peak-to-peak range of per-frame Gray8 means",
            "full_frame_gray8": full_summary["ptp"],
            "roi_gray8": roi_summary["ptp"],
            "optical_luminance_full_frame_gray8": luminance_full_summary["ptp"],
            "optical_luminance_roi_gray8": luminance_roi_summary["ptp"],
        },
        "gray_background_statistics": {
            "full_frame": full_summary,
            "roi": roi_summary,
            "source": "exact WO-0016 pipeline Gray8: camera channel 0, nearest-neighbor 640x400 to 320x240; not a separately computed luminance conversion",
        },
        "optical_luminance_statistics": {
            "full_frame": luminance_full_summary,
            "roi": luminance_roi_summary,
            "source": (LUMINANCE_FRAMES[0]["source"] if LUMINANCE_FRAMES else None),
            "pipeline_relationship": "measured separately from the exact Gray8 channel passed to PL; it does not replace the DMA input",
        },
        "seam_peak_candidate": {
            "max_gray8_pixel_in_roi": (None if max_peak is None else int(max_peak["gray_roi_pixel_peak"])),
            "frame_id": (None if max_peak is None else int(max_peak["frame_id"])),
            "x": (None if max_peak is None else int(max_peak["gray_roi_pixel_peak_x"])),
            "y": (None if max_peak is None else int(max_peak["gray_roi_pixel_peak_y"])),
            "brightest_horizontal_row_mean_gray8": (None if max_bright_row is None else float(max_bright_row["gray_roi_brightest_row_mean"])),
            "brightest_horizontal_row_frame_id": (None if max_bright_row is None else int(max_bright_row["frame_id"])),
            "brightest_horizontal_row_y": (None if max_bright_row is None else int(max_bright_row["gray_roi_brightest_row_y"])),
            "seam_attribution": "candidate peak only; no separate seam classifier is implemented",
            "threshold_margin_to_max_roi_pixel_gray8": (
                None if max_peak is None
                else THRESHOLD - int(max_peak["gray_roi_pixel_peak"])
            ),
        },
        "fg_pixels": {
            "roi_mean": (statistics.fmean(fg_pixels) if fg_pixels else None),
            "roi_max": (max(fg_pixels) if fg_pixels else None),
            "samples": len(fg_pixels),
        },
        "components_after_min_area": {
            "mean": (statistics.fmean(components) if components else None),
            "max": (max(components) if components else None),
            "samples": len(components),
        },
        "component_area_histogram": component_area_summary,
        "max_component_area": component_area_summary["max_component_area"],
        "total_count_trace": counts,
        "total_count_zero_gate": {
            "pass": count_zero,
            "final_total_count": (counts[-1] if counts else evidence.get("total_count")),
            "max_total_count_during_run": (max(counts) if counts else None),
        },
        "dma_status_per_frame": FRAME_DMASR,
        "dma_status_gate": {
            "expected": "0x00001002",
            "frames": len(FRAME_DMASR),
            "passes": dmasr_pass_count,
            "pass_rate": (dmasr_pass_count / len(FRAME_DMASR) if FRAME_DMASR else 0.0),
            "all_pass": exact_dmasr,
        },
        "buffer_release_gate": {
            "buffers_released": buffers_released,
            "cma_after_release_kib": cma_after,
            "cma_watermark_kib": CMA_WATERMARK_KIB,
            "cma_after_release_pass": cma_pass,
        },
        "packer_gate": {
            "frames_verified": packer_frames,
            "frames_processed": frame_count,
            "all_frames_verified": packer_pass,
        },
        "camera_mode_gate": {
            "width": evidence.get("camera_width"),
            "height": evidence.get("camera_height"),
            "actual_fps": camera_fps,
            "at_least_50_fps": camera_mode_pass,
        },
        "steady_frame_timing": {
            "elapsed_seconds_from_frame_offsets": elapsed,
            "sample_delivery_fps": observed_fps,
            "target_fps": TARGET_FPS,
            "requested_sample_duration_seconds": REQUESTED_SAMPLE_DURATION_S,
            "requested_frame_count": REQUIRED_FRAMES,
            "within_target_plus_minus_10_percent": fps_pass,
            "runner_end_to_end_fps": evidence.get("hardware_fps"),
            "runner_elapsed_seconds": evidence.get("elapsed_seconds"),
            "estimated_belt_revolutions_at_reported_160_mm_s": estimated_belt_revolutions,
            "belt_speed_mm_s_source": "user-reported approximately 160 mm/s; estimate only",
            "belt_loop_mm_source": "user-reported 500 mm conveyor length; estimate only",
            "requested_duration_range_seconds": [5.0, 10.0],
            "duration_gate_pass": duration_pass,
            "stage_timing_distributions_ms": {
                key: _time_distribution(values) for key, values in STREAM_TIMINGS.items()
            },
        },
        "per_frame": records,
        "sampled_frame_sidecars": snapshot_records,
        "stageb1_gates": {
            "frame_count_100": sample_complete,
            "duration_5_to_10_seconds": duration_pass,
            "roi_bg_mean_20_to_45": bg_target_pass,
            "delivery_fps_within_15_15_plus_minus_10_percent": fps_pass,
            "no_components_after_min_area": component_noise_clear,
            "roi_peak_below_threshold_128": threshold_margin_clear,
            "total_count_zero": count_zero,
            "dmasr_all_0x1002": exact_dmasr,
            "cma_after_release_at_least_32768_kib": cma_pass,
            "all_pass": stageb1_pass,
        },
        "stageb1_status": ("BOARD_STAGEB1_PASS" if stageb1_pass
                            else "BOARD_STAGEB1_REVIEW_REQUIRED"),
        "runner_returncode": runner_returncode,
        "runner_error": RUNNER_ERROR,
    })

    temporary = path.with_suffix(path.suffix + ".stageb1-tmp")
    fd = os.open(str(temporary), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    return 0 if stageb1_pass else (runner_returncode or 1)


def _capture_runner_error(args: Any) -> Any:
    global RUNNER_ERROR
    try:
        return _original_execute_board_stream(args)
    except BaseException as exc:
        RUNNER_ERROR = f"{type(exc).__name__}: {exc}"
        raise


def _fail_hold_with_partial_telemetry(api: Any, evidence_path: str | None,
                                      evidence: Any) -> None:
    enriched = dict(evidence)
    enriched["stageb1_kind"] = STAGEB1_STAGE
    enriched["stageb1_preflight"] = PREFLIGHT
    component_area_summary = _component_area_summary()
    enriched["stageb1_partial_telemetry"] = {
        "pipeline_gray_source": "camera channel 0, resized nearest-neighbor to 320x240",
        "optical_frames_seen": len(OPTICAL_FRAMES),
        "roi_bg_gray8": _summarize([float(x["gray_roi_mean"]) for x in OPTICAL_FRAMES]),
        "full_frame_bg_gray8": _summarize([float(x["gray_full_frame_mean"]) for x in OPTICAL_FRAMES]),
        "optical_luminance_roi_gray8": _summarize(
            [float(x["optical_luminance_roi_mean"]) for x in OPTICAL_FRAMES]
        ),
        "roi_peak_gray8": max((int(x["gray_roi_pixel_peak"]) for x in OPTICAL_FRAMES), default=None),
        "counted_frames": _result_records(),
        "component_area_histogram": component_area_summary,
        "max_component_area": component_area_summary["max_component_area"],
        "dma_status_per_frame": list(FRAME_DMASR),
        "stage_timing_distributions_ms": {
            key: _time_distribution(values) for key, values in STREAM_TIMINGS.items()
        },
        "runner_error": RUNNER_ERROR,
    }
    _original_fail_hold(api, evidence_path, enriched)


def main() -> int:
    args = runner._parser().parse_args()
    global PREFLIGHT
    try:
        PREFLIGHT = _read_camera_preflight(args)
    except BaseException as exc:
        payload = {
            "schema": 1,
            "work_order": "WO-0016",
            "status": "BOARD_STAGEB1_PRECONDITION_REJECTED",
            "stageb1_kind": STAGEB1_STAGE,
            "boot_id_expected": EXPECTED_BOOT_ID,
            "error": f"{type(exc).__name__}: {exc}",
            "board_accessed": False,
            "guard_consumed": STAGEB1_GUARD.exists(),
            "guard_path": str(STAGEB1_GUARD),
            "stageb1_preflight": PREFLIGHT,
        }
        if args.evidence_json:
            path = Path(args.evidence_json)
            if not path.exists():
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
                    output.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2

    runner.WO16_GUARD = STAGEB1_GUARD
    runner.claim_once_guard = _claim_stageb1_guard
    runner.MaskCounter = _RightwardMaskCounter
    runner.camera_gray_frames = _observed_camera_gray_frames
    runner.DoubleBufferStream = ObservedDoubleBufferStream
    runner.execute_board_stream = _capture_runner_error
    runner.board_once.PynqBoardAPI.fail_hold = _fail_hold_with_partial_telemetry
    handoff_started = time.perf_counter()
    time.sleep(HANDOFF_STABILIZATION_DELAY_S)
    PREFLIGHT["camera_handoff_stabilization"] = {
        "requested_seconds": HANDOFF_STABILIZATION_DELAY_S,
        "actual_seconds": time.perf_counter() - handoff_started,
        "starts_after_preview_and_video0_release_preflight": True,
    }
    returncode = runner.main()
    if args.evidence_json:
        return _augment_evidence(Path(args.evidence_json), runner_returncode=returncode)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
