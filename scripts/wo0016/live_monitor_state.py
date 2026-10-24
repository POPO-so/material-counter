"""Bounded, hardware-free telemetry and frame-boundary monitor controls."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
import os
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
    def __init__(self, *, mode, history_limit=120, clock=time.monotonic):
        if mode not in ("mock", "board") or not isinstance(history_limit, int) or history_limit < 2:
            raise ValueError("mode and history_limit (>=2) are required")
        self.mode = mode
        self.clock = clock
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.completed_times = deque(maxlen=history_limit)
        self.events = deque(maxlen=30)
        self.started = clock()
        self.frames = self.raw_total = self.base = self.total = self.detections = 0
        self.reset_requested = False
        self.reset_sequence = 0
        self.view_mode = "overlay"
        self.phase = "waiting"
        self.health = {"cma_free_kib": None, "rss_kib": process_rss_kib(), "dma_status": None}
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
                self.invalidate_preview()
                self.events.append({"id": self.frames, "time": time.time(), "message": "计数已清零"})
            self.raw_total = result.total_count
            self.total = self.raw_total - self.base
            self.frames += 1
            self.detections = len(result.detections)
            self.completed_times.append(self.clock())
            self.phase = "stopping" if self.stop.is_set() else "running"
            return replace(result, total_count=self.total)

    def set_phase(self, phase, message):
        with self.lock:
            self.phase = phase
            self.events.append({"id": self.frames, "time": time.time(), "message": str(message)[:500]})

    def update_health(self, *, cma_free_kib=None, dma_status=None):
        with self.lock:
            self.health = {"cma_free_kib": cma_free_kib, "rss_kib": process_rss_kib(),
                           "dma_status": dma_status}

    def snapshot(self):
        with self.lock:
            now = self.clock()
            age = None if not self.completed_times else max(0.0, now - self.completed_times[-1])
            fresh = age is not None and age <= 5 and self.phase == "running"
            span = 0 if len(self.completed_times) < 2 else self.completed_times[-1] - self.completed_times[0]
            fps = (len(self.completed_times) - 1) / span if fresh and span > 0 else None
            verified = "frame_verified" if fresh else "unknown"
            telemetry = {"source": "pl_dma" if self.mode == "board" else "software", "mode": self.mode,
                         "phase": self.phase, "total_count": self.total, "detections": self.detections,
                         "frames_processed": self.frames, "processing_fps": fps,
                         "fps_kind": "completed-frame sliding window", "elapsed_seconds": now - self.started,
                         "last_frame_age_seconds": age, "stale": not fresh,
                         "camera": verified if self.mode == "board" else "disabled",
                         "fpga": verified if self.mode == "board" else "disabled",
                         "dma": verified if self.mode == "board" else "disabled",
                         "events": list(self.events), "reset_sequence": self.reset_sequence,
                         "reset_pending": self.reset_requested, "view_mode": self.view_mode,
                         **self.health}
            return {"dashboard": telemetry, "status": self.phase, "resource_health": dict(self.health),
                    "view_mode": self.view_mode}
