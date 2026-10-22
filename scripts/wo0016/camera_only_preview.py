#!/usr/bin/env python3
"""WO-0017 camera-only industrial HUD preview with a software threshold PIP."""
from __future__ import annotations

import argparse
import json
import mmap
import os
import signal
import struct
import subprocess
import time
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import pwd
except ImportError:  # pragma: no cover - only available on the PYNQ Linux board
    pwd = None

import cv2
import numpy as np

import mjpeg_preview
from stream_counter import MaskCounter
from live_calibration import (MonochromePreprocessor, monochrome_view,
                              optical_metrics, CalibrationControls)

EXPECTED_BOOT = "35983d66-bb1d-4a8a-85e5-802170825515"
WO_ROOT = Path("/home/xilinx/wo0017")
CAMERA_PATH = "/dev/video0"
CAMERA_INDEX = 0
SOURCE_WIDTH, SOURCE_HEIGHT, SOURCE_FPS = 640, 400, 60
WIDTH, HEIGHT = 320, 240
THRESHOLD = 128
PREVIEW_PORT = 8080
TARGET_CLIENT_FRAMES = 180
DMA_BASE = 0x40400000
DMA_ERROR_MASK = 0x770
DMA_STATUS_OFFSETS = {"MM2S": 0x04, "S2MM": 0x34}
COUNT_LINE_X = 160
HYSTERESIS = 15.0
running = True


@dataclass(frozen=True)
class HudResult:
    frame_id: int
    total_count: int
    tracks: tuple[dict[str, Any], ...]
    calibration: dict[str, Any] | None = None


def live_boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()


def cma_free_kib() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        if line.startswith("CmaFree:"):
            return int(line.split()[1])
    raise RuntimeError("CmaFree is absent from /proc/meminfo")


def preview_memory_snapshot() -> dict[str, int]:
    values = {}
    for path in ("/proc/meminfo", "/proc/self/status"):
        for line in Path(path).read_text(encoding="ascii").splitlines():
            name, _, value = line.partition(":")
            if name in ("CmaFree", "MemAvailable", "VmRSS"):
                values[name] = int(value.split()[0])
    return {"cma_free_kib": values["CmaFree"],
            "mem_available_kib": values["MemAvailable"],
            "process_rss_kib": values["VmRSS"]}


def require_preview_memory(snapshot: dict[str, int], *, calibration_mode: bool,
                           startup: bool = False) -> str:
    """Camera calibration uses ordinary RAM and no PL CMA allocation.

    Keep the DMA reserve before opening the camera and as continuous telemetry.
    After camera allocation succeeds, calibration uses its 16 MiB reserve while
    system RAM and its own RSS are healthy. It never allocates PL DMA buffers.
    Non-calibration behavior retains the strict continuous CMA reserve gate.
    """
    for name in ("cma_free_kib", "mem_available_kib", "process_rss_kib"):
        value = snapshot.get(name)
        if type(value) is not int or value < 0:
            raise RuntimeError("invalid memory telemetry: " + name)
    if snapshot["mem_available_kib"] < 65536:
        raise RuntimeError("MemAvailable fell below 65536 KiB")
    if snapshot["process_rss_kib"] > 196608:
        raise RuntimeError("preview RSS exceeded 196608 KiB")
    if calibration_mode and snapshot["cma_free_kib"] < 16384:
        raise RuntimeError("calibration CMA free memory fell below 16384 KiB")
    if snapshot["cma_free_kib"] <= 32768:
        if startup or not calibration_mode:
            raise RuntimeError("CMA free memory is not above 32768 KiB")
        return "CMA_LOW: STOP CALIBRATION BEFORE DMA"
    return "MEMORY_OK"


def read_dma_statuses() -> dict[str, int]:
    fd = os.open("/dev/mem", os.O_RDONLY | getattr(os, "O_SYNC", 0))
    window = None
    try:
        window = mmap.mmap(fd, 0x1000, flags=mmap.MAP_SHARED,
                           prot=mmap.PROT_READ, offset=DMA_BASE)
        return {name: struct.unpack_from("<I", window, offset)[0]
                for name, offset in DMA_STATUS_OFFSETS.items()}
    finally:
        if window is not None:
            window.close()
        os.close(fd)


def require_dma_idle_and_error_free(statuses: dict[str, int]) -> None:
    for name, status in statuses.items():
        if status & DMA_ERROR_MASK:
            raise RuntimeError(f"{name} DMASR has error bits set: 0x{status:08x}")
        if not status & 0x1 and not status & 0x2:
            raise RuntimeError(f"{name} DMA is active: DMASR=0x{status:08x}")


