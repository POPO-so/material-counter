import sys
import types
import builtins
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wo0015"))

import pynq_device_probe as probe


def _install_readonly_probe_gates(monkeypatch):
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(probe.preflight, "read_boot_id",
                        lambda: probe.preflight.AUTHORIZED_BOOT_ID)
    monkeypatch.setattr(probe.preflight, "read_cma_meminfo",
                        lambda: {"CmaTotal": 131072, "CmaFree": 71684})
    monkeypatch.setattr(probe.preflight, "read_fpga_manager",
                        lambda: {"state": "operating", "flags": "0"})
    monkeypatch.setattr(probe.preflight, "audit_process_ownership",
                        lambda: {"findings": [], "denied": []})
    monkeypatch.setattr(probe.preflight, "read_ov9281_gate",
                        lambda device: {"status": "PASS", "device": device})


def test_readonly_board_probe_uses_fpga_manager_without_global_device_enumeration(monkeypatch):
    _install_readonly_probe_gates(monkeypatch)

    class NoEnumerationMeta(type):
        @property
        def devices(cls):
            pytest.fail("readiness must not touch the blocking Device.devices probe")

    class FakeDevice(metaclass=NoEnumerationMeta):
        pass

    fake_pynq = types.ModuleType("pynq")
    fake_pynq.Device = FakeDevice
    monkeypatch.setitem(sys.modules, "pynq", fake_pynq)
    explicit_device = types.SimpleNamespace(name="embedded_xrt0")
    monkeypatch.setattr(
        probe,
        "load_pynq_context",
        lambda timeout_s=12.0: (fake_pynq, explicit_device, 4.25),
        raising=False,
    )

    result = probe.collect(probe.preflight.AUTHORIZED_BOOT_ID)

    assert result["status"] == "READ_ONLY_PRECHECK_PASS"
    assert result["boot_id"] == probe.preflight.AUTHORIZED_BOOT_ID
    assert result["cma_free_kib"] == 71684
    assert result["fpga_manager"] == {"state": "operating", "flags": "0"}
    assert result["pynq_context"] == {
        "status": "PASS",
        "backend": "embedded_xrt0",
        "load_elapsed_ms": 4.25,
        "device_enumeration": "SKIPPED",
    }
    assert result["camera_gate"]["status"] == "PASS"
    assert result["board_accessed"] is False


def test_readonly_board_probe_reports_bounded_pynq_load_timeout_without_enumerating(monkeypatch):
    _install_readonly_probe_gates(monkeypatch)
    imported = []

    class TimeoutEnumerationMeta(type):
        @property
        def devices(cls):
            raise TimeoutError("PYNQ import exceeded 12 seconds")

    class FakeDevice(metaclass=TimeoutEnumerationMeta):
        pass

    fake_pynq = types.ModuleType("pynq")
    fake_pynq.Device = FakeDevice
    monkeypatch.setitem(sys.modules, "pynq", fake_pynq)

    def timeout(**kwargs):
        imported.append(kwargs)
        raise TimeoutError("PYNQ import exceeded 12 seconds")

    monkeypatch.setattr(probe, "load_pynq_context", timeout, raising=False)

    with pytest.raises(probe.preflight.PreflightError, match="PYNQ load timed out"):
        probe.collect(probe.preflight.AUTHORIZED_BOOT_ID)

    assert imported == [{"timeout_s": probe.PYNQ_LOAD_TIMEOUT_S}]


def test_pynq_context_loader_enforces_deadline_with_daemon_worker():
    import threading

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    daemon_flags = []

    def blocked_loader():
        daemon_flags.append(threading.current_thread().daemon)
        entered.set()
        release.wait(timeout=1.0)
        finished.set()
        return types.SimpleNamespace(), types.SimpleNamespace(name="embedded_xrt0")

    with pytest.raises(TimeoutError, match="exceeded 0.01 seconds"):
        probe.load_pynq_context(timeout_s=0.01, loader=blocked_loader)

    assert entered.is_set()
    assert daemon_flags == [True]
    release.set()
    assert finished.wait(timeout=1.0)


def test_default_pynq_loader_uses_bounded_main_thread_without_device_scan(monkeypatch):
    events = []
    current_handler = {"value": object()}

    class FakeSignal:
        SIGALRM = 14
        ITIMER_REAL = 0

        @staticmethod
        def getsignal(sig):
            assert sig == FakeSignal.SIGALRM
            return current_handler["value"]

        @staticmethod
        def signal(sig, handler):
            assert sig == FakeSignal.SIGALRM
            previous = current_handler["value"]
            current_handler["value"] = handler
            return previous

        @staticmethod
        def getitimer(kind):
            assert kind == FakeSignal.ITIMER_REAL
            return 0.0, 0.0

        @staticmethod
        def setitimer(kind, delay, interval=0.0):
            assert kind == FakeSignal.ITIMER_REAL
            events.append((delay, interval))
            return 0.0, 0.0

    fake_pynq = types.ModuleType("pynq")
    device = types.SimpleNamespace(name="embedded_xrt0")

    def main_thread_loader():
        import threading
        events.append(threading.current_thread() is threading.main_thread())
        return fake_pynq, device

    monkeypatch.setattr(probe, "signal", FakeSignal, raising=False)
    monkeypatch.setattr(probe, "_load_explicit_embedded_device", main_thread_loader)

    loaded_pynq, loaded_device, elapsed_ms = probe.load_pynq_context(timeout_s=0.25)

    assert loaded_pynq is fake_pynq
    assert loaded_device is device
    assert elapsed_ms >= 0
    assert events == [(0.25, 0.0), True, (0.0, 0.0)]


