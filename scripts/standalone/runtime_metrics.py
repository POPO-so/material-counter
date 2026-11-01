"""Low-rate Linux scheduler and PL counter window metrics."""
from __future__ import annotations

from pathlib import Path
import math
import os
import time


COUNTERS = ("total_cycles", "active_cycles", "input_beats", "output_beats",
            "input_stall_cycles", "output_stall_cycles")
U64_MAX = (1 << 64) - 1
CPU_SOURCES = ("camera", "drawing", "preview_pump", "hardware_owner", "snapshots", "other")


def cpu_source_breakdown(sample, *, hertz=100, cpu_count=None):
    """Return per-thread-name CPU attribution and comparable proc counter deltas.

    Percentages use total system capacity across all CPUs. Invalid or incomplete
    input is represented by ``None`` and this diagnostic helper never raises.
    """
    empty = {name: None for name in CPU_SOURCES}
    empty.update(process_total=None, external_ssh=None, external_other=None,
                 external_process_total=None,
                 external_processes_additive_with_threads=False,
                 cpu_capacity="whole_system_all_cores_0_to_100_percent",
                 cpu_capacity_cores=None, network=None, interrupts=None)
    try:
        if not isinstance(sample, dict):
            return None
        interval = sample.get("interval_s")
        count = os.cpu_count() if cpu_count is None else cpu_count
        if (isinstance(interval, bool) or not isinstance(interval, (int, float))
                or not math.isfinite(interval) or interval <= 0
                or type(hertz) not in (int, float) or not math.isfinite(hertz) or hertz <= 0
                or type(count) is not int or count <= 0):
            return empty
        empty["cpu_capacity_cores"] = count
        threads = sample.get("threads")
        denominator = interval * hertz * count
        totals = {name: 0 for name in CPU_SOURCES}
        complete = isinstance(threads, dict) and bool(threads)
        for entry in threads.values() if isinstance(threads, dict) else ():
            if not isinstance(entry, dict):
                complete = False
                continue
            name = str(entry.get("name") or "").lower()
            if "gray8-camera" in name or "camera" in name:
                source = "camera"
            elif "drawing" in name:
                source = "drawing"
            elif "video-pump" in name or "pump" in name:
                source = "preview_pump"
            elif "video-hardware-owner" in name or "driver-owner" in name or "button" in name:
                source = "hardware_owner"
            elif "crossing-snapshot" in name or "report-export" in name:
                source = "snapshots"
            else:
                source = "other"
            user, system = entry.get("utime_ticks_delta"), entry.get("stime_ticks_delta")
            if (type(user) is not int or user < 0 or type(system) is not int or system < 0):
                complete = False
                continue
            totals[source] += user + system
        if complete:
            for source, ticks in totals.items():
                empty[source] = ticks / denominator * 100.0
            empty["process_total"] = sum(totals.values()) / denominator * 100.0
        external = sample.get("external_processes")
        external_interval = sample.get("external_interval_s")
        valid_external_interval = (isinstance(external_interval, (int, float))
                                   and not isinstance(external_interval, bool)
                                   and math.isfinite(external_interval)
                                   and external_interval > 0)
        if (sample.get("external_processes_complete") is True
                and isinstance(external, dict) and valid_external_interval):
            external_denominator = external_interval * hertz * count
            external_totals = {"ssh": 0, "other": 0}
            external_complete = True
            for entry in external.values():
                if not isinstance(entry, dict):
                    external_complete = False
                    continue
                ticks = entry.get("cpu_ticks_delta")
                if type(ticks) is not int or ticks < 0:
                    external_complete = False
                    continue
                name = str(entry.get("name") or "").lower()
                source = "ssh" if name.startswith(("ssh", "sshd")) else "other"
                external_totals[source] += ticks
            if external_complete:
                empty["external_ssh"] = external_totals["ssh"] / external_denominator * 100.0
                empty["external_other"] = external_totals["other"] / external_denominator * 100.0
                empty["external_process_total"] = (
                    sum(external_totals.values()) / external_denominator * 100.0)
        network = sample.get("network")
        if isinstance(network, dict):
            empty["network"] = network
        interrupts, softirqs = sample.get("interrupts"), sample.get("softirqs")
        if isinstance(interrupts, dict) and isinstance(softirqs, dict):
            empty["interrupts"] = {"interrupts": interrupts, "softirqs": softirqs}
        return empty
    except Exception:
        return empty


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


class ExternalProcessSampler:
    """Sample non-application process CPU at a low rate with a hard PID read cap."""
    def __init__(self, proc_root="/proc", *, interval_s=3.0, max_processes=128,
                 clock=time.monotonic):
        if not math.isfinite(interval_s) or interval_s <= 0:
            raise ValueError("interval_s must be positive")
        if type(max_processes) is not int or max_processes <= 0:
            raise ValueError("max_processes must be positive")
        self.proc_root = Path(proc_root)
        self.interval_s = float(interval_s)
        self.max_processes = max_processes
        self.clock = clock
        self._last_at = None
        self._previous = None

    def sample(self, *, excluded_pids=()):
        now = float(self.clock())
        if self._last_at is not None and now < self._last_at:
            self._previous = None
            self._last_at = now
            return None
        if self._last_at is not None and now - self._last_at < self.interval_s:
            return None
        excluded = {str(pid) for pid in excluded_pids}
        current, complete = self._read_processes(excluded)
        previous, self._previous, previous_at = self._previous, current, self._last_at
        self._last_at = now
        if previous is None:
            return None
        dt = now - previous_at
        complete = complete and set(previous) == set(current)
        deltas = {}
        if complete and dt > 0:
            for pid, entry in current.items():
                old = previous[pid]
                if entry["starttime"] != old["starttime"]:
                    complete = False
                    break
                ticks = entry["cpu_ticks"] - old["cpu_ticks"]
                if ticks < 0:
                    complete = False
                    break
                deltas[pid] = dict(name=entry["name"], cpu_ticks_delta=ticks)
        return {"external_interval_s": dt,
                "external_processes": deltas if complete else {},
                "external_processes_complete": complete}

    def _read_processes(self, excluded):
        processes = {}
        complete = True
        attempted_pids = 0
        try:
            entries = self.proc_root.iterdir()
            for path in entries:
                if not path.name.isdigit() or path.name in excluded:
                    continue
                if attempted_pids >= self.max_processes:
                    complete = False
                    break
                attempted_pids += 1
                try:
                    raw = (path / "stat").read_text()
                    close = raw.rfind(")")
                    if close < 0:
                        raise ValueError("invalid process stat")
                    fields = raw[close + 1:].split()
                    ticks = int(fields[11]) + int(fields[12])
                    starttime = int(fields[19])
                    if ticks < 0:
                        raise ValueError("negative process ticks")
                    if starttime < 0:
                        raise ValueError("negative process starttime")
                    name = raw[raw.find("(") + 1:close]
                except (OSError, ValueError, IndexError):
                    complete = False
                    continue
                processes[path.name] = {"name": name, "cpu_ticks": ticks,
                                        "starttime": starttime}
        except OSError:
            return {}, False
        return processes, complete


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
