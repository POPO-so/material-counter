"""Read-only root/profile PYNQ, CMA, camera, and FPGA readiness check."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

PACKAGE_DIR = Path(__file__).resolve().parent
WO15_DIR = PACKAGE_DIR / "wo0015"
if str(WO15_DIR) not in sys.path:
    sys.path.insert(0, str(WO15_DIR))

import board_preflight as preflight  # noqa: E402


def diagnose(expected_boot_id: str) -> dict[str, object]:
    """Collect root-only readiness telemetry without importing PYNQ or mutating state."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise preflight.PreflightError("board diagnostic must run as root")
    if expected_boot_id != preflight.AUTHORIZED_BOOT_ID:
        raise preflight.PreflightError("requested boot_id differs from the authorized manifest boot")
    boot_id = preflight.read_boot_id()
    if boot_id != expected_boot_id:
        raise preflight.PreflightError("live boot_id differs from the authorized Phase 1.6 boot")

    diagnostics: dict[str, object] = {}

    def capture(name: str, operation) -> None:
        try:
            diagnostics[name] = {"status": "PASS", "evidence": operation()}
        except Exception as exc:
            diagnostics[name] = {
                "status": "FAIL",
                "error": f"{type(exc).__name__}: {exc}",
            }

    meminfo = preflight.read_cma_meminfo()
    cma_free = meminfo["CmaFree"]
    diagnostics["cma_gate"] = {
        "status": "PASS" if cma_free >= 32768 else "BLOCKED",
        "free_kib": cma_free,
        "minimum_kib": 32768,
        "total_kib": meminfo["CmaTotal"],
    }
    capture("fpga_manager", preflight.read_fpga_manager)
    capture("process_ownership", preflight.audit_process_ownership)
    capture("camera_gate", lambda: preflight.read_ov9281_gate("/dev/video0"))
    return {
        "status": "READ_ONLY_DIAGNOSTICS_COMPLETE",
        "boot_id": boot_id,
        "cma_gate": diagnostics["cma_gate"],
        "fpga_manager": diagnostics["fpga_manager"],
        "process_ownership": diagnostics["process_ownership"],
        "camera_gate": diagnostics["camera_gate"],
        "pynq_enumeration": "NOT_RUN_BY_DIAGNOSTIC",
        "board_accessed": False,
    }


def collect(expected_boot_id: str) -> dict[str, object]:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise preflight.PreflightError("board precheck must run as root")
    if expected_boot_id != preflight.AUTHORIZED_BOOT_ID:
        raise preflight.PreflightError("requested boot_id differs from the authorized manifest boot")
    boot_id = preflight.read_boot_id()
    if boot_id != expected_boot_id:
        raise preflight.PreflightError("live boot_id differs from the authorized Phase 1.6 boot")

    meminfo = preflight.read_cma_meminfo()
    cma_free = meminfo["CmaFree"]
    if cma_free < 32768:
        raise preflight.PreflightError(
            f"CmaFree={cma_free} KiB is below the 32768 KiB Phase 1.6 watermark"
        )
    fpga_manager = preflight.read_fpga_manager()
    process_ownership = preflight.audit_process_ownership()
    camera = preflight.read_ov9281_gate("/dev/video0")

    try:
        from pynq import Device

        devices = Device.devices
        device_count = len(devices)
    except Exception as exc:
        raise preflight.PreflightError(
            f"cannot enumerate root-visible PYNQ Device.devices: {exc}"
        ) from exc
    if device_count <= 0:
        raise preflight.PreflightError(
            "No PYNQ devices found: len(Device.devices) must be greater than zero"
        )
    return {
        "status": "READ_ONLY_PRECHECK_PASS",
        "boot_id": boot_id,
        "cma_free_kib": cma_free,
        "fpga_manager": fpga_manager,
        "pynq_devices": {
            "count": device_count,
            "names": [str(getattr(device, "name", type(device).__name__))
                      for device in devices],
        },
        "process_ownership": process_ownership,
        "camera_gate": camera,
        "board_accessed": False,
    }


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    try:
        if len(argv) == 2 and argv[0] == "--diagnose":
            result = diagnose(argv[1])
        elif len(argv) == 1:
            result = collect(argv[0])
        else:
            raise preflight.PreflightError("expected <authorized-boot-id> or --diagnose <authorized-boot-id>")
    except BaseException as exc:
        print(json.dumps({
            "status": "READ_ONLY_PRECHECK_FAIL",
            "board_accessed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
