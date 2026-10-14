"""Regression tests for the cold-boot PYNQ GlobalState boundary."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import child
import core

spec = importlib.util.spec_from_file_location(
    "cold_global_state_artifacts", HERE.parent / "test_pynq_image_once.py")
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)

BOOT = "cold-boot-test-id"


@pytest.fixture
def board_env(tmp_path, monkeypatch):
    proc_root = tmp_path / "proc"
    sys_root = tmp_path / "sys"
    pynq_root = tmp_path / "site-packages" / "pynq"
    state_file = pynq_root / "pl_server" / "global_pl_state_.json"
    bit_path = tmp_path / "existing.bit"
    bit_path.write_bytes(b"previously-loaded-bitstream")

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    write(proc_root / "sys/kernel/random/boot_id", BOOT + "\n")
    write(proc_root / "uptime", "125.00 100.00\n")
    write(proc_root / "iomem", "00000000-1fffffff : System RAM\n")
    write(sys_root / "class/fpga_manager/fpga0/state", "operating\n")
    write(sys_root / "class/fpga_manager/fpga0/flags", "0\n")
    (pynq_root / "pl_server").mkdir(parents=True, exist_ok=True)
    (pynq_root / "__init__.py").touch()

    real_path = Path

    def mapped_path(value):
        raw = str(value)
        if raw == "/proc" or raw.startswith("/proc/"):
            return proc_root / raw.removeprefix("/proc/")
        if raw == "/sys" or raw.startswith("/sys/"):
            return sys_root / raw.removeprefix("/sys/")
        return real_path(value)

    monkeypatch.setattr(child, "Path", mapped_path)
    monkeypatch.setattr(child.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(child, "process_audit", lambda: {"findings": [], "denied": []})

    api = object.__new__(child.BoardAPI)
    api.pynq = SimpleNamespace(__file__=str(pynq_root / "__init__.py"), __version__="3.1.1")
    return SimpleNamespace(
        api=api,
        bit_path=bit_path,
        state_file=state_file,
        manager_state=sys_root / "class/fpga_manager/fpga0/state",
        manager_flags=sys_root / "class/fpga_manager/fpga0/flags",
        write=write,
    )


def save_global_state(board_env, *, shutdown_ips=None, ip_dict=None, include_shutdown=True):
    content = {
        "bitfile_name": str(board_env.bit_path),
        "bitfile_hash": hashlib.sha1(board_env.bit_path.read_bytes()).hexdigest(),
        "ip_dict": {} if ip_dict is None else ip_dict,
    }
    if include_shutdown:
        content["shutdown_ips"] = {} if shutdown_ips is None else shutdown_ips
    board_env.write(board_env.state_file, json.dumps(content))


def make_worker(tmp_path, board_env, mode="overlay-only", *, receipts=False):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    plan = core.base.validate_inputs(*artifacts.make_artifacts(artifact_dir))
    api = artifacts.FakeApi(plan.packet)
    journal = core.Journal(tmp_path / "guard")
    if receipts:
        for name, receipt_mode in (
                ("overlay-receipt", "overlay-only"),
                ("mmio-receipt", "mmio-read-only")):
            if name == "mmio-receipt" and mode != "dma-once":
                continue
            (journal.root / (name + ".json")).write_text(json.dumps({
                "schema": 1,
                "mode": receipt_mode,
                "boot_id": BOOT,
                "bit_sha256": plan.bit_sha256,
            }), encoding="utf-8")
    worker = core.Worker(plan, api, journal, board_env.api.environment,
                         fixed_identity=False, timeout_s=2.0, mode=mode)
    return worker, api, plan, journal


def test_missing_global_state_is_a_clean_cold_boot_environment(board_env):
    report = board_env.api.environment()

    assert report["boot_id"] == BOOT
    assert report["fpga_state"] == "operating"
    assert report["pl_state_status"] == "COLD_BOOT_NO_GLOBAL_STATE"
    assert report["effective_shutdown_ips"] == {}
    assert report["loaded_bit_sha256"] is None


def test_present_empty_global_state_keeps_loaded_overlay_identity(board_env):
    save_global_state(board_env, ip_dict={"checker": {"phys_addr": 0x43C20000}})

    report = board_env.api.environment()

    assert report["pl_state_status"] == "GLOBAL_STATE_PRESENT"
    assert report["effective_shutdown_ips"] == {}
    assert report["loaded_bit_sha256"] == hashlib.sha256(
        board_env.bit_path.read_bytes()).hexdigest()


@pytest.mark.parametrize("payload", [
    "{broken-json",
    json.dumps({"bitfile_name": "old.bit", "bitfile_hash": "0" * 40}),
    json.dumps({"shutdown_ips": [{"name": "old_ip"}]}),
    json.dumps({"shutdown_ips": {"old_ip": {"phys_addr": 0x43C00000}}}),
    "[]",
])
def test_existing_malformed_or_unsafe_global_state_fails_closed(board_env, payload):
    board_env.write(board_env.state_file, payload)

    with pytest.raises(core.base.PreflightError):
        board_env.api.environment()


def test_present_global_state_still_rejects_shutdown_ip_in_ip_dict(board_env):
    save_global_state(board_env, ip_dict={"shutdown_manager": {"phys_addr": 0x43C00000}})

    with pytest.raises(core.base.PreflightError):
        board_env.api.environment()


@pytest.mark.parametrize(("field", "value"), [
    ("state", "unknown"),
    ("flags", "1"),
    ("flags", "invalid"),
])
def test_bad_fpga_manager_state_or_flags_fails_closed(board_env, field, value):
    target = board_env.manager_state if field == "state" else board_env.manager_flags
    board_env.write(target, value + "\n")

    with pytest.raises(core.base.PreflightError):
        board_env.api.environment()


def test_global_state_permission_error_is_not_treated_as_missing(board_env, monkeypatch):
    save_global_state(board_env)
    path_type = type(board_env.state_file)
    original_read_text = path_type.read_text

    def denied(path, *args, **kwargs):
        if path == board_env.state_file:
            raise PermissionError("state file access denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "read_text", denied)

    with pytest.raises(core.base.PreflightError):
        board_env.api.environment()


def test_worker_accepts_cold_boot_environment_without_terminal_failure(
        tmp_path, board_env):
    worker, _, _, journal = make_worker(tmp_path, board_env)

    result = worker.command("ENV")

    assert result["ok"]
    assert worker.env["pl_state_status"] == "COLD_BOOT_NO_GLOBAL_STATE"
    assert not journal.exists("terminal-fail")


def test_overlay_health_does_not_issue_receipt_while_global_state_stays_absent(
        tmp_path, board_env):
    worker, api, _, journal = make_worker(tmp_path, board_env)
    assert worker.command("ENV")["ok"]
    assert worker.command("LOAD")["ok"]

    result = worker.command("HEALTH", {
        "ping": True, "ssh": True, "linux": True, "boot_id": BOOT,
    })

    assert not result["ok"]
    assert len(api.overlay_calls) == 1
    assert not api.mmios and not api.buffers
    assert not journal.exists("overlay-receipt")


@pytest.mark.parametrize("mode", ["mmio-read-only", "dma-once"])
def test_follow_on_hardware_stages_reject_missing_loaded_hash(
        tmp_path, board_env, mode):
    worker, api, _, journal = make_worker(
        tmp_path, board_env, mode, receipts=True)

    result = worker.command("ENV")

    assert not result["ok"]
    assert "missing or mismatched" in result["error"]
    assert not api.mmios and not api.buffers
    assert not journal.exists("mmio-read-only-consumed")
    assert not journal.exists("dma-once-consumed")
