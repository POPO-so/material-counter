"""Bounded, read-only display health accounting for the integrated run.

The monitor only classifies observations; it never touches HDMI, VDMA, CMA or
the VideoPump. Callers decide whether an event is recorded (default) or used to
request a controlled stop (explicit opt-in).
"""

from __future__ import annotations

DISPLAY_FAIL_HOLD = "DISPLAY_FAIL_HOLD"
STOPPED = "STOPPED"


class DisplayHealthMonitor:
    """Detect a dead display path without mistaking pause or jitter for failure.

    Rules:
    - paused, display disabled, or no processed progress: reset the window;
    - pump state DISPLAY_FAIL_HOLD: immediate failure event;
    - pump thread gone while not STOPPED: immediate failure event;
    - submitted unchanged over ``stall_window_s`` while at least
      ``min_processed`` frames were processed: stalled event;
    - display_fps zero for ``fps_zero_samples`` fresh telemetry samples while
      submitted is not progressing: fps-zero event.
    Each reason is reported once until a new submission proves recovery.
    """

    def __init__(self, *, stall_window_s=15.0, min_processed=20,
                 fps_zero_samples=3, startup_grace_s=30.0):
        if stall_window_s <= 0 or min_processed < 1 or fps_zero_samples < 1:
            raise ValueError("health thresholds must be positive")
        self.stall_window_s = float(stall_window_s)
        self.min_processed = int(min_processed)
        self.fps_zero_samples = int(fps_zero_samples)
        self.startup_grace_s = float(startup_grace_s)
        self._first_observe = None
        self._last_submitted = None
        self._last_progress_at = None
        self._last_processed = None
        self._zero_fps_streak = 0
        self._last_fps_sample = None
        self._last_fps_processed = None
        self._reported = None
        self.events = 0
        self.last_event = None

    def _reset(self, now, processed):
        self._last_submitted = None
        self._last_progress_at = now
        self._last_processed = processed
        self._zero_fps_streak = 0
        self._last_fps_sample = None
        self._last_fps_processed = None
        self._reported = None

    def _event(self, now, reason, processed, pump_status, display_fps):
        if self._reported == reason:
            return None
        self._reported = reason
        self.events += 1
        self.last_event = {
            "reason": reason,
            "monotonic": now,
            "processed": processed,
            "submitted": pump_status.get("submitted"),
            "last_frame_id": pump_status.get("last_frame_id"),
            "video_pump_state": pump_status.get("state"),
            "video_pump_error": pump_status.get("error"),
            "video_pump_failed_at": pump_status.get("failed_at"),
            "video_pump_failed_stage": pump_status.get("failed_stage"),
            "display_fps": display_fps,
            "action": "record_only",
        }
        return self.last_event

    def observe(self, now, processed, paused, display_enabled, pump_status,
                display_fps=None, fps_sample_time=None):
        """Return one bounded event dict when a failure is first observed."""
        if not display_enabled or paused or pump_status is None:
            self._reset(now, processed)
            return None
        if self._first_observe is None:
            self._first_observe = now
        state = pump_status.get("state")
        if state == DISPLAY_FAIL_HOLD:
            return self._event(now, "display_fail_hold", processed,
                               pump_status, display_fps)
        if not pump_status.get("alive", True) and state != STOPPED:
            return self._event(now, "pump_thread_exited", processed,
                               pump_status, display_fps)

        submitted = int(pump_status.get("submitted") or 0)
        if self._last_submitted is None:
            self._last_submitted = submitted
            self._last_progress_at = now
            self._last_processed = processed
            self._last_fps_processed = processed
        elif submitted > self._last_submitted:
            self._last_submitted = submitted
            self._last_progress_at = now
            self._last_processed = processed
            self._last_fps_processed = processed
            self._zero_fps_streak = 0
            self._reported = None
        else:
            in_grace = (submitted == 0
                        and now - self._first_observe < self.startup_grace_s)
            processed_delta = processed - (self._last_processed or processed)
            if (not in_grace
                    and now - self._last_progress_at >= self.stall_window_s
                    and processed_delta >= self.min_processed):
                return self._event(now, "submitted_stalled", processed,
                                   pump_status, display_fps)

        if display_fps is not None and fps_sample_time is not None:
            if fps_sample_time != self._last_fps_sample:
                self._last_fps_sample = fps_sample_time
                grew = (self._last_fps_processed is not None
                        and processed > self._last_fps_processed)
                self._last_fps_processed = processed
                if display_fps <= 0 and grew:
                    self._zero_fps_streak += 1
                else:
                    self._zero_fps_streak = 0
                if (self._zero_fps_streak >= self.fps_zero_samples
                        and submitted <= (self._last_submitted or 0)):
                    return self._event(now, "display_fps_zero", processed,
                                       pump_status, display_fps)
        return None