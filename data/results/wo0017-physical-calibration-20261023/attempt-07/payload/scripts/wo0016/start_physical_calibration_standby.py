"""Start the physical-calibration operator gate with the pinned PYNQ Python."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request

from calibration_runtime_gate import DEFAULT_WORKER_PYTHON, build_worker_environment


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start one loopback calibration standby worker")
    parser.add_argument("--attempt-dir", type=Path, required=True)
    parser.add_argument("--worker-python", default=DEFAULT_WORKER_PYTHON)
    parser.add_argument("--control-port", type=int, default=8081)
    parser.add_argument("--handoff-timeout-s", type=float, default=3600)
    parser.add_argument("--ready-timeout-s", type=float, default=90)
    parser.add_argument("runner_args", nargs=argparse.REMAINDER)
    return parser


def build_operator_gate_command(*, worker_python: str, launcher: str | Path,
                                attempt_dir: str | Path, control_port: int,
                                handoff_timeout_s: float,
                                runner_args: list[str]) -> list[str]:
    if not worker_python.startswith("/"):
        raise ValueError("worker Python must be an absolute PYNQ virtualenv path")
    if isinstance(control_port, bool) or not 1 <= control_port <= 65535:
        raise ValueError("control port must be in 1..65535")
    if not runner_args or "--physical-calibration" not in runner_args:
        raise ValueError("standby worker requires physical-calibration runner arguments")
    if "--worker-python" in runner_args:
        index = runner_args.index("--worker-python")
        if index + 1 >= len(runner_args) or runner_args[index + 1] != worker_python:
            raise ValueError("runner worker Python differs from the standby executable")
    else:
        runner_args = [*runner_args, "--worker-python", worker_python]
    return [
        worker_python, str(Path(launcher)),
        "--attempt-dir", str(Path(attempt_dir)),
        "--control-port", str(control_port),
        "--host", "127.0.0.1",
        "--handoff-timeout-s", str(handoff_timeout_s),
        "--standby-before-preflight", "--", *runner_args,
    ]


def _read_status(port: int) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=1) as response:
            value = json.loads(response.read())
        return value if isinstance(value, dict) else None
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runner_args = args.runner_args[1:] if args.runner_args[:1] == ["--"] else args.runner_args
    attempt_dir = args.attempt_dir.expanduser().resolve(strict=True)
    if not attempt_dir.is_dir():
        raise ValueError("attempt directory must already exist")
    if args.handoff_timeout_s <= 0 or args.ready_timeout_s <= 0:
        raise ValueError("standby timeout values must be positive")
    script_dir = Path(__file__).resolve().parent
    wo15_dir = script_dir / "wo0015"
    if not (wo15_dir / "board_once.py").is_file():
        wo15_dir = script_dir.parent / "wo0015"
    launcher = script_dir / "operator_gate_launcher.py"
    log_path = attempt_dir / "operator-gate.log"
    pid_path = attempt_dir / "operator-gate.pid"
    for path in (log_path, pid_path):
        if path.exists() or path.is_symlink():
            raise RuntimeError(f"refusing to reuse existing standby artifact: {path}")
    command = build_operator_gate_command(
        worker_python=args.worker_python,
        launcher=launcher,
        attempt_dir=attempt_dir,
        control_port=args.control_port,
        handoff_timeout_s=args.handoff_timeout_s,
        runner_args=runner_args,
    )
    env = build_worker_environment(script_dir, wo15_dir)
    previous_umask = os.umask(0o077)
    try:
        with log_path.open("xb") as log:
            process = subprocess.Popen(
                command,
                cwd=str(script_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        with pid_path.open("x", encoding="ascii") as pid_file:
            pid_file.write(f"{process.pid}\n")
    finally:
        os.umask(previous_umask)

    deadline = time.monotonic() + args.ready_timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"standby worker exited before READY; inspect {log_path}")
        state = _read_status(args.control_port)
        if state and state.get("phase") == "READY":
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"One-time operator passcode: ([A-Za-z0-9_-]+)", log_text)
            if not match:
                raise RuntimeError("standby reached READY without issuing its one-time passcode")
            print(json.dumps({
                "status": "CALIBRATION_STANDBY_READY",
                "pid": process.pid,
                "attempt_dir": str(attempt_dir),
                "worker_python": args.worker_python,
                "control_url": f"http://127.0.0.1:{args.control_port}/",
                "phase": state["phase"],
                "one_time_passcode": match.group(1),
            }, ensure_ascii=False, sort_keys=True))
            return 0
        time.sleep(0.2)
    raise TimeoutError(
        f"standby worker did not reach READY within {args.ready_timeout_s}s; inspect {log_path}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
