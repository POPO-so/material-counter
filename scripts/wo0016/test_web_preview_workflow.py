"""Workflow tests for the self-contained remote preview startup payload."""
import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def remote(name="web_preview_start"):
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verified_result(boot="boot-a"):
    return {"status": "GRAY8_PIXEL_VERIFIED", "mode": "self-test", "frames": 3,
            "pixel_verified_frames": 3, "buffers_held": False, "boot_id": boot}


def config_with_hashed_deployment(tmp_path):
    root = tmp_path / "board"
    manifest = root / "export" / "manifest.json"
    bit = root / "export" / "gray8.bit"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b'{"design":"temporary"}')
    bit.write_bytes(b"temporary-bitstream")
    config = {
        "runtime_root": str(root),
        "manifest": "export/manifest.json",
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "bit": "export/gray8.bit",
        "bit_sha256": hashlib.sha256(bit.read_bytes()).hexdigest(),
        "python": "/fake/python3",
        "pynq_init": "/fake/pynq/__init__.py",
        "board_dir": "/fake/board-modules",
        "board_port": 8080,
    }
    return config


class LockFile:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def setup_main_boundaries(monkeypatch, module, config, *, state="COLD_BOOT_NO_GLOBAL_STATE",
                          cma_values=(40000,), boots=("boot-a",), result=None,
                          process_code=0, write_evidence=True):
    calls = {"preflight": [], "commands": [], "cache_writes": [], "launch": []}

    class Preflight:
        @staticmethod
        def audit_process_ownership():
            calls["preflight"].append(("audit",))

        @staticmethod
        def inspect_pynq_global_state(path, **kwargs):
            calls["preflight"].append(("inspect", path, kwargs))
            return {"pl_state_status": state}

        @staticmethod
        def read_cma_meminfo():
            value = cma_values[min(sum(1 for item in calls["preflight"]
                                       if item[0] == "cma"), len(cma_values) - 1)]
            calls["preflight"].append(("cma", value))
            return {"CmaFree": value}

    class Fcntl(types.ModuleType):
        LOCK_EX = 1
        LOCK_NB = 2

        @staticmethod
        def flock(file_obj, flags):
            calls["lock"] = flags

    monkeypatch.setitem(sys.modules, "fcntl", Fcntl("fcntl"))
    monkeypatch.setitem(sys.modules, "board_preflight", Preflight)
    monkeypatch.setattr(module, "open", lambda *args, **kwargs: LockFile(), raising=False)

    boot_values = iter(boots)
    last_boot = [None]
    class FakePath:
        def read_text(self, *args, **kwargs):
            try:
                last_boot[0] = next(boot_values)
            except StopIteration:
                pass
            return last_boot[0]

        def write_text(self, text, *args, **kwargs):
            calls["cache_writes"].append((str(self.path), text))

        def __init__(self, path):
            self.path = str(path)

    original_path = Path
    def path_factory(path):
        value = str(path).replace("\\", "/")
        if value in ("/proc/sys/kernel/random/boot_id", "/proc/sys/vm/drop_caches"):
            return FakePath(value)
        return original_path(path)
    monkeypatch.setattr(module, "Path", path_factory)
    monkeypatch.setattr(module.os, "sync", lambda: calls.setdefault("sync", 0) or calls.__setitem__("sync", calls.get("sync", 0) + 1), raising=False)

    def spawn(command, **kwargs):
        calls["self_test_command"] = list(command)
        calls["self_test_env"] = kwargs["env"]
        calls["commands"].append(("self-test", list(command)))
        if write_evidence:
            output = Path(command[command.index("--output") + 1])
            output.write_text(json.dumps(result if result is not None else verified_result()),
                              encoding="utf-8")
        return types.SimpleNamespace(wait=lambda timeout: process_code)

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs:
                        calls["commands"].append(("camera", list(command), kwargs)) or None)
    monkeypatch.setattr(module, "launch_preview", lambda cfg, outdir, boot:
                        calls["launch"].append((boot, outdir)) or "preview-result")
    return calls


def test_main_cold_boot_loads_overlay_after_hash_preflight_and_self_test(tmp_path, monkeypatch):
    module = remote()
    config = config_with_hashed_deployment(tmp_path)
    outdir = tmp_path / "output"
    outdir.mkdir()
    calls = setup_main_boundaries(monkeypatch, module, config)

    assert module.main(config, outdir) == "preview-result"
    assert calls["self_test_command"][0] == config["python"]
    assert "--load-overlay" in calls["self_test_command"]
    assert calls["preflight"][:2] == [
        ("audit",), ("inspect", config["pynq_init"], {"require_empty_shutdown": True})]
    assert calls["commands"][-1][0] == "camera"
    assert calls["launch"][0][0] == "boot-a"


