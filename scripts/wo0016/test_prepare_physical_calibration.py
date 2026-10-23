from pathlib import Path
from types import SimpleNamespace
import json
import subprocess

import pytest

import calibration_runtime_gate as runtime_gate
import prepare_physical_calibration as prepare
from start_physical_calibration_standby import build_operator_gate_command


def _runner_args(tmp_path: Path, lock_path: Path) -> list[str]:
    return [
        "--mode", "board",
        "--physical-calibration",
        "--calibration-id", "physical-calibration-20261023-07",
        "--calibration-lock", str(lock_path),
        "--expected-boot-id", "35983d66-bb1d-4a8a-85e5-802170825515",
        "--stage1-receipt", str(tmp_path / "stage1-receipt.json"),
        "--stage1-receipt-sha256", "a" * 64,
        "--manifest", str(tmp_path / "manifest.json"),
        "--manifest-sha256", "b" * 64,
        "--execute",
        "--authorize-stage", "wo0017-physical-calibration",
        "--approve-once",
        "--counting-profile", "full-width-mixed",
        "--max-frames", "300",
        "--evidence-json", str(tmp_path / "evidence.json"),
        "--capture-dir", str(tmp_path / "capture"),
    ]


def test_worker_dependency_gate_failure_prevents_board_admission_and_lock_creation(
        tmp_path, monkeypatch, capsys):
    boot_id = "35983d66-bb1d-4a8a-85e5-802170825515"
    lock_path = tmp_path / boot_id / "physical-calibration-20261023-07" / "calibration-lock.json"
    calls = []

    def reject_worker(*_args, **_kwargs):
        calls.append("worker")
        raise RuntimeError("worker environment audit failed with exit code 2")

    def unexpected_board_admission(*_args, **_kwargs):
        calls.append("board")
        return {
            "calibration": {
                "lock_path": lock_path,
                "identity": {"stage": "physical-calibration", "calibration_id": "physical-calibration-20261023-07"},
            },
            "plan": SimpleNamespace(boot_id=boot_id),
            "meminfo": {"CmaFree": 90_000},
            "camera_profile": {},
            "camera_ownership": {},
            "dma_status": {"mm2s": 1, "s2mm": 1},
        }

    monkeypatch.setenv("XILINX_XRT", "/usr")
    monkeypatch.setattr(prepare, "verify_worker_environment", reject_worker, raising=False)
    monkeypatch.setattr(prepare.runner, "validate_calibration_board_state",
                        unexpected_board_admission)

    result = prepare.main(["--", *_runner_args(tmp_path, lock_path)])

    assert result == 2
    assert calls == ["worker"]
    assert not lock_path.exists()
    assert "CALIBRATION_PREP_REJECTED" in capsys.readouterr().err


def test_worker_environment_gate_uses_the_exact_python_and_hardware_free_audit(
        tmp_path, monkeypatch):
    worker_python = str(tmp_path / "pynq-python")
    report = {
        "status": "WORKER_ENVIRONMENT_AUDIT_PASS",
        "interpreter": worker_python,
        "pynq_file": "/home/xilinx/pynq/__init__.py",
        "pydantic_file": "/venv/site-packages/pydantic/__init__.py",
        "audit_environment": {
            "board_accessed": False,
            "overlay_loaded": False,
            "mmio_opened": False,
        },
    }
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps(report), "")

    monkeypatch.setattr(runtime_gate.subprocess, "run", run)
    result = runtime_gate.verify_worker_environment(
        worker_python,
        script_dir=tmp_path / "wo0016",
        wo15_dir=tmp_path / "wo0015",
        base_environment={"PYTHONPATH": "/existing", "XILINX_XRT": "/wrong"},
    )

    command, kwargs = calls[0]
    assert command[0] == worker_python
    assert command[1] == "-c"
    assert "import pynq" in command[2]
    assert "import pydantic" in command[2]
    assert "api.audit_environment" in command[2]
    assert "require_loaded_bit=False" in command[2]
    assert kwargs["env"]["XILINX_XRT"] == "/usr"
    assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "/existing" in kwargs["env"]["PYTHONPATH"]
    assert kwargs["check"] is False
    assert result == report


@pytest.mark.parametrize("stderr", [
    "ModuleNotFoundError: No module named 'pynq'",
    "ModuleNotFoundError: No module named 'pydantic'",
    "PreflightError: audit_environment failed",
])
def test_worker_environment_gate_rejects_import_or_audit_exit_two(
        tmp_path, monkeypatch, stderr):
    def run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 2, "", stderr)

    monkeypatch.setattr(runtime_gate.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="exit code 2") as error:
        runtime_gate.verify_worker_environment(
            str(tmp_path / "pynq-python"),
            script_dir=tmp_path,
            wo15_dir=tmp_path,
        )
    assert stderr in str(error.value)


def test_worker_environment_gate_rejects_audit_that_touched_board(tmp_path, monkeypatch):
    report = {
        "status": "WORKER_ENVIRONMENT_AUDIT_PASS",
        "interpreter": str(tmp_path / "python"),
        "pynq_file": "/pynq/__init__.py",
        "pydantic_file": "/pydantic/__init__.py",
        "audit_environment": {
            "board_accessed": True,
            "overlay_loaded": False,
            "mmio_opened": False,
        },
    }
    monkeypatch.setattr(runtime_gate.subprocess, "run", lambda command, **_kwargs:
                        subprocess.CompletedProcess(command, 0, json.dumps(report), ""))
    with pytest.raises(RuntimeError, match="hardware-free"):
        runtime_gate.verify_worker_environment(
            str(tmp_path / "python"), script_dir=tmp_path, wo15_dir=tmp_path)


def test_standby_command_starts_operator_gate_with_same_pynq_worker(tmp_path):
    worker_python = runtime_gate.DEFAULT_WORKER_PYTHON
    command = build_operator_gate_command(
        worker_python=worker_python,
        launcher=tmp_path / "operator_gate_launcher.py",
        attempt_dir=tmp_path,
        control_port=8081,
        handoff_timeout_s=3600,
        runner_args=["--mode", "board", "--physical-calibration"],
    )
    assert command[0] == worker_python
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[-2:] == ["--worker-python", worker_python]
    assert "--standby-before-preflight" in command

    with pytest.raises(ValueError, match="differs"):
        build_operator_gate_command(
            worker_python=worker_python,
            launcher=tmp_path / "operator_gate_launcher.py",
            attempt_dir=tmp_path,
            control_port=8081,
            handoff_timeout_s=3600,
            runner_args=["--mode", "board", "--physical-calibration",
                         "--worker-python", "/usr/bin/python3"],
        )
