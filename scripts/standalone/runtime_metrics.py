"""Low-rate Linux scheduler and PL counter window metrics."""
from __future__ import annotations

from pathlib import Path
import math
import os
import time


COUNTERS = ("total_cycles", "active_cycles", "input_beats", "output_beats",
            "input_stall_cycles", "output_stall_cycles")
U64_MAX = (1 << 64) - 1


def counter_rates(before, after, dt_s, *, min_interval_s=0.1, max_interval_s=10.0):
    """Compute fresh deltas; decreasing counters indicate a hardware reset."""
    result = dict(fresh=False, dt_s=None, rle_activity_pct=None, input_mb_s=None,
                  output_mb_s=None, input_stall_pct=None, output_stall_pct=None,
                  channel_activity_includes_wait=True)
    try:
        dt = float(dt_s)
        if not math.isfinite(dt) or not min_interval_s <= dt <= max_interval_s:
            return result
        if (not isinstance(before, dict) or not isinstance(after, dict)
                or type(before.get("sequence")) is not int or type(after.get("sequence")) is not int
                or after["sequence"] <= before["sequence"]):
            return result
        left, right = before.get("counters"), after.get("counters")
        if not isinstance(left, dict) or not isinstance(right, dict):
            return result
        if any(type(left.get(k)) is not int or type(right.get(k)) is not int
               or not 0 <= left[k] <= U64_MAX or not 0 <= right[k] <= U64_MAX
               or right[k] < left[k] for k in COUNTERS):
            return result
        d = {k: right[k] - left[k] for k in COUNTERS}
        total = d["total_cycles"]
        if any(d[key] > total for key in ("active_cycles", "input_stall_cycles", "output_stall_cycles", "input_beats", "output_beats")):
            return result
        result.update(fresh=True, dt_s=dt,
            rle_activity_pct=(100.0 * d["active_cycles"] / total if total else None),
            input_mb_s=d["input_beats"] * 4 / dt / 1_000_000,
            output_mb_s=d["output_beats"] * 4 / dt / 1_000_000,
            input_stall_pct=(100.0 * d["input_stall_cycles"] / total if total else None),
            output_stall_pct=(100.0 * d["output_stall_cycles"] / total if total else None))
        return result
    except (TypeError, ValueError, OverflowError):
        return result


class RuntimeSampler:
    """Sample Linux procfs at most once per interval; proc_root and clock are injectable."""
    def __init__(self, proc_root="/proc", *, pid=None, interval_s=3.0, clock=time.monotonic):
        if not math.isfinite(interval_s) or interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.proc_root = Path(proc_root)
        self.pid = str(os.getpid() if pid is None else pid)
        self.interval_s = float(interval_s)
        self.clock = clock
        self._last_at = None
        self._previous = None

    def sample(self):
        now = float(self.clock())
        if self._last_at is not None and now < self._last_at:
            self._previous = None
            self._last_at = now
            self._previous_at = now
            return None
        if self._last_at is not None and now - self._last_at < self.interval_s:
            return None
        current = self._read_all()
        previous, self._previous, self._last_at = self._previous, current, now
        if previous is None or now <= getattr(self, "_previous_at", float("-inf")):
            self._previous_at = now
            return None
        dt = now - self._previous_at
        self._previous_at = now
        return _deltas(previous, current, dt)

    def _read_all(self):
        root = self.proc_root
        data = {"cpu": _read_cpu(root / "stat"), "softirqs": _read_label_counts(root / "softirqs"),
                "interrupts": _read_label_counts(root / "interrupts"),
                "network": _read_network(root / "net/dev"), "threads": {}}
        task_root = root / self.pid / "task"
        try:
            paths = sorted(task_root.iterdir(), key=lambda p: p.name)
        except OSError:
            paths = []
        for path in paths:
            try:
                fields = _read_thread(path)
            except (OSError, ValueError, IndexError):
                continue
            data["threads"][path.name] = fields
        return data


def _read_cpu(path):
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    result = {}
    for line in lines:
        parts = line.split()
        if parts and (parts[0] == "cpu" or parts[0].startswith("cpu") and parts[0][3:].isdigit()):
            try: result[parts[0]] = [int(v) for v in parts[1:]]
            except ValueError: return None
    return result or None


def _read_label_counts(path):
    result = {}
    try:
        for line in path.read_text().splitlines():
            if ":" not in line: continue
            label, values = line.split(":", 1)
            numbers = []
            for token in values.split():
                try: numbers.append(int(token))
                except ValueError: break
            if numbers: result[label.strip()] = sum(numbers)
    except OSError:
        return None
    return result or None


def _read_network(path):
    result = {}
    try:
        for line in path.read_text().splitlines():
            if ':' not in line:
                continue
            name, fields = line.split(':', 1)
            fields = fields.split()
            if len(fields) != 16:
                return None
            result[name.strip()] = dict(rx_bytes=int(fields[0]), rx_packets=int(fields[1]),
                                        tx_bytes=int(fields[8]), tx_packets=int(fields[9]))
    except (OSError, ValueError):
        return None
    return result or None


def _read_thread(path):
    raw = (path / "stat").read_text()
    close = raw.rfind(")")
    name = (path / "comm").read_text().strip() if (path / "comm").exists() else raw[raw.find("(")+1:close]
    fields = raw[close + 1:].split()
    utime, stime = int(fields[11]), int(fields[12])
    try: sched = (path / "schedstat").read_text().split()
    except OSError: sched = []
    return {"name": name, "utime_ticks": utime, "stime_ticks": stime,
            "schedstat_run_ns": int(sched[0]) if len(sched) >= 2 else None,
            "schedstat_wait_ns": int(sched[1]) if len(sched) >= 2 else None}


def _deltas(old, new, dt):
    def delta_map(a, b):
        if a is None or b is None: return None
        result = {}
        for key, value in a.items():
            if key not in b: return None
            current = b[key]
            if isinstance(value, list) and isinstance(current, list):
                if len(current) != len(value) or any(y < x for x, y in zip(value, current)): return None
                result[key] = [y - x for x, y in zip(value, current)]
            elif type(value) is int and type(current) is int:
                if current < value: return None
                result[key] = current - value
            else: return None
        return result
    threads = {}
    for tid, current in new["threads"].items():
        before = old["threads"].get(tid)
        entry = {"name": current["name"]}
        for field in ("utime_ticks", "stime_ticks", "schedstat_run_ns", "schedstat_wait_ns"):
            previous_value = before.get(field) if before else None
            value = current.get(field)
            key = field + "_delta"
            entry[key] = (value - previous_value if previous_value is not None
                          and value is not None and value >= previous_value else None)
        threads[tid] = entry
    network = None
    if old.get("network") is not None and new.get("network") is not None:
        network = {name: delta_map(old["network"].get(name), values)
                   for name, values in new["network"].items()}
    return {"interval_s": dt, "network": network, "cpu": delta_map(old["cpu"], new["cpu"]),
            "softirqs": delta_map(old["softirqs"], new["softirqs"]),
            "interrupts": delta_map(old["interrupts"], new["interrupts"]), "threads": threads}
