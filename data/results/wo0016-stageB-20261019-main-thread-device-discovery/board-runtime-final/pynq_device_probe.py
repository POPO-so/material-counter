"""Read-only root/profile PYNQ, CMA, camera, and FPGA readiness check."""
from __future__ import annotations

import json
import gc
import os
from pathlib import Path
import signal
import sys
import threading
import time

PACKAGE_DIR = Path(__file__).resolve().parent
WO15_DIR = PACKAGE_DIR / "wo0015"
if str(WO15_DIR) not in sys.path:
    sys.path.insert(0, str(WO15_DIR))

import board_preflight as preflight  # noqa: E402

CMA_WATERMARK_KIB = 32768
DROP_CACHES_PATH = Path("/proc/sys/vm/drop_caches")
PYNQ_LOAD_TIMEOUT_S = 8.0


def _load_explicit_embedded_device():
    """Load the embedded backend directly, without probing every PYNQ device."""
    import pynq
    from pynq.pl_server.embedded_device import EmbeddedDevice

    device = EmbeddedDevice()
    # Overlay and allocation APIs that omit a device will use this explicit
    # instance. Setting the property does not access Device.devices.
    pynq.Device.active_device = device
    return pynq, device


def load_pynq_context(timeout_s: float = PYNQ_LOAD_TIMEOUT_S, *, loader=None):
    """Bound PYNQ setup and return its duration without scanning all devices."""
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) \
            or timeout_s <= 0:
        raise ValueError("timeout_s must be a positive number")
    load = loader or _load_explicit_embedded_device
    started = time.perf_counter()

    # XRT/PYNQ device construction runs in the main thread. Some native XRT
    # calls do not make progress when invoked from a background Python thread.
    # SIGALRM still gives the board process a catchable deadline; the shell
    # timeout remains the hard stop if native code ignores the signal.
    if loader is None and threading.current_thread() is threading.main_thread() \
            and hasattr(signal, "setitimer") and hasattr(signal, "ITIMER_REAL"):
        old_handler = signal.getsignal(signal.SIGALRM)
        old_timer = signal.getitimer(signal.ITIMER_REAL)

        def deadline_handler(_signum, _frame):
            raise TimeoutError(
                f"PYNQ import/device setup exceeded {timeout_s:.2f} seconds"
            )

        signal.signal(signal.SIGALRM, deadline_handler)
        signal.setitimer(signal.ITIMER_REAL, float(timeout_s))
        try:
            pynq, device = load()
        finally:
            elapsed_s = time.perf_counter() - started
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, old_handler)
            if old_timer[0] > 0:
                signal.setitimer(
                    signal.ITIMER_REAL,
                    max(0.001, old_timer[0] - elapsed_s),
                    old_timer[1],
                )
        return pynq, device, (time.perf_counter() - started) * 1000.0

    result: list[tuple[object, object]] = []
    errors: list[BaseException] = []

    def invoke() -> None:
        try:
            result.append(load())
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=invoke, name="wo0016-pynq-load", daemon=True)
    worker.start()
    worker.join(timeout=float(timeout_s))
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if worker.is_alive():
        raise TimeoutError(f"PYNQ import/device setup exceeded {timeout_s:.2f} seconds")
    if errors:
        raise errors[0]
    if not result or len(result[0]) != 2:
        raise RuntimeError("PYNQ context loader returned an invalid result")
    pynq, device = result[0]
    return pynq, device, elapsed_ms


def _drop_kernel_caches() -> None:
    """Drop clean page cache and reclaimable slab once; never targets live CMA buffers."""
    DROP_CACHES_PATH.write_text("3\n", encoding="ascii")


def recover_cma_if_needed(expected_boot_id: str, *, read_cma=None, drop_caches=None,
                          collect_garbage=None, audit_owners=None) -> dict[str, object]:
    """Collect local Python garbage, then make one guarded kernel-cache drop if CMA is low."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise preflight.PreflightError("CMA recovery check must run as root")
    if expected_boot_id != preflight.AUTHORIZED_BOOT_ID:
        raise preflight.PreflightError("requested boot_id differs from the authorized manifest boot")
    boot_id = preflight.read_boot_id()
    if boot_id != expected_boot_id:
        raise preflight.PreflightError("live boot_id differs from the authorized Phase 1.6 boot")

    read_cma = read_cma or preflight.read_cma_meminfo
    drop_caches = drop_caches or _drop_kernel_caches
    collect_garbage = collect_garbage or gc.collect
    audit_owners = audit_owners or preflight.audit_process_ownership
    gc_reclaimed = collect_garbage()
    before = read_cma()
    before_kib = before["CmaFree"]
    result: dict[str, object] = {
        "status": "PASS" if before_kib >= CMA_WATERMARK_KIB else "PENDING_CACHE_DROP",
        "boot_id": boot_id,
        "cma_free_before_kib": before_kib,
        "minimum_kib": CMA_WATERMARK_KIB,
        "gc_objects_collected": gc_reclaimed,
        "cache_drop_attempted": False,
        "process_ownership": None,
    }
    if before_kib >= CMA_WATERMARK_KIB:
        result["cma_free_after_kib"] = before_kib
        return result

    try:
        ownership = audit_owners()
    except Exception as exc:
        return result | {
            "status": "BLOCKED_OWNER_AUDIT_FAILED",
            "owner_audit_error": f"{type(exc).__name__}: {exc}",
        }
    result["process_ownership"] = ownership
    if ownership.get("findings") or ownership.get("denied"):
        return result | {"status": "BLOCKED_OWNER_PRESENT"}
    if preflight.read_boot_id() != expected_boot_id:
        raise preflight.PreflightError("boot_id changed before the kernel cache drop")

    result["cache_drop_attempted"] = True
    try:
        drop_caches()
    except Exception as exc:
        return result | {
            "status": "BLOCKED_CACHE_DROP_FAILED",
            "cache_drop_error": f"{type(exc).__name__}: {exc}",
        }
    try:
        after = read_cma()
        after_kib = after["CmaFree"]
    except Exception as exc:
        return result | {
            "status": "BLOCKED_CMA_READ_FAILED",
            "cache_drop_error": f"{type(exc).__name__}: {exc}",
        }
    result["cma_free_after_kib"] = after_kib
    result["status"] = "PASS" if after_kib >= CMA_WATERMARK_KIB else "BLOCKED_CMA_LOW"
    return result


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
        _pynq, device, load_elapsed_ms = load_pynq_context(
            timeout_s=PYNQ_LOAD_TIMEOUT_S
        )
    except TimeoutError as exc:
        raise preflight.PreflightError(f"PYNQ load timed out: {exc}") from exc
    except Exception as exc:
        raise preflight.PreflightError(
            f"explicit embedded PYNQ device setup failed: {exc}"
        ) from exc
    return {
        "status": "READ_ONLY_PRECHECK_PASS",
        "boot_id": boot_id,
        "cma_free_kib": cma_free,
        "fpga_manager": fpga_manager,
        "pynq_context": {
            "status": "PASS",
            "backend": str(getattr(device, "name", type(device).__name__)),
            "load_elapsed_ms": load_elapsed_ms,
            "device_enumeration": "SKIPPED",
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
        elif len(argv) == 2 and argv[0] == "--recover-cma":
            result = recover_cma_if_needed(argv[1])
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
    return 0 if result.get("status") == "PASS" or result.get("status") == "READ_ONLY_PRECHECK_PASS" \
        or result.get("status") == "READ_ONLY_DIAGNOSTICS_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
