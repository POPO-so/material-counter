"""Read-only, bounded telemetry from completed frames; never touches hardware."""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any


class DashboardState:
    def __init__(self, *, source: str, clock=time.monotonic):
        if source not in ("pl_dma", "software"):
            raise ValueError("source must be pl_dma or software")
        self.source = source
        self._clock = clock
        self._lock = threading.Lock()
        self._first = self._last = self._ended = None
        self._frames = 0
        self._total = self._detections = None
        self._phase = "waiting"
        self._events: deque[dict[str, Any]] = deque(maxlen=30)
        self._event_id = 0

    def _event(self, message: str) -> None:
        self._event_id += 1
        self._events.append({"id": self._event_id, "time": time.time(),
                             "message": str(message)[:500]})

    def set_phase(self, phase: str, message: str) -> None:
        with self._lock:
            self._phase = phase
            if phase in ("complete", "failed", "stopped"):
                self._ended = self._clock()
            self._event(message)

    def on_frame(self, result: Any, _mask: Any = None) -> None:
        now = self._clock()
        with self._lock:
            if self._first is None:
                self._first = now
                self._event("收到首个已处理帧" if self.source == "software"
                            else "收到首个 DMA / Packer 校验完成的计数帧")
            self._last = now
            self._frames += 1
            total = int(result.total_count)
            if total != self._total:
                self._event(f"累计计数：{total}")
            self._total = total
            self._detections = len(result.detections)
            self._phase = "running"

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            age = None if self._last is None else max(0.0, now - self._last)
            stale = age is None or age > 5.0
            fresh = not stale and self._phase == "running"
            elapsed = (None if self._first is None else
                       max(0.0, (now if self._ended is None else self._ended) - self._first))
            # Completion-to-completion rate, excluding camera setup and warmup.
            span = None if self._first is None else self._last - self._first
            fps = ((self._frames - 1) / span
                   if fresh and span is not None and span > 0 else None)
            return {"source": self.source, "phase": self._phase,
                    "total_count": self._total, "detections": self._detections,
                    "frames_processed": self._frames, "processing_fps": fps,
                    "fps_kind": "completed-frame session mean",
                    "elapsed_seconds": elapsed, "last_frame_age_seconds": age,
                    "stale": stale,
                    "camera": "frames_received" if fresh else "unknown",
                    "fpga": ("disabled" if self.source == "software" else
                             "frame_verified" if fresh else "unknown"),
                    "dma": ("disabled" if self.source == "software" else
                            "frame_verified" if fresh else "unknown"),
                    "events": list(self._events)}
