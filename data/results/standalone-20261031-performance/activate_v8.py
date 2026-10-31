#!/usr/bin/env python3
"""One-time, preconditioned v8 namespace switch. Generate/review only; do not run here."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time


OLD_ROOT = Path("/opt/material-counter/releases/standalone-20261031-v7-count-5272587")
NEW_ROOT = Path("/opt/material-counter/releases/standalone-20261031-v8-perf-e2fb595")
OLD_INDEX = "ca9348788e98efe187752a29dcbe3ddc0970efa8ff85a1f8e43bbee9142ae58a"
NEW_INDEX = "e127d991371f5077bd270b2ed037508d2e76c5dfdd9c9573940fdfb2a4d1707e"
EXPECTED_BOOT = "f1462f32-61d2-44c7-9b42-d46d7d4d64c6"
CURRENT = Path("/opt/material-counter/current")
ENV = Path("/etc/material-counter/package.env")
SERVICE = "material-counter-integrated.service"
CAMERA_REPORT = Path("/tmp/perf-v8-camera-600.json")
RUN_DIR = Path("/var/lib/material-counter") / EXPECTED_BOOT
BACKUP = RUN_DIR / "pre-perf-e2fb595.env"
ACTIVATION_RECORD = RUN_DIR / "v8-perf-e2fb595-activation.json"
LOCKS = (Path("/tmp/material-counter-desktop.lock"),
         Path("/run/material-standalone.lock"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def parse_env(raw: bytes) -> dict[str, str]:
    values = {}
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key in values:
            raise ValueError("invalid or duplicate package.env assignment")
        values[key] = value.strip()
    if set(values) != {"PACKAGE_ROOT", "PACKAGE_INDEX_SHA256"}:
        raise ValueError("package.env keys differ from the reviewed pair")
    return values


def systemd_state() -> dict[str, str]:
    result = subprocess.run(
        ["systemctl", "show", "--property=ActiveState", "--property=MainPID", SERVICE],
        check=True, capture_output=True, text=True)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def require_stopped_report() -> dict:
    report = json.loads(CAMERA_REPORT.read_text(encoding="utf-8"))
    required = (report.get("state") == "STOPPED" and report.get("processed") == 600
                and report.get("invalid_frames") == 0
                and report.get("camera_released") is True
                and report.get("buffers_released") is True
                and report.get("rle_buffers_released") is True)
    if not required:
        raise RuntimeError("v8 600-frame STOPPED/resource-release evidence is incomplete")
    return {key: report.get(key) for key in
            ("state", "processed", "invalid_frames", "camera_released",
             "buffers_released", "rle_buffers_released")}


def require_new_package() -> dict:
    sys.path.insert(0, str(NEW_ROOT))
    from scripts.standalone.delivery import verify_package
    result = verify_package(NEW_ROOT, NEW_INDEX)
    if result.get("status") != "PASS":
        raise RuntimeError("new release verify_package did not pass")
    return result


def require_dma_idle() -> dict:
    from pynq import MMIO
    dma = MMIO(0x40400000, 0x1000)
    mm2s = dma.read(0x04)
    s2mm = dma.read(0x34)
    error_mask = 0x770
    for name, status in (("MM2S", mm2s), ("S2MM", s2mm)):
        if not status & 0x2 or status & error_mask:
            raise RuntimeError(f"{name} is not idle/error-free: 0x{status:08x}")
    vdma = MMIO(0x43000000, 0x1000)
    control, status = vdma.read(0x00), vdma.read(0x04)
    if control & 1 or not status & 1 or status & error_mask:
        raise RuntimeError(
            f"VDMA is not halted/error-free: control=0x{control:08x}, status=0x{status:08x}")
    return {"mm2s_status": mm2s, "s2mm_status": s2mm,
            "vdma_control": control, "vdma_status": status,
            "error_mask": error_mask}


def acquire_locks():
    streams = []
    try:
        for path in LOCKS:
            stream = path.open("r+")
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            streams.append(stream)
    except BaseException:
        for stream in streams:
            stream.close()
        raise
    return streams


def write_new(path: Path, data: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def replace_env(data: bytes, mode: int, suffix: str) -> None:
    temp = ENV.with_name(f".{ENV.name}.perf-e2fb595-{suffix}-{os.getpid()}.tmp")
    try:
        write_new(temp, data, mode)
        os.replace(temp, ENV)
        fsync_dir(ENV.parent)
    finally:
        temp.unlink(missing_ok=True)


def replace_current(target: str, suffix: str) -> None:
    temp = CURRENT.with_name(f".{CURRENT.name}.perf-e2fb595-{suffix}-{os.getpid()}.tmp")
    try:
        os.symlink(target, temp)
        os.replace(temp, CURRENT)
        fsync_dir(CURRENT.parent)
    finally:
        temp.unlink(missing_ok=True)


def activate() -> dict:
    if os.geteuid() != 0 or os.environ.get("XILINX_XRT") != "/usr":
        raise RuntimeError("activation requires root and XILINX_XRT=/usr")
    if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != EXPECTED_BOOT:
        raise RuntimeError("boot ID differs from the reviewed boot")
    if not RUN_DIR.is_dir():
        raise RuntimeError("boot runtime directory is unavailable")
    if BACKUP.exists() or BACKUP.is_symlink() or ACTIVATION_RECORD.exists() or ACTIVATION_RECORD.is_symlink():
        raise RuntimeError("backup or activation record already exists")
    if not CURRENT.is_symlink() or os.readlink(CURRENT) != str(OLD_ROOT):
        raise RuntimeError("current does not point to the expected old release")

    old_env = ENV.read_bytes()
    old_values = parse_env(old_env)
    if old_values != {"PACKAGE_ROOT": str(OLD_ROOT), "PACKAGE_INDEX_SHA256": OLD_INDEX}:
        raise RuntimeError("package.env differs from the expected old release identity")
    service = systemd_state()
    if service.get("ActiveState") != "inactive" or service.get("MainPID") != "0":
        raise RuntimeError(f"service is not inactive with MainPID=0: {service}")
    stopped = require_stopped_report()
    package = require_new_package()
    locks = acquire_locks()
    try:
        if not CURRENT.is_symlink() or os.readlink(CURRENT) != str(OLD_ROOT):
            raise RuntimeError("current changed during activation preflight")
        if parse_env(ENV.read_bytes()) != old_values:
            raise RuntimeError("package.env changed during activation preflight")
        if systemd_state() != service:
            raise RuntimeError("service state changed during activation preflight")
        if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != EXPECTED_BOOT:
            raise RuntimeError("boot changed during activation preflight")
        dma = require_dma_idle()

        env_mode = stat.S_IMODE(ENV.stat().st_mode)
        write_new(BACKUP, old_env, env_mode)
        fsync_dir(RUN_DIR)
        record_created = False
        try:
            replace_current(str(NEW_ROOT), "switch")
            new_env = (f"PACKAGE_ROOT={NEW_ROOT}\n"
                       f"PACKAGE_INDEX_SHA256={NEW_INDEX}\n").encode("utf-8")
            replace_env(new_env, env_mode, "switch")
            record = {
                "schema": "material-counter.release-activation.v1",
                "status": "SWITCHED_NOT_STARTED",
                "activated_at_unix_ns": time.time_ns(),
                "boot_id": EXPECTED_BOOT,
                "old_root": str(OLD_ROOT), "old_index_sha256": OLD_INDEX,
                "new_root": str(NEW_ROOT), "new_index_sha256": NEW_INDEX,
                "backup_env": str(BACKUP),
                "service": service,
                "camera_run": stopped,
                "package_verification": package,
                "dma_vdma": dma,
                "service_started": False,
                "overlay_downloaded": False,
            }
            record_bytes = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            write_new(ACTIVATION_RECORD, record_bytes, 0o600)
            record_created = True
            fsync_dir(RUN_DIR)
            return record
        except BaseException:
            if record_created:
                ACTIVATION_RECORD.unlink(missing_ok=True)
            rollback_errors = []
            try:
                current_now = os.readlink(CURRENT) if CURRENT.is_symlink() else None
                if current_now == str(NEW_ROOT):
                    replace_current(str(OLD_ROOT), "rollback")
                elif current_now != str(OLD_ROOT):
                    raise RuntimeError(f"current changed unexpectedly during rollback: {current_now}")
            except BaseException as error:
                rollback_errors.append(f"current: {error!r}")
            try:
                env_now = ENV.read_bytes()
                new_env = (f"PACKAGE_ROOT={NEW_ROOT}\n"
                           f"PACKAGE_INDEX_SHA256={NEW_INDEX}\n").encode("utf-8")
                if env_now == new_env:
                    replace_env(old_env, env_mode, "rollback")
                elif env_now != old_env:
                    raise RuntimeError("package.env changed unexpectedly during rollback")
            except BaseException as error:
                rollback_errors.append(f"package.env: {error!r}")
            if rollback_errors:
                raise RuntimeError("namespace rollback incomplete: " + "; ".join(rollback_errors))
            raise
    finally:
        for stream in reversed(locks):
            stream.close()


if __name__ == "__main__":
    result = activate()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