def test_readonly_board_probe_fails_closed_when_explicit_pynq_setup_fails(monkeypatch):
    _install_readonly_probe_gates(monkeypatch)
    monkeypatch.setattr(
        probe, "load_pynq_context",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("EmbeddedDevice unavailable")),
        raising=False,
    )

    with pytest.raises(probe.preflight.PreflightError, match="explicit embedded PYNQ device setup failed"):
        probe.collect(probe.preflight.AUTHORIZED_BOOT_ID)


def test_readonly_board_probe_stops_on_boot_mismatch_before_pynq_import(monkeypatch):
    _install_readonly_probe_gates(monkeypatch)
    monkeypatch.setattr(probe.preflight, "read_boot_id", lambda: "different-boot")

    with pytest.raises(probe.preflight.PreflightError, match="live boot_id"):
        probe.collect(probe.preflight.AUTHORIZED_BOOT_ID)


def test_readonly_diagnostic_reports_low_cma_and_process_camera_gates_without_pynq(monkeypatch):
    _install_readonly_probe_gates(monkeypatch)
    monkeypatch.setattr(probe.preflight, "read_cma_meminfo",
                        lambda: {"CmaTotal": 131072, "CmaFree": 32320})
    imported = []
    original_import = builtins.__import__

    def track_import(name, *args, **kwargs):
        imported.append(name)
        if name == "pynq":
            raise AssertionError("diagnostic must not import PYNQ")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", track_import)

    result = probe.diagnose(probe.preflight.AUTHORIZED_BOOT_ID)

    assert result["status"] == "READ_ONLY_DIAGNOSTICS_COMPLETE"
    assert result["cma_gate"] == {"status": "BLOCKED", "free_kib": 32320,
                                  "minimum_kib": 32768, "total_kib": 131072}
    assert result["process_ownership"]["evidence"]["findings"] == []
    assert result["camera_gate"]["status"] == "PASS"
    assert result["pynq_enumeration"] == "NOT_RUN_BY_DIAGNOSTIC"
    assert "pynq" not in imported


def test_readonly_diagnostic_refuses_wrong_boot_before_scanning_processes(monkeypatch):
    _install_readonly_probe_gates(monkeypatch)
    monkeypatch.setattr(probe.preflight, "read_boot_id", lambda: "different-boot")
    monkeypatch.setattr(probe.preflight, "audit_process_ownership",
                        lambda: pytest.fail("wrong boot must stop before process scan"))

    with pytest.raises(probe.preflight.PreflightError, match="live boot_id"):
        probe.diagnose(probe.preflight.AUTHORIZED_BOOT_ID)


def test_cma_recovery_skips_drop_caches_when_watermark_is_already_met(monkeypatch):
    _install_readonly_probe_gates(monkeypatch)
    events = []

    result = probe.recover_cma_if_needed(
        probe.preflight.AUTHORIZED_BOOT_ID,
        read_cma=lambda: {"CmaTotal": 131072, "CmaFree": 71684},
        drop_caches=lambda: events.append("drop"),
        collect_garbage=lambda: events.append("gc") or 0,
        audit_owners=lambda: {"findings": [], "denied": []},
    )

    assert result["status"] == "PASS"
    assert result["cache_drop_attempted"] is False
    assert result["cma_free_before_kib"] == result["cma_free_after_kib"] == 71684
    assert events == ["gc"]


def test_cma_recovery_drops_caches_once_only_after_owner_audit(monkeypatch):
    _install_readonly_probe_gates(monkeypatch)
    samples = iter((
        {"CmaTotal": 131072, "CmaFree": 12000},
        {"CmaTotal": 131072, "CmaFree": 70000},
    ))
    events = []

    result = probe.recover_cma_if_needed(
        probe.preflight.AUTHORIZED_BOOT_ID,
        read_cma=lambda: next(samples),
        drop_caches=lambda: events.append("drop"),
        collect_garbage=lambda: events.append("gc") or 3,
        audit_owners=lambda: events.append("audit") or {"findings": [], "denied": []},
    )

    assert result["status"] == "PASS"
    assert result["cache_drop_attempted"] is True
    assert result["cma_free_before_kib"] == 12000
    assert result["cma_free_after_kib"] == 70000
    assert result["gc_objects_collected"] == 3
    assert events == ["gc", "audit", "drop"]


def test_cma_recovery_refuses_cache_drop_when_fpga_owner_is_present(monkeypatch):
    _install_readonly_probe_gates(monkeypatch)
    events = []

    result = probe.recover_cma_if_needed(
        probe.preflight.AUTHORIZED_BOOT_ID,
        read_cma=lambda: {"CmaTotal": 131072, "CmaFree": 12000},
        drop_caches=lambda: events.append("drop"),
        collect_garbage=lambda: 1,
        audit_owners=lambda: {"findings": [{"pid": 99}], "denied": []},
    )

    assert result["status"] == "BLOCKED_OWNER_PRESENT"
    assert result["cache_drop_attempted"] is False
    assert events == []
