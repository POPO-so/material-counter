"""Bound split admission and retry only unchanged, uniquely matched parents.

No child masks or counted flags are cached here. An unresolved observation
never becomes a single-item detection merely because a budget was exhausted.
"""
from dataclasses import dataclass
import math
import numbers

import numpy as np


def positive_limit(value, name, *, integer=False, allow_zero=False):
    if value is None:
        return None
    valid = (isinstance(value, numbers.Integral) if integer
             else isinstance(value, numbers.Real))
    if (isinstance(value, (bool, np.bool_)) or not valid or not math.isfinite(value)
            or value < 0 or (value == 0 and not allow_zero)):
        raise ValueError(name + ' must be a finite ' + ('nonnegative' if allow_zero else 'positive') + ' number')
    return int(value) if integer else float(value)


class SplitBudget:
    """Admission between calls; an in-flight OpenCV call is not preempted."""
    def __init__(self, max_candidates, max_pixels, budget_ms):
        self.max_candidates = max_candidates
        self.max_pixels = max_pixels
        self.budget_ms = budget_ms
        self.attempts = self.pixels = 0
        self.elapsed_ms = 0.0

    def admit(self, pixels):
        if self.max_candidates is not None and self.attempts >= self.max_candidates:
            return 'candidate_budget'
        if self.max_pixels is not None and self.pixels + pixels > self.max_pixels:
            return 'pixel_budget'
        if self.budget_ms is not None and self.elapsed_ms >= self.budget_ms:
            return 'time_budget'
        self.attempts += 1
        self.pixels += pixels
        return None


@dataclass
class RetryState:
    component: object
    first_seen_s: float
    last_seen_s: float
    last_attempt_s: float | None = None
    signature: bytes | None = None
    outcome: str = 'unresolved'
    seeds: int = 0
    children: tuple = ()
    origin: tuple = (0, 0)


class SplitRetries:
    def __init__(self, interval_s, *, capacity=1024, lifetime_s=.5):
        self.interval_s = interval_s
        self.capacity = capacity
        self.lifetime_s = lifetime_s
        self.entries = []

    def prepare(self, candidates, now, *, axis, line, max_step, lateral_step, timebase):
        previous = [e for e in self.entries if 0 <= now-e.last_seen_s <= self.lifetime_s]
        edges = []
        for index, component in candidates:
            for old_index, entry in enumerate(previous):
                old = entry.component
                elapsed = max(0, now-entry.last_seen_s)
                longitudinal = abs(getattr(component, 'centroid_'+axis)-getattr(old, 'centroid_'+axis))
                other = 'y' if axis == 'x' else 'x'
                lateral = abs(getattr(component, 'centroid_'+other)-getattr(old, 'centroid_'+other))
                if (longitudinal <= max(4, max_step*elapsed/timebase)
                        and lateral <= lateral_step
                        and .8 <= component.area/old.area <= 1.25):
                    edges.append((index, old_index))
        # Ambiguous neighbours deliberately invalidate reuse, rather than
        # lending another physical object's cached hole count to this parent.
        by_new, by_old = {}, {}
        for index, old_index in edges:
            by_new.setdefault(index, []).append(old_index)
            by_old.setdefault(old_index, []).append(index)
        states = {}
        for index, component in candidates:
            owners = by_new.get(index, [])
            if len(owners) == 1 and len(by_old[owners[0]]) == 1:
                entry = previous[owners[0]]
                entry.component = component
                entry.last_seen_s = now
            else:
                entry = RetryState(component, now, now)
            states[index] = entry
        # Keep only current observations and bound persistent memory.
        self.entries = list(states.values())[:self.capacity]

        def priority(item):
            index, component = item
            entry = states[index]
            since = now-(entry.last_attempt_s if entry.last_attempt_s is not None else entry.first_seen_s)
            distance = abs(getattr(component, 'centroid_'+axis)-line)
            return (distance/max_step - since/timebase, index)

        return states, [index for index, _ in sorted(candidates, key=priority)]

    def cached(self, entry, signature, now):
        return (self.interval_s > 0 and entry.last_attempt_s is not None
                and 0 <= now-entry.last_attempt_s < self.interval_s
                and entry.signature == signature and entry.outcome in ('parent', 'single', 'split'))


def run_signature(runs, labels, index, component):
    """Exact translation-invariant run identity; does not allocate a mask."""
    words = runs[labels == index]
    normalized = (((words >> 20)-component.y_min) << 20
                  | ((((words >> 10) & 0x3ff)-component.x_min) << 10)
                  | ((words & 0x3ff)-component.x_min))
    return normalized.tobytes()
