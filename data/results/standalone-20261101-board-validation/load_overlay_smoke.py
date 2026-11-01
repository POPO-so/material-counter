#!/usr/bin/env python3
"""One-shot G2 BIT download smoke; deliberately performs no PL AXI readback."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from scripts.standalone.bundle import sha256, verify_bundle
from scripts.standalone.boot_integrated import BoardBackend
from scripts.standalone.delivery import verify_package
from scripts.standalone.runtime_environment import verify_runtime_environment
from scripts.wo0015.board_preflight import inspect_pynq_global_state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--index-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, output = args.root.resolve(strict=True), args.output
    if os.geteuid() != 0:
        raise RuntimeError("run as root")
    if output.exists():
        raise RuntimeError(f"refusing to overwrite evidence: {output}")

    integrity = verify_package(root, args.index_sha256)
    runtime = verify_runtime_environment(root / "board-runtime.json")
    manifest_path = root / "export/display-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_sha256 = sha256(manifest_path)
    files = verify_bundle(manifest_path, manifest_sha256)

    service = subprocess.run(
        ["systemctl", "show", "material-counter-integrated.service",
         "-p", "ActiveState", "-p", "MainPID"],
        check=True, capture_output=True, text=True,
    ).stdout
    if "ActiveState=inactive" not in service or "MainPID=0" not in service:
        raise RuntimeError(f"service owner is not inactive: {service.strip()}")

    locks = []
    try:
        for lock_path in ("/tmp/material-counter-desktop.lock",
                          "/run/material-standalone.lock"):
            stream = open(lock_path, "a", encoding="utf-8")
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locks.append(stream)

        backend = BoardBackend(root, files, manifest, output.parent)
        ownership = backend.audit()
        backend.configure()

        import pynq
        from pynq import Clocks

        loaded = inspect_pynq_global_state(
            pynq.__file__, require_empty_shutdown=True,
            expected_bit_path=files["bit"],
            expected_bit_sha256=sha256(files["bit"]),
            require_loaded_bit=True, allow_equivalent_bit_path=False,
        )
        clocks = [Clocks.fclk0_mhz, Clocks.fclk1_mhz, Clocks.fclk2_mhz]
        if any(abs(actual - expected) > 0.15
               for actual, expected in zip(clocks, (50.0, 100.0, 142.857))):
            raise RuntimeError(f"clock readback mismatch: {clocks}")

        result = {
            "schema": "material-counter.g2-overlay-load-smoke.v1",
            "status": "BIT_HASH_AND_CLOCKS_PASS",
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "index_sha256": args.index_sha256,
            "manifest_sha256": manifest_sha256,
            "bit_sha256": sha256(files["bit"]),
            "hwh_sha256": sha256(files["hwh"]),
            "integrity": integrity,
            "runtime_compatibility": runtime["status"],
            "process_ownership": ownership,
            "pynq_loaded_state": loaded,
            "fclk_mhz": clocks,
            "pl_register_readback": "not_performed",
            "camera_runner": "not_started",
            "systemd_modified": False,
        }
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    finally:
        for stream in reversed(locks):
            stream.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"overlay smoke failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
