"""Threaded display pump for SharedFrames and independently owned HDMI DMA."""
from __future__ import annotations

from collections import deque
import threading
import time

import numpy as np

from scripts.standalone.video_telemetry import FrameTelemetry


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
                 *, telemetry=None):
        if not isinstance(submit_timeout, (int, float)) or submit_timeout <= 0:
            raise ValueError("submit_timeout must be positive")
        self.outputs = outputs
        self.hw = hw
        self.owner = owner
        self.session_uuid = session_uuid
        self.submit_timeout = float(submit_timeout)
        self.telemetry = telemetry
        self.state = RUNNING
        self.error = None
        self.submitted = 0
        self.skipped = 0
        self.submit_failures = 0
        self.submit_latency_ms = deque(maxlen=self.TIMING_WINDOW)
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

    def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    lease = self.outputs.wait_for_frame(timeout=0.5, stop=self._stop)
                except BaseException as exc:
                    self._fail(exc)
                    break
                if lease is None:
                    continue

                frame = None
                failed = False
                session_mismatch = False
                try:
                    if lease.session_uuid != self.session_uuid:
                        session_mismatch = True
                        raise RuntimeError(
                            "shared frame session UUID does not match video pump"
                        )
                    frame = self.owner.call(self.hw.hdmi.newframe)
                    copy_started_ns = time.monotonic_ns()
                    np.copyto(frame, lease.array)
                    self.owner.call(
                        self.hw.hdmi.writeframe,
                        frame,
                        timeout=self.submit_timeout,
                    )
                    submit_done_ns = time.monotonic_ns()
                    # A successful call transfers the CMA frame to the HDMI driver.
                    frame = None
                    self.timings_ms["copy_submit_ms"].append(
                        (submit_done_ns - copy_started_ns) / 1_000_000
                    )
                    self.submit_latency_ms.append(
                        (submit_done_ns - copy_started_ns) / 1_000_000
                    )
                    self.timings_ms["scale_ms"].append(lease.scale_ns / 1_000_000)
                    self.timings_ms["draw_ms"].append(lease.draw_ns / 1_000_000)
                    if self.telemetry is not None and lease.capture_time_ns > 0:
                        try:
                            self.telemetry.record_frame(
                                FrameTelemetry(
                                    frame_id=lease.frame_id,
                                    capture_ns=lease.capture_time_ns,
                                    publish_ns=lease.source_publish_ns or None,
                                    compose_done_ns=lease.published_ns,
                                    submit_done_ns=submit_done_ns,
                                    compose_work_ns=lease.compose_ns,
                                )
                            )
                        except BaseException:
                            pass
                    self.submitted += 1
                except BaseException as exc:
                    if session_mismatch:
                        self.skipped += 1
                    else:
                        self.submit_failures += 1
                    if frame is not None:
                        try:
                            self.owner.retain(frame)
                        except BaseException as retain_exc:
                            exc = RuntimeError(
                                f"{exc}; retaining uncertain CMA frame failed: {retain_exc}"
                            )
                    self._fail(exc)
                    failed = True
                finally:
                    try:
                        self.outputs.release_read(lease.slot)
                    except BaseException as exc:
                        self._fail(exc)
                        failed = True
                if failed:
                    break
        except BaseException as exc:
            self._fail(exc)
        finally:
            if self.state != DISPLAY_FAIL_HOLD:
                self.state = STOPPED

    def _fail(self, exc):
        self.error = str(exc)
        self.state = DISPLAY_FAIL_HOLD
