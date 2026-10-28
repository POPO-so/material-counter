"""Bounded, hardware-free telemetry and frame-boundary monitor controls."""
from __future__ import annotations

from collections import deque
import copy
from dataclasses import replace
import lighting_profile
from functools import lru_cache
from pathlib import Path
import os
import math
import threading
import time


@lru_cache(maxsize=1)
def _windows_rss_api():
    """Create ctypes structures once: POINTER's cache retains each type."""
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    return kernel, psapi, Counters


def process_rss_kib():
    """Current resident set, not a historical high-water mark."""
    if os.name == "nt":
        import ctypes
        kernel, psapi, Counters = _windows_rss_api()
        value = Counters()
        value.cb = ctypes.sizeof(value)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb):
            return None
        return int(value.WorkingSetSize // 1024)
    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        pass
    return None


class LiveMonitorState:
    def __init__(self, *, mode, history_limit=120, mm_per_pixel=0.75, clock=time.monotonic):
        if mode not in ("mock", "board") or not isinstance(history_limit, int) or history_limit < 2:
            raise ValueError("mode and history_limit (>=2) are required")
        if not math.isfinite(mm_per_pixel) or mm_per_pixel <= 0:
            raise ValueError("mm-per-pixel must be finite and positive")
        self.mode = mode
        self.mm_per_pixel = mm_per_pixel
        self.clock = clock
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.completed_times = deque(maxlen=history_limit)
        self.velocity_history = deque(maxlen=history_limit)
        self.last_crossing_area_px = None
        self.events = deque(maxlen=30)
        self.started = clock()
        self.frames = self.raw_total = self.base = self.total = self.detections = 0
        self.reset_requested = False
        self.reset_sequence = 0
        self.view_mode = "overlay"
        self.phase = "waiting"
        self.health = {"cma_free_kib": None, "rss_kib": process_rss_kib(), "dma_status": None}
        self._cpu_sample = None
        self.camera_controls = None
        self.invalidate_preview = lambda: None

    def control(self, action, payload):
        if not isinstance(payload, dict):
            raise ValueError("control body must be a JSON object")
        with self.lock:
            if action in ("reset", "stop") and payload:
                raise ValueError("reset/stop body must be {}")
            if action == "reset":
                if self.phase in ("failed", "fail_hold", "stopped") or self.stop.is_set():
                    raise ValueError("monitor is not running")
                self.reset_requested = True
                return {"pending": True, "reset_sequence": self.reset_sequence}
            if action == "view":
                if set(payload) != {"mode"} or payload["mode"] not in ("overlay", "binary"):
                    raise ValueError("mode must be overlay or binary")
                self.view_mode = payload["mode"]
            elif action == "stop":
                self.stop.set()
                self.phase = "stopping"
            else:
                raise ValueError("unknown live control")
        if action == "view":
            self.invalidate_preview()
        return {"view_mode": self.view_mode, "phase": self.phase}

    def complete_frame(self, result):
        with self.lock:
            if self.reset_requested:
                # New events on this boundary frame belong to the new batch.
                self.base = self.raw_total
                self.reset_requested = False
                self.reset_sequence += 1
                self.last_crossing_area_px = None
                self.invalidate_preview()
                self.events.append({"id": self.frames, "time": time.time(), "message": "计数已清零"})
            self.raw_total = result.total_count
            self.total = self.raw_total - self.base
            self.frames += 1
            self.detections = len(result.detections)
            self.completed_times.append(self.clock())
            tracks = {track["track_id"]: track for track in getattr(result, "tracks", ())}
            velocities = [track.get("forward_velocity") for track in tracks.values()
                          if not track.get("missed", 0) and not track.get("finished", False)]
            velocities = [value for value in velocities if self._valid_velocity(value)]
            # One sample per completed frame: multiple targets do not overweight a frame.
            self.velocity_history.append(sum(velocities) / len(velocities) if velocities else None)
            for event in getattr(result, "events", ()):
                track = tracks.get(event.track_id, {})
                area = track.get("detection", {}).get("area")
                velocity = track.get("forward_velocity")
                velocity = velocity if self._valid_velocity(velocity) else None
                self.last_crossing_area_px = area
                size_hint = (lighting_profile.classify_area(area) if getattr(self,"counting_profile",None) == "lighting-calibrated" else "unknown")
                material_hint = (lighting_profile.classify_material_area(area)
                                 if getattr(self,"counting_profile",None) == "lighting-calibrated" else None)
                details = f"面积 {area}px" if area is not None else "面积 N/A"
                details += f"，速度 {velocity:.2f} px/帧" if velocity is not None else "，速度 N/A"
                if size_hint != "unknown":
                    details += f"，尺寸 {size_hint}"
                if material_hint:
                    details += f"，候选物料 {material_hint}（面积区间暂定）"
                recovery = "；触发速度预测跨帧恢复" if event.inferred_gap else ""
                self.events.append({"id": self.frames, "time": time.time(),
                                    "track_id": event.track_id, "area_px": area, "area_size_hint": size_hint, "material_hint": material_hint,
                                    "px_per_frame": velocity, "inferred_gap": event.inferred_gap,
                                    "message": f"T{event.track_id} 越线计数，{details}{recovery}"})
            self.phase = "stopping" if self.stop.is_set() else "running"
            return replace(result, total_count=self.total)

    @staticmethod
    def _valid_velocity(value):
        return isinstance(value, (int, float)) and math.isfinite(value) and value >= 0

    def set_phase(self, phase, message):
        with self.lock:
            self.phase = phase
            self.events.append({"id": self.frames, "time": time.time(), "message": str(message)[:500]})

    def update_health(self, *, cma_free_kib=None, dma_status=None):
        with self.lock:
            metrics = {"cpu_percent": None, "memory_total_kib": None,
                       "memory_available_kib": None, "cma_total_kib": None}
            if self.mode == "board":
                try:
                    values = {}
                    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[0].rstrip(":") in ("MemTotal", "MemAvailable", "CmaTotal"):
                            values[parts[0].rstrip(":")] = int(parts[1])
                    metrics.update(memory_total_kib=values.get("MemTotal"),
                                   memory_available_kib=values.get("MemAvailable"),
                                   cma_total_kib=values.get("CmaTotal"))
                except (OSError, ValueError, UnicodeError):
                    pass
                try:
                    fields = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0].split()
                    if fields[0] != "cpu" or len(fields) < 5:
                        raise ValueError("invalid CPU sample")
                    counters = [int(value) for value in fields[1:9]]
                    total, idle = sum(counters), counters[3] + (counters[4] if len(counters) > 4 else 0)
                    if self._cpu_sample is not None:
                        delta = total - self._cpu_sample[0]
                        idle_delta = idle - self._cpu_sample[1]
                        if delta > 0 and 0 <= idle_delta <= delta:
                            metrics["cpu_percent"] = 100 * (delta - idle_delta) / delta
                    self._cpu_sample = (total, idle)
                except (OSError, ValueError, IndexError, UnicodeError):
                    self._cpu_sample = None
            self.health = {"cma_free_kib": cma_free_kib, "rss_kib": process_rss_kib(),
                           "dma_status": dma_status, **metrics}

    def set_camera_controls(self, report):
        if not isinstance(report, dict) or report.get("required_controls_verified") is not True:
            raise ValueError("camera report must confirm all required controls")
        required = report.get("required_controls")
        expected = lighting_profile.CAMERA_CONTROLS
        if required != expected:
            raise ValueError("camera report does not match calibrated required controls")
        gain = report.get("gain")
        if not isinstance(gain, dict) or gain.get("requested") != 0:
            raise ValueError("camera report must include the requested gain=0 status")
        if gain.get("status") == "not_exposed":
            if (gain.get("verification") != "not_verified"
                    or gain.get("message") != lighting_profile.GAIN_UNVERIFIED_MESSAGE):
                raise ValueError("unexposed gain must carry the explicit V4L2 exception")
        elif gain.get("status") != "verified" or gain.get("value") != 0:
            raise ValueError("gain must be read back as zero or explicitly reported unexposed")
        with self.lock:
            self.camera_controls = copy.deepcopy(report)

    def snapshot(self):
        with self.lock:
            now = self.clock()
            age = None if not self.completed_times else max(0.0, now - self.completed_times[-1])
            fresh = age is not None and age <= 5 and self.phase == "running"
            span = 0 if len(self.completed_times) < 2 else self.completed_times[-1] - self.completed_times[0]
            fps = (len(self.completed_times) - 1) / span if fresh and span > 0 else None
            samples = [value for value in self.velocity_history if value is not None]
            px_frame = (sum(samples) / len(samples) if fresh and samples
                        and self.velocity_history[-1] is not None else None)
            px_second = px_frame * fps if px_frame is not None and fps is not None else None
            verified = "frame_verified" if fresh else "unknown"
            telemetry = {"source": "pl_dma" if self.mode == "board" else "software", "mode": self.mode,
                         "phase": self.phase, "total_count": self.total, "detections": self.detections,
                         "frames_processed": self.frames, "processing_fps": fps,
                         "px_per_frame": px_frame, "px_per_second": px_second,
                         "cm_per_second": px_second * self.mm_per_pixel / 10 if px_second is not None else None,
                         "mm_per_pixel": self.mm_per_pixel,
                         "speed_kind": "observed-track frame means / completed-frame sliding window",
                         "counting_profile": getattr(self,"counting_profile",None),
                         "preprocessing_config": getattr(self,"preprocessing_config",None),
                         "area_hints_provisional": getattr(self,"counting_profile",None) == "lighting-calibrated",
                         "material_area_labels": lighting_profile.AREA_HINTS["material_labels"] if getattr(self,"counting_profile",None) == "lighting-calibrated" else None,
                         "area_size_ranges": lighting_profile.AREA_HINTS["ranges"] if getattr(self,"counting_profile",None) == "lighting-calibrated" else None,
                         "camera_controls": copy.deepcopy(self.camera_controls),
                         "last_crossing_area_px": self.last_crossing_area_px,
                         "fps_kind": "completed-frame sliding window", "elapsed_seconds": now - self.started,
                         "last_frame_age_seconds": age, "stale": not fresh,
                         "camera": verified if self.mode == "board" else "disabled",
                         "fpga": verified if self.mode == "board" else "disabled",
                         "dma": verified if self.mode == "board" else "disabled",
                         "events": list(self.events), "reset_sequence": self.reset_sequence,
                         "reset_pending": self.reset_requested, "view_mode": self.view_mode,
                         **self.health}
            return {"dashboard": telemetry, "status": self.phase, "resource_health": dict(self.health),
                    "camera_controls": copy.deepcopy(self.camera_controls),
                    "view_mode": self.view_mode}
