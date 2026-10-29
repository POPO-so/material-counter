"""Validate the pinned G1 bundle and start one bounded camera display run."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import time
import uuid


RUN_DIR = Path("/home/xilinx/standalone-20261029-g1")
MANIFEST = RUN_DIR / "export/manifest-diag-resume.json"
MANIFEST_SHA256 = "685bb871fed4d0c1ed58ff9bb5c079b2a51748c1102c25b86dee5ca8a6e7c8d3"
CAMERA = Path("/dev/v4l/by-id/usb-HSK_USB_Camera_20210616103-video-index0")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
CAMERA_WAIT_SECONDS = 120


def _current_boot_id() -> str:
    raw = BOOT_ID_PATH.read_text(encoding="ascii").strip()
    return str(uuid.UUID(raw))


def _wait_for_camera() -> None:
    deadline = time.monotonic() + CAMERA_WAIT_SECONDS
    next_log = 0.0
    while True:
        if CAMERA.exists():
            return
        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError(
                f"camera device did not appear within {CAMERA_WAIT_SECONDS} seconds: {CAMERA}"
            )
        if now >= next_log:
            print(f"Waiting for camera device: {CAMERA}", flush=True)
            next_log = now + 15.0
        time.sleep(min(1.0, deadline - now))


def main() -> None:
    try:
        digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
        if digest != MANIFEST_SHA256:
            raise RuntimeError(
                f"manifest SHA256 mismatch: expected {MANIFEST_SHA256}, got {digest}"
            )

        boot_id = _current_boot_id()
        _wait_for_camera()

        output_dir = RUN_DIR / "runs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"screen-{boot_id}.json"
        if os.path.lexists(output):
            raise FileExistsError(f"refusing to overwrite existing report: {output}")

        print(f"Starting bounded G1 camera display run for boot {boot_id}", flush=True)
        command = [
            sys.executable,
            "-B",
            "-u",
            "-m",
            "scripts.standalone.g1_run",
            "--execute",
            "--source",
            "camera",
            "--manifest",
            str(MANIFEST),
            "--manifest-sha256",
            MANIFEST_SHA256,
            "--boot-id",
            boot_id,
            "--duration",
            "7200",
            "--fps",
            "20",
            "--output",
            str(output),
        ]
        os.execvpe(sys.executable, command, os.environ.copy())
    except Exception as exc:
        print(f"G1 boot autostart failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
