"""Bounded periodic diagnostic snapshots, separate from the final session JSON.

The writer is opt-in. It keeps at most one pending snapshot, writes with a
single atomic replace and never fsyncs, so the counting loop is not blocked by
storage. The final report remains the authoritative session record.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import threading

SNAPSHOT_SCHEMA = "material-counter.integrated-run.snapshot.v1"

_SNAPSHOT_KEYS = (
    "boot_id", "session_id", "main_pid", "state", "processed", "submitted",
    "published", "dropped_preview", "duration_s", "total_count",
    "invalid_frames", "camera_released", "buffers_released",
    "rle_buffers_released", "display_held", "display_held_reason",
    "cleanup_stage", "hdmi_close_attempted", "video_pump_state",
    "video_pump_error", "video_pump_failed_at", "video_pump_failed_stage",
    "video_pump_last_frame_id", "pre_stop_video_state", "dma_after",
    "vdma_after", "monitor_after", "timings_ms", "after_memory",
    "video_pump_diagnostics", "video_owner_diagnostics", "vdma_diag_mmio",
)


def _tail(value, limit):
    if value is None:
        return []
    items = list(value)
    return items[-limit:] if limit > 0 else []


def compact_diagnostic_snapshot(report, *, max_samples=40, max_events=20):
    """Build a bounded snapshot; never includes frame_trace or raw images."""
    snapshot = {"schema": SNAPSHOT_SCHEMA, "snapshot": True}
    for key in _SNAPSHOT_KEYS:
        if key in report:
            snapshot[key] = report.get(key)
    snapshot["samples"] = _tail(report.get("samples"), max_samples)
    snapshot["recoveries"] = _tail(report.get("recoveries"), max_events)
    snapshot["events"] = _tail(report.get("events"), max_events)
    snapshot["display_health_events"] = _tail(
        report.get("display_health_events"), max_events)
    return snapshot


class DiagnosticSnapshotWriter:
    def __init__(self, path, *, max_bytes=262144):
        if max_bytes < 1024:
            raise ValueError("max_bytes must be at least 1024")
        self.path = Path(path)
        self.max_bytes = int(max_bytes)
        self.stats = {"submitted": 0, "written": 0, "dropped": 0,
                      "errors": 0, "last_bytes": 0}
        self._queue = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run, name="diag-snapshot", daemon=True)
        self._thread.start()

    def submit(self, snapshot):
        """Queue one snapshot without blocking; drop when the writer is busy."""
        if self._closed:
            raise RuntimeError("snapshot writer is closed")
        self.stats["submitted"] += 1
        try:
            self._queue.put_nowait(snapshot)
            return True
        except queue.Full:
            self.stats["dropped"] += 1
            return False

    @staticmethod
    def _encode(snapshot):
        return json.dumps(snapshot, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")

    def _trim(self, snapshot):
        data = self._encode(snapshot)
        while len(data) > self.max_bytes and snapshot.get("samples"):
            samples = snapshot["samples"]
            snapshot["samples"] = samples[:max(1, len(samples) // 2)]
            data = self._encode(snapshot)
        return data

    def _write(self, snapshot):
        data = self._trim(snapshot)
        if len(data) > self.max_bytes:
            self.stats["dropped"] += 1
            return
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            with open(temporary, "wb") as stream:
                stream.write(data)
            os.replace(temporary, self.path)
            self.stats["written"] += 1
            self.stats["last_bytes"] = len(data)
        except OSError:
            self.stats["errors"] += 1
            try:
                temporary.unlink()
            except OSError:
                pass

    def _run(self):
        while not self._stop.is_set():
            try:
                snapshot = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if snapshot is None:
                break
            self._write(snapshot)

    def close(self, timeout=1.0):
        if self._closed:
            return self.stats
        self._closed = True
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout)
        return self.stats