def query_v4l2() -> str:
    command = ["v4l2-ctl", "-d", CAMERA_PATH, "--get-fmt-video", "--get-parm",
               "--get-ctrl=auto_exposure,exposure_time_absolute,brightness"]
    if os.geteuid() != 0:
        command = ["sudo", "-n"] + command
    result = subprocess.run(command, check=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=10)
    return result.stdout


def profile_matches(output: str, controls: dict | None = None) -> bool:
    controls = controls or CalibrationControls.STANDARD
    required = (
        "Width/Height      : 640/400",
        "Pixel Format      : 'MJPG'",
        "Frames per second: 60.000 (60/1)",
        f"brightness: {controls['brightness']}\n",
        "auto_exposure: 1 (Manual Mode)",
        f"exposure_time_absolute: {controls['exposure_time_absolute']}\n",
    )
    return all(item in output for item in required)


def require_profile(output: str, stage: str, controls: dict | None = None) -> None:
    if not profile_matches(output, controls):
        raise RuntimeError("V4L2 profile mismatch at " + stage + ":\n" + output)


def apply_v4l2_controls(values: dict[str, int]) -> dict[str, int]:
    command = ["v4l2-ctl", "-d", CAMERA_PATH,
               "--set-ctrl=" + ",".join(f"{key}={value}" for key, value in values.items())]
    if os.geteuid() != 0:
        command = ["sudo", "-n"] + command
    subprocess.run(command, check=True, stdout=subprocess.PIPE,
                   stderr=subprocess.STDOUT, text=True, timeout=5)
    require_profile(query_v4l2(), "after calibration control", values)
    return dict(values)


