"""Small in-process cache for timestamped telemetry samples."""

import math
import numbers


class TelemetryCache:
    """Store caller-supplied telemetry values with monotonic sample times."""

    def __init__(self):
        self._samples = {}

    def record(self, name, value, period_s, now):
        """Record a value and its sampling period at monotonic time ``now``."""
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        period = _finite_real(period_s, "period_s")
        if period <= 0:
            raise ValueError("period_s must be greater than zero")
        sampled_at = _finite_real(now, "now")
        if sampled_at < 0:
            raise ValueError("now must be non-negative")
        if not isinstance(value, (dict, numbers.Real)) or isinstance(value, bool):
            raise ValueError("value must be a numeric scalar or dict")

        previous = self._samples.get(name)
        if previous is not None and sampled_at < previous["sampled_monotonic"]:
            raise ValueError("sample time cannot move backwards for a key")
        self._samples[name] = {
            "value": value,
            "sampled_monotonic": sampled_at,
            "period_s": period,
        }

    def snapshot(self, now):
        """Return current cached values with age and staleness, without refreshing."""
        sampled_now = _finite_real(now, "now")
        if sampled_now < 0:
            raise ValueError("now must be non-negative")
        if any(sampled_now < sample["sampled_monotonic"]
               for sample in self._samples.values()):
            raise ValueError("snapshot time cannot precede a recorded sample")

        result = {}
        for name, sample in self._samples.items():
            age = sampled_now - sample["sampled_monotonic"]
            result[name] = {
                "value": sample["value"],
                "sampled_monotonic": sample["sampled_monotonic"],
                "period_s": sample["period_s"],
                "age_s": age,
                "stale": age > 2 * sample["period_s"] + 1,
            }
        return result


def _finite_real(value, name):
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError("{} must be a finite real number".format(name))
    try:
        converted = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("{} must be a finite real number".format(name)) from exc
    if not math.isfinite(converted):
        raise ValueError("{} must be a finite real number".format(name))
    return converted
