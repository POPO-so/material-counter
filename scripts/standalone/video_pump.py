"""Threaded display pump for SharedFrames and independently owned HDMI DMA."""
from __future__ import annotations

from collections import deque
import copy
import threading
import time

import numpy as np

from .vdma_diag import capture_vdma_snapshot


RUNNING = "RUNNING"
STOPPED = "STOPPED"
DISPLAY_FAIL_HOLD = "DISPLAY_FAIL_HOLD"


class VideoPump:
    """Copy the latest shared frame into a CMA buffer and submit it to HDMI.

    Stopping this worker only stops frame submission. It does not establish
    that VDMA has halted or that retained CMA resources may be released.
    """

    DEFAULT_SUBMIT_TIMEOUT = 1.0
    TIMING_WINDOW = 20_000

    def __init__(self, outputs, hw, owner, session_uuid, submit_timeout=1.0,
                 vdma_read32=None):
        if not isinstance(submit_timeout, (int, float)) or submit_timeout <= 0:
            raise ValueError("submit_timeout must be positive")
        self.outputs = outputs
        self.hw = hw
        self.owner = owner
        self.session_uuid = session_uuid
        self.submit_timeout = float(submit_timeout)
        # Side-effect-free VDMA read function, or None to keep failure
        # evidence free of MMIO (the default).
        self.vdma_read32 = vdma_read32
        self.state = RUNNING
        self.error = None
        self.failed_at = None
        self.failed_stage = None
        self.first_failure = None
        self.last_success = None
        self.vdma_snapshot = None
        self.writeframe_submitted_at = None
        self.writeframe_returned_at = None
        self.last_frame_id = None
        self.submitted = 0
        self.timings_ms = {
            name: deque(maxlen=self.TIMING_WINDOW)
            for name in ("copy_submit_ms", "scale_ms", "draw_ms")
        }
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="video-pump", daemon=True
        )
        self._thread.start()

    def request_stop(self):
        """Request worker shutdown without waiting for driver calls to return."""
        self._stop.set()

    def join(self, timeout=None):
        """Wait up to timeout for this worker; never attempts to kill it."""
        self._thread.join(timeout)
        return not self._thread.is_alive()

    @property
    def is_alive(self):
        return self._thread.is_alive()

    def timing_percentiles_ms(self):
        """Return bounded p50/p95/p99 summaries for recorded stage timings."""
        return {
            name: {
                "p50": float(np.percentile(values, 50)) if values else None,
                "p95": float(np.percentile(values, 95)) if values else None,
                "p99": float(np.percentile(values, 99)) if values else None,
            }
            for name, values in self.timings_ms.items()
        }

    def status(self):
        """Bounded evidence snapshot for reports; no hardware access."""
        return dict(state=self.state, error=self.error, failed_at=self.failed_at,
                    failed_stage=self.failed_stage, submitted=self.submitted,
                    last_frame_id=self.last_frame_id, alive=self.is_alive)

    def _run(self):
        stage = "acquire"
        try:
            while not self._stop.is_set():
                try:
                    stage = "acquire"
                    lease = self.outputs.acquire_latest()
                except BaseException as exc:
                    self._fail(exc, stage)
                    break
                if lease is None:
                    self._stop.wait(0.001)
                    continue

                frame = None
                failed = False
                self.last_frame_id = lease.frame_id
                try:
                    stage = "session_check"
                    if lease.session_uuid != self.session_uuid:
                        raise RuntimeError(
                            "shared frame session UUID does not match video pump"
                        )
                    stage = "newframe"
                    frame = self.owner.call(self.hw.hdmi.newframe)
                    copy_started = time.monotonic()
                    stage = "copy"
                    np.copyto(frame, lease.array)
                    stage = "writeframe"
                    self.writeframe_submitted_at = time.monotonic()
                    self.writeframe_returned_at = None
                    self.owner.call(
                        self.hw.hdmi.writeframe,
                        frame,
                        timeout=self.submit_timeout,
                    )
                    self.writeframe_returned_at = time.monotonic()
                    # A successful call transfers the CMA frame to the HDMI driver.
                    frame = None
                    self.submitted += 1
                    self.last_success = dict(
                        frame_id=lease.frame_id,
                        submitted=self.submitted,
                        submitted_at=self.writeframe_submitted_at,
                        returned_at=self.writeframe_returned_at,
                        writeframe_ms=1000 * (self.writeframe_returned_at -
                                              self.writeframe_submitted_at),
                        owner=(self._owner_diagnostics() or {}).get("last_success"),
                    )
                    self.timings_ms["copy_submit_ms"].append(
                        1000 * (time.monotonic() - copy_started)
                    )
                    self.timings_ms["scale_ms"].append(lease.scale_ns / 1_000_000)
                    self.timings_ms["draw_ms"].append(lease.draw_ns / 1_000_000)
                except BaseException as exc:
                    if frame is not None:
                        try:
                            self.owner.retain(frame)
                        except BaseException as retain_exc:
                            exc = RuntimeError(
                                f"{exc}; retaining uncertain CMA frame failed: {retain_exc}"
                            )
                    self._fail(exc, stage)
                    failed = True
                finally:
                    try:
                        stage = "release"
                        self.outputs.release_read(lease.slot)
                    except BaseException as exc:
                        self._fail(exc, stage)
                        failed = True
                if failed:
                    break
        except BaseException as exc:
            self._fail(exc, stage)
        finally:
            if self.state != DISPLAY_FAIL_HOLD:
                self.state = STOPPED

    def diagnostics(self):
        """Bounded failure evidence for reports; never touches hardware."""
        return {
            "submit_timeout": self.submit_timeout,
            "vdma_capture_enabled": self.vdma_read32 is not None,
            "first_failure": (copy.deepcopy(self.first_failure)
                              if self.first_failure is not None else None),
            "last_success": (dict(self.last_success)
                             if self.last_success is not None else None),
            "vdma_snapshot": (copy.deepcopy(self.vdma_snapshot)
                              if self.vdma_snapshot is not None else None),
            "writeframe_submitted_at": self.writeframe_submitted_at,
            "writeframe_returned_at": self.writeframe_returned_at,
        }

    def _owner_diagnostics(self):
        """Snapshot the driver owner's timings without assuming its type."""
        diagnostics = getattr(self.owner, "diagnostics", None)
        if diagnostics is None:
            return None
        try:
            return diagnostics()
        except BaseException:
            return None

    def _fail(self, exc, stage=None):
        # Keep the first failure: a later release error must not hide the
        # operation that actually broke the display path.
        if self.state != DISPLAY_FAIL_HOLD:
            self.error = str(exc)
            self.failed_stage = stage
            self.failed_at = time.monotonic()
            self.first_failure = dict(
                stage=stage,
                error=self.error,
                monotonic=self.failed_at,
                frame_id=self.last_frame_id,
                submit_timeout=self.submit_timeout,
                writeframe_submitted_at=self.writeframe_submitted_at,
                writeframe_returned_at=self.writeframe_returned_at,
                owner=self._owner_diagnostics(),
                last_success=(dict(self.last_success)
                              if self.last_success is not None else None),
            )
        self.state = DISPLAY_FAIL_HOLD
        if (stage == "writeframe" and self.vdma_read32 is not None
                and self.vdma_snapshot is None):
            # Failure-only, bounded, read-only capture. A blocked or failing
            # read stays inside the snapshot and cannot replace the error above.
            self.vdma_snapshot = capture_vdma_snapshot(self.vdma_read32)
