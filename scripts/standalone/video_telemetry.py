"""Low overhead, bounded telemetry for the standalone video pipeline."""

from collections import deque
from dataclasses import dataclass
from math import fsum, isfinite


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
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer or None")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")


def percentile(values, percent):
    """Return a percentile using NumPy's default linear interpolation rule."""
    ordered = sorted(values)
    if not ordered:
        return None
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percent / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return fsum((ordered[lower] * (1 - fraction), ordered[upper] * fraction))


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
        self._frames = deque(maxlen=max_samples)
        self._camera = deque(maxlen=max_samples)
        self._pump = deque(maxlen=max_samples)
        self._camera_totals = None
        self._pump_totals = None

    def record_frame(self, frame):
        self._frames.append(frame)

    def record_camera(
        self, captured_total, overwrite_drops, sequence_errors, now_ns
    ) -> dict | None:
        self._validate_counters((captured_total, overwrite_drops, sequence_errors), now_ns)
        previous = self._camera_totals
        self._camera_totals = (captured_total, overwrite_drops, sequence_errors, now_ns)
        self._camera.append((now_ns, captured_total))
        if previous is None:
            return None
        elapsed_ns = now_ns - previous[3]
        fps = (captured_total - previous[0]) * 1_000_000_000 / elapsed_ns if elapsed_ns > 0 else 0.0
        return {
            "camera_fps": fps,
            "camera_overwrite_drops": overwrite_drops - previous[1],
            "camera_sequence_errors": sequence_errors - previous[2],
        }

    def record_pump(
        self, submitted, skipped, submit_failures, now_ns
    ) -> dict | None:
        self._validate_counters((submitted, skipped, submit_failures), now_ns)
        previous = self._pump_totals
        self._pump_totals = (submitted, skipped, submit_failures, now_ns)
        self._pump.append((now_ns, submitted))
        if previous is None:
            return None
        elapsed_ns = now_ns - previous[3]
        fps = (submitted - previous[0]) * 1_000_000_000 / elapsed_ns if elapsed_ns > 0 else 0.0
        return {
            "display_fps": fps,
            "pump_skips": skipped - previous[1],
            "pump_submit_failures": submit_failures - previous[2],
        }

    @staticmethod
    def _validate_counters(values, now_ns):
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("counter totals must be non-negative integers")
        if isinstance(now_ns, bool) or not isinstance(now_ns, int) or now_ns < 0:
            raise ValueError("now_ns must be a non-negative integer")

    def sample(self, now_ns):
        if isinstance(now_ns, bool) or not isinstance(now_ns, int) or now_ns < 0:
            raise ValueError("now_ns must be a non-negative integer")
        cutoff_ns = now_ns - self.window_s * 1_000_000_000
        frames = [frame for frame in self._frames if frame.capture_ns >= cutoff_ns]
        metric_values = {name: [] for name in self._METRIC_STAGES}
        for frame in frames:
            for metric, (start_name, end_name) in self._METRIC_STAGES.items():
                start = getattr(frame, start_name)
                end = getattr(frame, end_name)
                if start is not None and end is not None:
                    metric_values[metric].append((end - start) / 1_000_000)
        metrics = {name: _summary(values) for name, values in metric_values.items()}
        result = {
            "frame_count": len(frames),
            "metrics": metrics,
            "camera_fps": self._fps(self._camera, cutoff_ns),
            "display_fps": self._fps(self._pump, cutoff_ns),
        }
        result.update(metrics)
        return result

    @staticmethod
    def _fps(samples, cutoff_ns):
        in_window = [sample for sample in samples if sample[0] >= cutoff_ns]
        if len(in_window) < 2:
            return None
        elapsed_ns = in_window[-1][0] - in_window[0][0]
        if elapsed_ns <= 0:
            return None
        delta = in_window[-1][1] - in_window[0][1]
        return delta * 1_000_000_000 / elapsed_ns

    def reset(self):
        self._frames.clear()
        self._camera.clear()
        self._pump.clear()
        self._camera_totals = None
        self._pump_totals = None


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
