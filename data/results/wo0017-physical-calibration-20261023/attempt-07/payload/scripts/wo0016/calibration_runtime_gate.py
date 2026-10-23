"""Fail-closed, hardware-free interpreter/dependency audit for calibration startup."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
from typing import Mapping


DEFAULT_WORKER_PYTHON = "/usr/local/share/pynq-venv/bin/python3"
RUNTIME_AUDIT_TIMEOUT_S = 60

_RUNTIME_AUDIT_PROGRAM = r'''import json
import os
import sys

try:
    expected = os.path.abspath(sys.argv[1])
    if os.path.abspath(sys.executable) != expected:
        raise RuntimeError("actual interpreter does not match requested worker executable")

    import pynq
    import pydantic
    from types import SimpleNamespace
    from board_once import PynqBoardAPI

    api = PynqBoardAPI(SimpleNamespace(), stage=None)
    audit = api.audit_environment(
        require_empty_shutdown=False,
        require_loaded_bit=False,
    )
    if not isinstance(audit, dict):
        raise RuntimeError("audit_environment did not return an object")
    if api.board_accessed or api._overlay is not None or api._mmio:
        raise RuntimeError("hardware-free audit unexpectedly accessed board devices")

    print(json.dumps({
        "status": "WORKER_ENVIRONMENT_AUDIT_PASS",
        "interpreter": sys.executable,
        "python_version": sys.version.split()[0],
        "pynq_file": pynq.__file__,
        "pynq_version": getattr(pynq, "__version__", "unknown"),
        "pydantic_file": pydantic.__file__,
        "pydantic_version": getattr(pydantic, "__version__", "unknown"),
        "audit_environment": {
            "boot_id": audit.get("boot_id"),
            "fpga_manager": audit.get("fpga_manager"),
            "pynq_global_state": audit.get("pynq_state", {}).get("pl_state_status"),
            "process_findings": len(audit.get("process_ownership", {}).get("findings", [])),
            "process_denied": len(audit.get("process_ownership", {}).get("denied", [])),
            "board_accessed": api.board_accessed,
            "overlay_loaded": api._overlay is not None,
            "mmio_opened": bool(api._mmio),
        },
    }, sort_keys=True))
except Exception as exc:
    print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(2)
'''


def build_worker_environment(script_dir: str | Path, wo15_dir: str | Path, *,
                             base_environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the exact XRT/Python environment used by calibration workers."""
    env = dict(os.environ if base_environment is None else base_environment)
    env["XILINX_XRT"] = "/usr"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    python_paths = [str(Path(script_dir)), str(Path(wo15_dir)), "/home/xilinx"]
    python_paths.extend(env.get("PYTHONPATH", "").split(os.pathsep))
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(item for item in python_paths if item))
    return env


def verify_worker_environment(worker_python: str | Path = DEFAULT_WORKER_PYTHON, *,
                              script_dir: str | Path,
                              wo15_dir: str | Path,
                              base_environment: Mapping[str, str] | None = None,
                              timeout_s: float = RUNTIME_AUDIT_TIMEOUT_S) -> dict[str, object]:
    """Run imports and PYNQ's read-only global environment audit before any gate."""
    if not isinstance(worker_python, (str, os.PathLike)):
        raise ValueError("worker Python executable must be an absolute path")
    executable = str(worker_python)
    if (not executable
            or not (PurePosixPath(executable).is_absolute()
                    or PureWindowsPath(executable).is_absolute())):
        raise ValueError("worker Python executable must be an absolute path")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise ValueError("worker environment audit timeout must be positive")
    command = [executable, "-c", _RUNTIME_AUDIT_PROGRAM, executable]
    try:
        completed = subprocess.run(
            command,
            cwd=str(Path(script_dir)),
            env=build_worker_environment(script_dir, wo15_dir,
                                         base_environment=base_environment),
            capture_output=True,
            text=True,
            timeout=float(timeout_s),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"worker environment audit could not run: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "no diagnostic output").strip()
        raise RuntimeError(
            f"worker environment audit failed with exit code {completed.returncode}: {detail}"
        )
    try:
        report = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("worker environment audit returned invalid JSON") from exc
    if not isinstance(report, dict) or report.get("status") != "WORKER_ENVIRONMENT_AUDIT_PASS":
        raise RuntimeError("worker environment audit did not report PASS")
    if os.path.abspath(str(report.get("interpreter", ""))) != os.path.abspath(executable):
        raise RuntimeError("worker environment audit used a different interpreter")
    audit = report.get("audit_environment")
    if not isinstance(audit, dict) or audit.get("board_accessed") is not False:
        raise RuntimeError("worker environment audit did not remain hardware-free")
    if audit.get("overlay_loaded") is not False or audit.get("mmio_opened") is not False:
        raise RuntimeError("worker environment audit loaded an Overlay or opened MMIO")
    for module_path_key in ("pynq_file", "pydantic_file"):
        if not isinstance(report.get(module_path_key), str) or not report[module_path_key]:
            raise RuntimeError(f"worker environment audit omitted {module_path_key}")
    return report
