"""Opt-in board resource telemetry with a stoppable sampling thread."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import threading
import time


class ResourceMonitor:
    """Sample host board resources only while enabled.

    ``mode`` is the UI mode (``normal`` or ``detail``); ``source`` controls
    whether real Linux board telemetry may be collected. Reader and clock
    dependencies are injectable for hardware-free tests.
    """

    def __init__(self, *, source="board", mode="normal", interval=1.0, reader=None,
                 statvfs=None, monotonic=time.monotonic, fpga_resources=None,
                 interface_provider=None, device_checker=None, device_resolver=None):
        if source not in ("board", "mock"):
            raise ValueError("source must be board or mock")
        if mode not in ("normal", "detail"):
            raise ValueError("mode must be normal or detail")
        if not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
            raise ValueError("interval must be finite and positive")
        self.source = source
        self.interval = float(interval)
        self._reader = reader or (lambda path: Path(path).read_text(encoding="ascii"))
        self._statvfs = statvfs or getattr(os, "statvfs", lambda path: (_ for _ in ()).throw(OSError("statvfs unavailable")))
        self._clock = monotonic
        self._fpga_resources = fpga_resources
        self._interface_provider = interface_provider or (
            lambda: [entry.name for entry in Path("/sys/class/net").glob("*")])
        self._device_checker = device_checker or (lambda iface: Path("/sys/class/net", iface, "device").exists())
        self._device_resolver = device_resolver or (
            lambda path: str(Path(path).resolve(strict=True)))
        self._lock = threading.RLock()
        self._control_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._enabled = False
        self._mode = mode
        self._metrics = {}
        self._cpu_baseline = None
        self._network_baseline = None
        self._sd_baseline = None

    def control(self, *, enabled, mode=None):
        # Serialize the complete stop/join/start sequence. In particular, a
        # concurrent enable cannot clear the stop event while the old worker
        # is still being joined.
        with self._control_lock:
            return self._control_serialized(enabled=enabled, mode=mode)

    def _control_serialized(self, *, enabled, mode=None):
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        if mode is not None and mode not in ("normal", "detail"):
            raise ValueError("mode must be normal or detail")
        if not enabled:
            with self._lock:
                thread = self._thread
                self._stop.set()
            if thread is not None and thread is not threading.current_thread():
                thread.join()
            with self._lock:
                self._thread = None
                self._enabled = False
                self._metrics = {}
                self._reset_baselines()
                self._stop.clear()
                if mode is not None:
                    self._mode = mode
            return self.snapshot()

        with self._lock:
            if mode is not None:
                self._mode = mode
            if not self._enabled:
                self._enabled = True
                self._metrics = {"source": "mock"} if self.source == "mock" else {}
                self._stop.clear()
                if self.source == "board":
                    self._thread = threading.Thread(target=self._run, name="resource-monitor", daemon=True)
                    self._thread.start()
            return self.snapshot()

    def snapshot(self):
        with self._lock:
            metrics = dict(self._metrics)
            return {"enabled": self._enabled, "mode": self._mode,
                    "metrics": json.loads(json.dumps(metrics, allow_nan=False))}

    def close(self):
        self.control(enabled=False)

    def _reset_baselines(self):
        self._cpu_baseline = None
        self._network_baseline = None
        self._sd_baseline = None

    def _run(self):
        while not self._stop.is_set():
            metrics = self._sample_once()
            with self._lock:
                if self._stop.is_set() or not self._enabled:
                    break
                self._metrics = metrics
            if self._stop.wait(self.interval):
                break

    def _read_cpu(self):
        fields = self._reader("/proc/stat").splitlines()[0].split()
        if fields[0] != "cpu" or len(fields) < 5:
            return None
        counters = [int(value) for value in fields[1:9]]
        total = sum(counters)
        idle = counters[3] + (counters[4] if len(counters) > 4 else 0)
        current = (total, idle)
        previous = self._cpu_baseline
        self._cpu_baseline = current
        if previous is None:
            return None
        total_delta, idle_delta = total - previous[0], idle - previous[1]
        if total_delta <= 0 or idle_delta < 0 or idle_delta > total_delta:
            return None
        value = 100 * (total_delta - idle_delta) / total_delta
        return round(value, 1) if 0 <= value <= 100 else None

    def _meminfo(self):
        result = {}
        for line in self._reader("/proc/meminfo").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].rstrip(":") in ("MemTotal", "MemAvailable", "CmaTotal", "CmaFree"):
                result[parts[0].rstrip(":")] = int(parts[1])
        return result

    def _root_filesystem(self):
        try:
            root = next(line for line in self._reader("/proc/self/mountinfo").splitlines()
                        if " - " in line and line.split(" - ", 1)[0].split()[4] == "/")
            source = root.split(" - ", 1)[1].split()[1]
            if source == "/dev/root":
                source = self._device_resolver(source)
            if not re.fullmatch(r"/dev/mmcblk\d+p\d+", source):
                return {"rootfs_total_bytes": None, "rootfs_used_bytes": None}
            stats = self._statvfs("/")
            total = int(stats.f_blocks * stats.f_frsize)
            available = int(stats.f_bavail * stats.f_frsize)
            if total <= 0 or available < 0 or available > total:
                return {"rootfs_total_bytes": None, "rootfs_used_bytes": None}
            return {"rootfs_total_bytes": total, "rootfs_used_bytes": total - available}
        except (OSError, ValueError, IndexError, StopIteration, TypeError, AttributeError):
            return {"rootfs_total_bytes": None, "rootfs_used_bytes": None}

    def _default_interface(self):
        try:
            rows = self._reader("/proc/net/route").splitlines()[1:]
            routes = []
            for row in rows:
                fields = row.split()
                if len(fields) >= 8 and fields[1] == "00000000" and int(fields[3], 16) & 1:
                    routes.append((int(fields[6]), fields[0]))
            return min(routes)[1] if routes else None
        except (OSError, ValueError, IndexError):
            return None

    def _connected_physical_interface(self):
        candidates = []
        for iface in self._interface_provider():
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", iface):
                continue
            base = f"/sys/class/net/{iface}"
            try:
                # Ethernet device type plus carrier/up state. Requiring the
                # sysfs device link excludes loopback and virtual adapters.
                if not self._device_checker(iface):
                    continue
                if self._reader(f"{base}/type").strip() != "1":
                    continue
                if self._reader(f"{base}/carrier").strip() != "1":
                    continue
                if self._reader(f"{base}/operstate").strip() != "up":
                    continue
                candidates.append(iface)
            except (OSError, ValueError):
                continue
        return candidates[0] if len(candidates) == 1 else None

    def _network(self):
        iface = self._default_interface()
        if iface is None:
            iface = self._connected_physical_interface()
        if not iface or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", iface):
            self._network_baseline = None
            return {"network_interface": None, "network_rx_bytes_per_second": None,
                    "network_tx_bytes_per_second": None, "network_link_mbps": None}
        try:
            base = f"/sys/class/net/{iface}"
            rx, tx = int(self._reader(f"{base}/statistics/rx_bytes")), int(self._reader(f"{base}/statistics/tx_bytes"))
            try:
                speed = int(self._reader(f"{base}/speed"))
            except (OSError, ValueError):
                speed = None
            now = self._clock()
            previous = self._network_baseline
            self._network_baseline = (rx, tx, now, iface)
            rx_rate = tx_rate = None
            if previous and previous[3] == iface and now > previous[2] and rx >= previous[0] and tx >= previous[1]:
                elapsed = now - previous[2]
                rx_rate = round((rx - previous[0]) / elapsed, 1)
                tx_rate = round((tx - previous[1]) / elapsed, 1)
            return {"network_interface": iface,
                    "network_rx_bytes_per_second": rx_rate,
                    "network_tx_bytes_per_second": tx_rate,
                    "network_link_mbps": speed if speed is not None and speed > 0 else None}
        except (OSError, ValueError, TypeError):
            self._network_baseline = None
            return {"network_interface": iface, "network_rx_bytes_per_second": None,
                    "network_tx_bytes_per_second": None, "network_link_mbps": None}

    def _sd_io(self):
        try:
            devices = sorted(Path("/sys/block").glob("mmcblk[0-9]"))
            totals = [0, 0]
            for device in devices:
                fields = self._reader(f"{device}/stat").split()
                if len(fields) < 7:
                    raise ValueError("invalid SD statistics")
                totals[0] += int(fields[2]) * 512
                totals[1] += int(fields[6]) * 512
            now = self._clock()
            previous = self._sd_baseline
            self._sd_baseline = (totals[0], totals[1], now)
            if not devices:
                return {"sd_device": None, "sd_read_bytes_per_second": None, "sd_write_bytes_per_second": None}
            names = ",".join(device.name for device in devices)
            if not previous or now <= previous[2]:
                return {"sd_device": names, "sd_read_bytes_per_second": None, "sd_write_bytes_per_second": None}
            elapsed = now - previous[2]
            read_delta, write_delta = totals[0] - previous[0], totals[1] - previous[1]
            if read_delta < 0 or write_delta < 0:
                return {"sd_device": names, "sd_read_bytes_per_second": None, "sd_write_bytes_per_second": None}
            return {"sd_device": names,
                    "sd_read_bytes_per_second": round(read_delta / elapsed, 1),
                    "sd_write_bytes_per_second": round(write_delta / elapsed, 1)}
        except (OSError, ValueError, IndexError):
            self._sd_baseline = None
            return {"sd_device": None, "sd_read_bytes_per_second": None, "sd_write_bytes_per_second": None}

    def _temperature(self):
        try:
            zones = sorted(Path("/sys/class/thermal").glob("thermal_zone*"))
            for zone in zones:
                kind = self._reader(f"{zone}/type").strip().lower()
                if kind in ("soc_thermal", "zynq-thermal", "xadc"):
                    value = int(self._reader(f"{zone}/temp")) / 1000
                    return round(value, 1) if -40 <= value <= 150 else None
        except (OSError, ValueError, IndexError):
            pass
        return None

    def _sample_once(self):
        metrics = {}
        try:
            metrics["cpu_percent"] = self._read_cpu()
        except (OSError, ValueError, IndexError):
            self._cpu_baseline = None
            metrics["cpu_percent"] = None
        try:
            memory = self._meminfo()
        except (OSError, ValueError):
            memory = {}
        total, available = memory.get("MemTotal"), memory.get("MemAvailable")
        metrics["memory_total_kib"] = total if isinstance(total, int) and total > 0 else None
        metrics["memory_available_kib"] = available if isinstance(available, int) and total is not None and 0 <= available <= total else None
        cma_total, cma_free = memory.get("CmaTotal"), memory.get("CmaFree")
        metrics["cma_total_kib"] = cma_total if isinstance(cma_total, int) and cma_total > 0 else None
        metrics["cma_free_kib"] = cma_free if isinstance(cma_free, int) and cma_total is not None and 0 <= cma_free <= cma_total else None
        metrics.update(self._root_filesystem())
        metrics.update(self._network())
        with self._lock:
            detail = self._mode == "detail"
        if detail:
            metrics.update(self._sd_io())
            metrics["soc_temperature_c"] = self._temperature()
            metrics["fpga_resources"] = self._fpga_resources
        return metrics
