"""Low overhead, bounded telemetry for the standalone video pipeline."""

from collections import deque
import threading
from dataclasses import dataclass
from math import isfinite


_MAX_U64 = 2**64 - 1


def _validate_u64(value, name):
    """Validate an unsigned 64-bit integer without accepting bool."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= _MAX_U64:
        raise ValueError(f"{name} must be between 0 and 2**64 - 1")
    return value


@dataclass(frozen=True, slots=True)
class FrameTelemetry:
    frame_id: int
    capture_ns: int
    consume_ns: int | None = None
    transfer_done_ns: int | None = None
    regions_done_ns: int | None = None
    track_done_ns: int | None = None
    publish_ns: int | None = None
    compose_done_ns: int | None = None
    submit_done_ns: int | None = None

    def __post_init__(self):
        if isinstance(self.frame_id, bool) or not isinstance(self.frame_id, int):
            raise TypeError("frame_id must be an integer")

        previous = None
        for name in (
            "capture_ns",
            "consume_ns",
            "transfer_done_ns",
            "regions_done_ns",
            "track_done_ns",
            "publish_ns",
            "compose_done_ns",
            "submit_done_ns",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            value = _validate_u64(value, name)
            if previous is not None and value < previous:
                raise ValueError("provided timestamps must not go backwards")
            previous = value


def percentile(values, percent):
    """Return a percentile using NumPy's default linear interpolation rule."""
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        raise TypeError("percent must be a number")
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")

    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]

    rank = (len(ordered) - 1) * percent / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    # This is the same linear-interpolation form used by NumPy's
    # ``method='linear'`` percentile implementation.
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _summary(values):
    if not values:
        return {
            "count": 0,
            "mean": None,
            "min": None,
            "max": None,
            "p50": None,
            "p95": None,
            "p99": None,
        }
    return {
        "count": len(values),
        "mean": sum(values) / len(values),
        "min": min(values),
        "max": max(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
    }


class TelemetryWindow:
    """A bounded collection of frame and cumulative counter telemetry."""

    _METRIC_STAGES = {
        "camera_wait_ms": ("capture_ns", "consume_ns"),
        "queue_transfer_ms": ("consume_ns", "transfer_done_ns"),
        "ps_total_ms": ("transfer_done_ns", "track_done_ns"),
        "regions_ms": ("transfer_done_ns", "regions_done_ns"),
        "track_ms": ("regions_done_ns", "track_done_ns"),
        "publish_ms": ("track_done_ns", "publish_ns"),
        "compose_ms": ("publish_ns", "compose_done_ns"),
        "submit_ms": ("compose_done_ns", "submit_done_ns"),
        "e2e_ms": ("capture_ns", "submit_done_ns"),
    }

    def __init__(self, window_s=2.0, max_samples=4096):
        if isinstance(window_s, bool) or not isinstance(window_s, (int, float)):
            raise TypeError("window_s must be a number")
        if not isfinite(window_s) or window_s < 0:
            raise ValueError("window_s must be finite and non-negative")
        if isinstance(max_samples, bool) or not isinstance(max_samples, int):
            raise TypeError("max_samples must be an integer")
        if max_samples <= 0:
            raise ValueError("max_samples must be positive")
        self.window_s = float(window_s)
        self.max_samples = max_samples
        self._lock = threading.Lock()
        self._frames = deque(maxlen=max_samples)
        self._camera_previous = None
        self._camera_fps = None
        self._camera_fps_ns = None
        self._pump_previous = None
        self._extra_previous = None
        self._display_fps = None
        self._display_fps_ns = None
        self._consumed_previous = None
        self._effective_input_fps = None
        self._effective_input_fps_ns = None

    def record_frame(self, frame):
        """Record one frame on the low-overhead path.

        This method intentionally performs only a deque append; it does not
        build dictionaries, validate timestamps, or perform sampling work.
        """
        with self._lock:
            self._frames.append(frame)

    def record_camera(
        self, captured_total, overwrite_drops, sequence_errors, now_ns
    ) -> dict | None:
        """Sample cumulative camera counters at low frequency.

        Only the previous counters and timestamp are retained.  This method is
        not intended for the per-frame hot path.
        """
        self._validate_counter_values(
            (captured_total, overwrite_drops, sequence_errors), now_ns
        )
        previous = self._camera_previous
        self._camera_previous = (
            captured_total,
            overwrite_drops,
            sequence_errors,
            now_ns,
        )
        if previous is None:
            return None

        if now_ns <= previous[3] or any(
            current < old
            for current, old in zip(
                self._camera_previous[:3], previous[:3]
            )
        ):
            self._camera_fps = None
            self._camera_fps_ns = None
            return {
                "camera_fps": None,
                "camera_overwrite_drops": None,
                "camera_sequence_errors": None,
            }

        elapsed_ns = now_ns - previous[3]
        fps = (
            (captured_total - previous[0]) * 1_000_000_000 / elapsed_ns
        )
        self._camera_fps = fps
        self._camera_fps_ns = now_ns
        return {
            "camera_fps": fps,
            "camera_overwrite_drops": overwrite_drops - previous[1],
            "camera_sequence_errors": sequence_errors - previous[2],
        }

    def record_pump(
        self, submitted, skipped, submit_failures, now_ns
    ) -> dict | None:
        """Sample cumulative pump counters at low frequency.

        Only the previous counters and timestamp are retained.  This method is
        not intended for the per-frame hot path.
        """
        self._validate_counter_values((submitted, skipped, submit_failures), now_ns)
        previous = self._pump_previous
        self._pump_previous = (submitted, skipped, submit_failures, now_ns)
        if previous is None:
            return None

        if now_ns <= previous[3] or any(
            current < old
            for current, old in zip(self._pump_previous[:3], previous[:3])
        ):
            self._display_fps = None
            self._display_fps_ns = None
            return {
                "display_fps": None,
                "pump_skips": None,
                "pump_submit_failures": None,
            }

        elapsed_ns = now_ns - previous[3]
        fps = (submitted - previous[0]) * 1_000_000_000 / elapsed_ns
        self._display_fps = fps
        self._display_fps_ns = now_ns
        return {
            "display_fps": fps,
            "pump_skips": skipped - previous[1],
            "pump_submit_failures": submit_failures - previous[2],
        }

    def record_consumed(self, consumed_total, now_ns) -> dict | None:
        """Sample the cumulative consumed-frame count at low frequency."""
        _validate_u64(consumed_total, "consumed_total")
        _validate_u64(now_ns, "now_ns")
        previous = self._consumed_previous
        self._consumed_previous = (consumed_total, now_ns)
        if previous is None:
            return None

        if now_ns <= previous[1] or consumed_total < previous[0]:
            self._effective_input_fps = None
            self._effective_input_fps_ns = None
            return {"effective_input_fps": None}

        elapsed_ns = now_ns - previous[1]
        fps = (consumed_total - previous[0]) * 1_000_000_000 / elapsed_ns
        self._effective_input_fps = fps
        self._effective_input_fps_ns = now_ns
        return {"effective_input_fps": fps}

    @staticmethod
    def _validate_counter_values(values, now_ns):
        for value in values:
            _validate_u64(value, "counter total")
        _validate_u64(now_ns, "now_ns")

    @staticmethod
    def _valid_timestamp(value):
        return (
            not isinstance(value, bool)
            and isinstance(value, int)
            and 0 <= value <= _MAX_U64
        )

    @staticmethod
    def _fresh_rate(rate, sampled_ns, now_ns, cutoff_ns):
        if sampled_ns is None or not cutoff_ns <= sampled_ns <= now_ns:
            return None
        return rate

    @classmethod
    def _duration_ms(cls, frame, start_name, end_name):
        start = getattr(frame, start_name, None)
        end = getattr(frame, end_name, None)
        if not cls._valid_timestamp(start) or not cls._valid_timestamp(end):
            return None
        if end < start:
            return None
        return (end - start) / 1_000_000

    def record_extra_drops(self, preview_total, drawing_total, now_ns):
        """Track preview-publish and drawing-output drops at low frequency."""
        self._validate_counter_values((preview_total, drawing_total), now_ns)
        previous = self._extra_previous
        self._extra_previous = (preview_total, drawing_total, now_ns)
        if previous is None:
            return None
        if now_ns <= previous[2] or preview_total < previous[0] or drawing_total < previous[1]:
            return {"preview_publish_drops": None, "drawing_output_drops": None}
        return {
            "preview_publish_drops": preview_total - previous[0],
            "drawing_output_drops": drawing_total - previous[1],
        }


    def sample(self, now_ns):
        _validate_u64(now_ns, "now_ns")
        cutoff_ns = now_ns - self.window_s * 1_000_000_000
        frames = []
        with self._lock:
            snapshot = tuple(self._frames)
        for frame in snapshot:
            capture_ns = getattr(frame, "capture_ns", None)
            if (
                self._valid_timestamp(capture_ns)
                and cutoff_ns <= capture_ns <= now_ns
            ):
                frames.append(frame)

        metric_values = {name: [] for name in self._METRIC_STAGES}
        for frame in frames:
            for metric, (start_name, end_name) in self._METRIC_STAGES.items():
                duration_ms = self._duration_ms(frame, start_name, end_name)
                if duration_ms is not None:
                    metric_values[metric].append(duration_ms)
        metrics = {name: _summary(values) for name, values in metric_values.items()}
        result = {
            "frame_count": len(frames),
            "metrics": metrics,
            "camera_fps": self._fresh_rate(
                self._camera_fps, self._camera_fps_ns, now_ns, cutoff_ns
            ),
            "display_fps": self._fresh_rate(
                self._display_fps, self._display_fps_ns, now_ns, cutoff_ns
            ),
            "effective_input_fps": self._fresh_rate(
                self._effective_input_fps,
                self._effective_input_fps_ns,
                now_ns,
                cutoff_ns,
            ),
        }
        result.update(metrics)
        return result

    def reset(self):
        with self._lock:
            self._frames.clear()
        self._camera_previous = None
        self._camera_fps = None
        self._camera_fps_ns = None
        self._pump_previous = None
        self._extra_previous = None
        self._display_fps = None
        self._display_fps_ns = None
        self._consumed_previous = None
        self._effective_input_fps = None
        self._effective_input_fps_ns = None


def pl_counters_snapshot(raw):
    """Wrap opaque PL counters without implying per-module attribution."""
    base = {"schema": "material-counter.pl-counters.v1", "modules": []}
    if raw is None:
        return {
            **base,
            "clock_hz": None,
            "aggregate": None,
            "coverage": "unknown",
        }
    result = {
        **base,
        "clock_hz": raw.get("clock_hz"),
        "aggregate": raw["counters"],
        "coverage": "aggregate-only",
    }
    if "sequence" in raw:
        result["sequence"] = raw["sequence"]
    return result