def test_main_warm_boot_verifies_expected_bit_and_skips_overlay_load(tmp_path, monkeypatch):
    module = remote()
    config = config_with_hashed_deployment(tmp_path)
    outdir = tmp_path / "output"
    outdir.mkdir()
    calls = setup_main_boundaries(monkeypatch, module, config, state="OVERLAY_ALREADY_LOADED")

    module.main(config, outdir)

    inspections = [item for item in calls["preflight"] if item[0] == "inspect"]
    assert inspections == [
        ("inspect", config["pynq_init"], {"require_empty_shutdown": True}),
        ("inspect", config["pynq_init"], {
            "require_empty_shutdown": True, "require_loaded_bit": True,
            "expected_bit_path": Path(config["runtime_root"]) / config["bit"],
            "expected_bit_sha256": config["bit_sha256"],
        }),
    ]
    assert "--load-overlay" not in calls["self_test_command"]
    assert calls["launch"]


def test_main_self_test_failure_does_not_start_camera(tmp_path, monkeypatch):
    module = remote()
    config = config_with_hashed_deployment(tmp_path)
    outdir = tmp_path / "output"
    outdir.mkdir()
    calls = setup_main_boundaries(monkeypatch, module, config, process_code=1)

    with pytest.raises(RuntimeError, match="FPGA 自检失败"):
        module.main(config, outdir)
    assert not any(command[0] == "camera" for command in calls["commands"])
    assert calls["launch"] == []


def test_main_boot_change_after_self_test_does_not_start_camera(tmp_path, monkeypatch):
    module = remote()
    config = config_with_hashed_deployment(tmp_path)
    outdir = tmp_path / "output"
    outdir.mkdir()
    calls = setup_main_boundaries(monkeypatch, module, config, boots=("boot-a", "boot-b"))

    with pytest.raises(RuntimeError, match="开机状态已变化"):
        module.main(config, outdir)
    assert not any(command[0] == "camera" for command in calls["commands"])
    assert calls["launch"] == []


def test_main_recovers_low_cma_then_launches(tmp_path, monkeypatch):
    module = remote()
    config = config_with_hashed_deployment(tmp_path)
    outdir = tmp_path / "output"
    outdir.mkdir()
    calls = setup_main_boundaries(monkeypatch, module, config, cma_values=(32000, 40000))

    module.main(config, outdir)

    assert calls["cache_writes"] == [("/proc/sys/vm/drop_caches", "3\n")]
    assert calls.get("sync") == 1
    assert calls["launch"]


def test_main_rejects_cma_that_remains_low_after_recovery(tmp_path, monkeypatch):
    module = remote()
    config = config_with_hashed_deployment(tmp_path)
    outdir = tmp_path / "output"
    outdir.mkdir()
    calls = setup_main_boundaries(monkeypatch, module, config, cma_values=(32000, 32768))

    with pytest.raises(RuntimeError, match="连续内存不足"):
        module.main(config, outdir)
    assert calls["cache_writes"] == [("/proc/sys/vm/drop_caches", "3\n")]
    assert not any(command[0] == "camera" for command in calls["commands"])
    assert calls["launch"] == []


@pytest.mark.parametrize("code,write_evidence,payload,error", [
    (0, True, None, None),
    (7, True, None, "FPGA 自检失败"),
    (0, False, None, "FPGA 自检失败"),
    (0, True, {"status": "FAIL_HOLD"}, "自检结果不满足启动条件"),
])
def test_run_self_test_executes_process_and_validates_evidence(
        tmp_path, monkeypatch, code, write_evidence, payload, error):
    module = remote()
    config = config_with_hashed_deployment(tmp_path)
    outdir = tmp_path / "output"
    outdir.mkdir()
    calls = setup_main_boundaries(monkeypatch, module, config, process_code=code,
                                  write_evidence=write_evidence, result=payload)

    if error:
        with pytest.raises(RuntimeError, match=error):
            module.run_self_test(config, outdir, "boot-a", load=True)
    else:
        module.run_self_test(config, outdir, "boot-a", load=True)
    assert calls["self_test_command"][0] == config["python"]
    assert "--load-overlay" in calls["self_test_command"]
    assert calls["self_test_command"][calls["self_test_command"].index("--manifest") + 1] == str(
        Path(config["runtime_root"]) / config["manifest"])