def software_gray_binary(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Resize a decoded MJPEG frame and derive an explicitly software mask."""
    return MonochromePreprocessor()(frame)


def read_frame(capture: Any) -> np.ndarray:
    ok, frame = capture.read()
    if not ok or frame is None:
        raise RuntimeError("camera returned no preview frame")
    try:
        monochrome_view(frame)
    except ValueError:
        raise RuntimeError("camera frame has unexpected shape/dtype: %r %r" %
                           (frame.shape, frame.dtype))
    return frame


def write_state(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_name(path.name + ".tmp")
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    fd = os.open(str(temp), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def drop_to_xilinx() -> None:
    if pwd is None:
        raise RuntimeError("dropping privileges requires the PYNQ Linux pwd module")
    user = pwd.getpwnam("xilinx")
    os.setgroups([])
    os.setgid(user.pw_gid)
    os.setuid(user.pw_uid)
    if os.geteuid() != user.pw_uid:
        raise RuntimeError("preview process did not drop root privileges")


def stop(*_args: Any) -> None:
    global running
    running = False


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(np.median(np.asarray(values, dtype=np.float64)))


def main(argv: list[str] | None = None) -> int:
    global running
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-boot-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--calibration-mode", action="store_true",
                        help="Enable loopback-only exposure/brightness API; never formal sampling")
    args = parser.parse_args(argv)

    if os.geteuid() != 0:
        raise RuntimeError("run as root to open /dev/video0; service drops to xilinx before serving")
    if pwd is None:
        raise RuntimeError("camera-only preview must run on PYNQ Linux")
    if args.expected_boot_id != EXPECTED_BOOT or live_boot_id() != EXPECTED_BOOT:
        raise RuntimeError("live Boot ID differs from the explicit preview binding")
    output_dir = args.output_dir.resolve(strict=False)
    expected_name = f"camera-preview-{EXPECTED_BOOT}-06"
    if output_dir.parent != WO_ROOT or output_dir.name != expected_name:
        raise RuntimeError("output directory must be the exact WO-0017 attempt -06 path")
    if output_dir.exists() or output_dir.is_symlink():
        raise RuntimeError(f"refusing to overwrite existing attempt directory: {output_dir}")
    output_dir.mkdir(mode=0o700)
    xilinx = pwd.getpwnam("xilinx")
    os.chown(output_dir, xilinx.pw_uid, xilinx.pw_gid)
    os.chmod(output_dir, 0o700)
    memory_path = output_dir / "memory-health.jsonl"
    memory_fd = os.open(str(memory_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.fchown(memory_fd, xilinx.pw_uid, xilinx.pw_gid)
    finally:
        os.close(memory_fd)

    state_path = output_dir / "preview-state.json"
    state: dict[str, Any] = {
        "schema": 1,
        "work_order": "WO-0017",
        "attempt": "camera-preview-06",
        "calibration_mode": args.calibration_mode,
        "calibration_control_access": "loopback-only JSON POST /api/calibration",
        "memory_policy": {
            "before_camera_cma_strictly_above_kib": 32768,
            "after_camera_minimum_cma_kib": 16384 if args.calibration_mode else 32769,
            "runtime_cma_32768_warning_only": args.calibration_mode,
            "calibration_cma_emergency_floor_kib": 16384,
            "min_mem_available_kib": 65536,
            "max_process_rss_kib": 196608,
            "formal_sampling_requires_preview_stopped_and_fresh_preflight": True,
        },
        "boot_id": EXPECTED_BOOT,
        "status": "STARTING",
        "camera": CAMERA_PATH,
        "expected_profile": {
            "backend": "CAP_V4L2", "pixelformat": "MJPG",
            "width": SOURCE_WIDTH, "height": SOURCE_HEIGHT, "fps": SOURCE_FPS,
            "auto_exposure": 1, "exposure_time_absolute": 50, "brightness": 0,
        },
        "hud": {
            "line_x": COUNT_LINE_X,
            "hysteresis_band_x": [COUNT_LINE_X - int(HYSTERESIS),
                                  COUNT_LINE_X + int(HYSTERESIS)],
            "pip_label": "SIM TH=128",
            "pip_source": "software threshold of resized camera Gray8; no PL DMA data",
            "software_tracker_enabled": True,
        },
        "board_counter_enabled": False,
        "dma_enabled": False,
        "overlay_reload": False,
        "formal_sampling": False,
        "frame_files_written": False,
        "preview_port": PREVIEW_PORT,
        "pid": os.getpid(),
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_state(state_path, state)

    capture = None
    server = None
    publisher = None
    loopback = None
    controls = CalibrationControls(enabled=args.calibration_mode, apply=apply_v4l2_controls)
    def control_update(payload: dict) -> dict:
        try:
            values = controls.update(payload)
            state["camera_controls"] = values
            state["last_control_utc"] = datetime.now(timezone.utc).isoformat()
            return values
        except (PermissionError, ValueError):
            raise
        except Exception as exc:
            state["control_failure"] = str(exc)
            stop()
            raise
    state["camera_controls"] = dict(controls.values)
    memory_started = time.monotonic()
    memory_last_log = -10.0
    def update_memory(*, startup: bool = False) -> None:
        nonlocal memory_last_log
        snapshot = preview_memory_snapshot()
        state["memory_health"] = snapshot
        minimum = state.setdefault("memory_minimum_kib", dict(snapshot))
        for key, value in snapshot.items():
            minimum[key] = min(minimum[key], value)
        state["process_rss_peak_kib"] = max(state.get("process_rss_peak_kib", 0), snapshot["process_rss_kib"])
        if snapshot["cma_free_kib"] <= 32768:
            state["cma_warning_samples"] = state.get("cma_warning_samples", 0) + 1
        elapsed = time.monotonic() - memory_started
        if startup or elapsed - memory_last_log >= 5.0:
            with memory_path.open("a", encoding="ascii") as stream:
                stream.write(json.dumps({"elapsed_seconds": elapsed, **snapshot}) + "\n")
            memory_last_log = elapsed
        state["memory_health_status"] = require_preview_memory(
            snapshot, calibration_mode=args.calibration_mode, startup=startup)
    try:
        cv2.setNumThreads(1)
        state["opencv_threads"] = cv2.getNumThreads()
        state["dma_status_read_only_before_preview"] = read_dma_statuses()
        require_dma_idle_and_error_free(state["dma_status_read_only_before_preview"])
        state["v4l2_before_open"] = query_v4l2()
        require_profile(state["v4l2_before_open"], "before OpenCV open")
        update_memory(startup=True)
        state["cma_before_camera_kib"] = cma_free_kib()
        if state["cma_before_camera_kib"] <= 32768:
            raise RuntimeError("CmaFree is not strictly above 32768 KiB before preview")

        capture = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
        if not capture.isOpened():
            raise RuntimeError("OpenCV could not open camera index 0 with CAP_V4L2")
        set_results = {
            "fourcc_mjpg": bool(capture.set(cv2.CAP_PROP_FOURCC,
                                              cv2.VideoWriter_fourcc(*"MJPG"))),
            "width_640": bool(capture.set(cv2.CAP_PROP_FRAME_WIDTH, SOURCE_WIDTH)),
            "height_400": bool(capture.set(cv2.CAP_PROP_FRAME_HEIGHT, SOURCE_HEIGHT)),
            "fps_60": bool(capture.set(cv2.CAP_PROP_FPS, SOURCE_FPS)),
            "buffer_size_1": bool(capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)),
        }
        state["opencv_set_results"] = set_results
        if not all(set_results.values()):
            raise RuntimeError("V4L2 rejected preview mode or single-frame buffer setting")
        state["opencv_properties_after_set"] = {
            "fourcc": int(round(capture.get(cv2.CAP_PROP_FOURCC))),
            "width": int(round(capture.get(cv2.CAP_PROP_FRAME_WIDTH))),
            "height": int(round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))),
            "fps": float(capture.get(cv2.CAP_PROP_FPS)),
        }
        before_first = query_v4l2()
        state["v4l2_before_first_frame"] = before_first
        require_profile(before_first, "immediately before first frame")
        if live_boot_id() != EXPECTED_BOOT:
            raise RuntimeError("Boot ID changed before the first preview frame")

        first_frame = read_frame(capture)
        state["first_frame_shape"] = list(first_frame.shape)
        state["first_frame_dtype"] = str(first_frame.dtype)
        after_first = query_v4l2()
        state["v4l2_after_first_frame"] = after_first
        require_profile(after_first, "after first frame")
        state["cma_after_first_frame_kib"] = cma_free_kib()
        update_memory()

        counter = MaskCounter(count_axis="x", line_x=COUNT_LINE_X,
                              hysteresis=HYSTERESIS, line_y=120,
                              roi_half_height=24, min_area=12,
                              max_step=60.0, max_lateral_step=10.0,
                              max_area=900, max_bbox_width=85, max_bbox_height=85,
                              discard_finished_tracks=True)
        state["component_limits"] = {"max_area": 900, "max_bbox_width": 85, "max_bbox_height": 85}
        preprocessor = MonochromePreprocessor()
        frame_times = deque(maxlen=60)
        hub = mjpeg_preview.MjpegFrameHub()
        publisher = mjpeg_preview.PeriodicMjpegPublisher(
            hub=hub, line_y=120, interval=1, quality=85,
            count_label="SIM COUNT",
            count_axis="x", line_x=COUNT_LINE_X, hysteresis=HYSTERESIS,
            pip_label="SIM TH=128",
        )
        timing: dict[str, list[float]] = {
            "camera_read_ms": [], "gray_resize_threshold_ms": [],
            "software_track_ms": [], "hud_handoff_ms": [],
        }
        frame_number = 0

        def process_and_publish(raw_frame: np.ndarray) -> None:
            nonlocal frame_number
            started = time.perf_counter()
            gray, mask = preprocessor(raw_frame)
            timing["gray_resize_threshold_ms"].append(
                (time.perf_counter() - started) * 1000.0)
            started = time.perf_counter()
            result = counter.update(frame_number, mask)
            timing["software_track_ms"].append((time.perf_counter() - started) * 1000.0)
            frame_times.append(time.monotonic())
            fps = ((len(frame_times)-1) / (frame_times[-1]-frame_times[0])
                   if len(frame_times) > 1 else 0.0)
            metrics = optical_metrics(gray, result, counter, fps=fps)
            state["calibration"] = metrics
            state["live_fps"] = fps
            hud_result = HudResult(frame_number, result.total_count, result.tracks, metrics)
            started = time.perf_counter()
            publisher(hud_result, mask, gray)
            timing["hud_handoff_ms"].append((time.perf_counter() - started) * 1000.0)
            for values in timing.values():
                del values[:-300]
            for values in (publisher.handoff_timings_ms, publisher.background_encode_timings_ms):
                del values[:-300]
            frame_number += 1

        process_and_publish(first_frame)
        drop_to_xilinx()
        if live_boot_id() != EXPECTED_BOOT:
            raise RuntimeError("Boot ID changed before preview listener startup")
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server = mjpeg_preview.MjpegPreviewServer(
            hub=hub, host="0.0.0.0", port=PREVIEW_PORT,
            state_provider=lambda: dict(state),
            control_callback=control_update if args.calibration_mode else None).start()
        loopback = mjpeg_preview.MjpegLoopbackClient(
            f"http://127.0.0.1:{server.port}/stream.mjpg", timeout_s=1.0).start()

        measurement_start = time.monotonic()
        pump_deadline = measurement_start + 25.0
        last_read_started = time.perf_counter()
        while (loopback.frames_received < TARGET_CLIENT_FRAMES
               and time.monotonic() < pump_deadline):
            if not running:
                raise RuntimeError("preview stopped during startup measurement")
            raw_frame = read_frame(capture)
            now = time.perf_counter()
            timing["camera_read_ms"].append((now - last_read_started) * 1000.0)
            last_read_started = now
            process_and_publish(raw_frame)
            if loopback.error:
                raise RuntimeError("local preview stream error: " + loopback.error)
        loopback.close()
        state["preview_frames_measured"] = loopback.frames_received
        state["preview_fps"] = loopback.preview_client_fps
        state["preview_measurement_seconds"] = (
            loopback.last_frame_time - loopback.first_frame_time
            if loopback.first_frame_time is not None and loopback.last_frame_time is not None
            else None
        )
        if (state["preview_frames_measured"] < TARGET_CLIENT_FRAMES
                or state["preview_fps"] is None or state["preview_fps"] <= 0):
            raise RuntimeError("could not measure 180 delivered HUD preview frames")
        state["pipeline_fps"] = (
            max(0, frame_number - 1) / max(time.monotonic() - measurement_start, 1e-9)
        )
        state["stage_median_ms"] = {
            key: _median(values) for key, values in timing.items()
        }
        state["hud_encode_median_ms"] = _median(publisher.background_encode_timings_ms)
        state["v4l2_at_ready"] = query_v4l2()
        require_profile(state["v4l2_at_ready"], "at ready state", controls.values)
        state["cma_at_ready_kib"] = cma_free_kib()
        update_memory()
        state.update({
            "status": "READY_WAITING_FOR_OPERATOR_OBSERVATION",
            "service_uid": os.geteuid(),
            "service_gid": os.getegid(),
            "preview_url": f"http://192.168.2.99:{server.port}/",
            "ready_utc": datetime.now(timezone.utc).isoformat(),
            "preview_frames_published": frame_number,
            "preview_source": "software threshold preview; no DMA, no formal sampling",
        })
        write_state(state_path, state)
        print("CAMERA_HUD_PREVIEW_READY url=%s fps=%.3f frames=%d pipeline_fps=%.3f cma_free_kib=%d boot=%s" % (
            state["preview_url"], state["preview_fps"], state["preview_frames_measured"],
            state["pipeline_fps"], state["cma_at_ready_kib"], EXPECTED_BOOT), flush=True)

        last_boot_check = time.monotonic()
        last_state_write = last_boot_check
        while running:
            now = time.monotonic()
            if now - last_boot_check >= 1.0:
                if live_boot_id() != EXPECTED_BOOT:
                    raise RuntimeError("Boot ID changed during camera-only preview")
                update_memory()
                last_boot_check = now
            raw_frame = read_frame(capture)
            process_and_publish(raw_frame)
            state["preview_frames_published"] = frame_number
            if time.monotonic() - last_state_write >= 1.0:
                state["last_frame_utc"] = datetime.now(timezone.utc).isoformat()
                state["hud_encode_median_ms"] = _median(publisher.background_encode_timings_ms)
                state["stage_median_ms"] = {key: _median(values) for key, values in timing.items()}
                if state.get("control_failure"):
                    raise RuntimeError("calibration control failed: " + state["control_failure"])
                write_state(state_path, state)
                last_state_write = time.monotonic()
        if state.get("control_failure"):
            raise RuntimeError("calibration control failed: " + state["control_failure"])
        state["status"] = "STOPPED"
        state["stopped_utc"] = datetime.now(timezone.utc).isoformat()
        state["preview_frames_published"] = frame_number
        write_state(state_path, state)
        return 0
    except BaseException as exc:
        state.update({"status": "FAILED", "failure": f"{type(exc).__name__}: {exc}",
                      "failed_utc": datetime.now(timezone.utc).isoformat()})
        try:
            write_state(state_path, state)
        except Exception:
            pass
        traceback.print_exc()
        raise
    finally:
        if loopback is not None:
            try:
                loopback.close()
            except Exception:
                pass
        if publisher is not None:
            try:
                publisher.close()
            except Exception:
                pass
        if server is not None:
            try:
                server.close()
            except Exception:
                pass
        restoration_error = None
        if capture is not None and args.calibration_mode:
            try:
                controls.close_and_restore()
                state["camera_controls"] = dict(controls.values)
                state["standard_profile_restored"] = True
                write_state(state_path, state)
            except Exception as restore_error:
                restoration_error = restore_error
                state.update({"status": "FAILED", "standard_profile_restored": False,
                              "restore_failure": str(restore_error)})
                write_state(state_path, state)
        if capture is not None:
            capture.release()
        if restoration_error is not None:
            raise restoration_error


if __name__ == "__main__":
    raise SystemExit(main())
