"""Offline fake tests for JPEG display bring-up and FAIL_HOLD retention."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile

import pytest

RUNNER_PATH = Path(__file__).with_name("run_jpeg_counter.py")
SPEC = importlib.util.spec_from_file_location("run_jpeg_counter_hold_tests", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

BRINGUP_PATH = RUNNER_PATH.parents[1] / "standalone" / "display_bringup.py"
BRINGUP_SPEC = importlib.util.spec_from_file_location("display_bringup_tests", BRINGUP_PATH)
assert BRINGUP_SPEC is not None and BRINGUP_SPEC.loader is not None
bringup = importlib.util.module_from_spec(BRINGUP_SPEC)
BRINGUP_SPEC.loader.exec_module(bringup)


class FakeDisplay:
    def __init__(self, *, start_error=None, stop_error=None, uncertain=False):
        self.start_error = start_error
        self.stop_error = stop_error
        self.uncertain = uncertain
        self.started = False
        self.closed = False
        self.start_calls = 0
        self.stop_calls = 0
        self.reset_calls = 0
        self.reload_calls = 0

    def start(self):
        self.start_calls += 1
        if self.start_error:
            raise self.start_error
        self.started = True
        return self

    def stop(self):
        self.stop_calls += 1
        if self.stop_error:
            raise self.stop_error
        self.closed = True

    def dma_uncertain(self):
        return self.uncertain

    def state(self):
        return {"started": self.started, "closed": self.closed}

    def reset(self):
        self.reset_calls += 1

    def reload(self):
        self.reload_calls += 1


class FakeTransport:
    def __init__(self, *, active=False, close_error=None):
        self.dma_may_be_active = active
        self.close_error = close_error
        self.close_calls = 0
        self.reset_calls = 0
        self.reload_calls = 0

    def close(self):
        self.close_calls += 1
        if self.close_error:
            raise self.close_error

    def reset(self):
        self.reset_calls += 1

    def reload(self):
        self.reload_calls += 1


class FakeBuffer:
    def __init__(self):
        self.free_calls = 0

    def freebuffer(self):
        self.free_calls += 1


class FakeSignalModule:
    SIGINT = 2
    SIGTERM = 15

    def __init__(self):
        self.handlers = {}

    def signal(self, signum, handler):
        self.handlers[signum] = handler


def test_display_start_precedes_allocation_and_transport():
    order = []
    display = FakeDisplay()

    def prepare():
        order.append("prepare")
        return display

    def allocate_tx():
        order.append("tx")
        return "tx"

    def allocate_rx():
        order.append("rx")
        return "rx"

    def make_transport(tx, rx):
        order.append("transport")
        return "transport"

    result = runner.bring_up_display_and_dma(
        prepare=prepare, allocate_tx=allocate_tx, allocate_rx=allocate_rx,
        make_transport=make_transport)

    assert order == ["prepare", "tx", "rx", "transport"]
    assert display.start_calls == 1
    assert result[3] == "transport"
    assert result[4] == ["tx", "rx"]


def test_display_start_failure_blocks_allocation_and_transfer():
    order = []
    display = FakeDisplay(start_error=RuntimeError("VSYNC did not advance"))

    def prepare():
        order.append("prepare")
        return display

    def allocate_tx():
        order.append("tx")
        return "tx"

    with pytest.raises(RuntimeError) as excinfo:
        runner.bring_up_display_and_dma(
            prepare=prepare, allocate_tx=allocate_tx,
            allocate_rx=lambda: order.append("rx") or "rx",
            make_transport=lambda tx, rx: order.append("transport") or "transport")

    assert order == ["prepare"]
    assert excinfo.value.display is display
    assert excinfo.value.allocated == []
    assert display.start_calls == 1


def test_prepare_failure_blocks_allocation():
    order = []

    def prepare():
        order.append("prepare")
        raise RuntimeError("clock readback")

    with pytest.raises(RuntimeError) as excinfo:
        runner.bring_up_display_and_dma(
            prepare=prepare, allocate_tx=lambda: order.append("tx"),
            allocate_rx=lambda: order.append("rx"),
            make_transport=lambda tx, rx: order.append("transport"))

    assert order == ["prepare"]
    assert excinfo.value.allocated == []
    assert not hasattr(excinfo.value, "display")


class FakeMonitor:
    def __init__(self, *, advancing=True, signature=0x4D435631):
        self.advancing = advancing
        self.signature = signature
        self.sequence = 0
        self.scans = 0
        self.writes = []

    def read(self, offset):
        if offset == 0x00:
            return self.signature
        if offset == 0x08:
            return 2
        if offset == 0x0C:
            return self.sequence
        if offset == 0x18:
            return self.scans
        if offset in (0x10, 0x20, 0x28, 0x30, 0x48):
            return 0
        if offset in (0x14, 0x24, 0x2C, 0x34, 0x4C):
            return 0
        return 0

    def write(self, offset, value):
        self.writes.append((offset, value))
        if offset == 0x04 and value == 1:
            self.sequence += 1
            if self.advancing:
                self.scans += 1


class FakeVideoModule:
    PIXEL_GRAY = "GRAY"

    @staticmethod
    def VideoMode(width, height, bpp, fps):
        return (width, height, bpp, fps)


class FakeHdmi:
    def __init__(self):
        self.configure_calls = []
        self.start_calls = 0
        self.close_calls = 0
        self.closed = False

    def configure(self, mode, pixel_format):
        self.configure_calls.append((mode, pixel_format))

    def start(self):
        self.start_calls += 1

    def close(self):
        self.close_calls += 1
        self.closed = True


class FakeVdma:
    def __init__(self, status=0):
        self.status = status

    def read(self, offset):
        assert offset == 4
        return self.status


def test_display_handle_start_waits_for_vsync_advance():
    monitor = FakeMonitor(advancing=True)
    handle = bringup.DisplayHandle(FakeHdmi(), FakeVdma(0), monitor, [50, 100, 142.857])

    handle.start(timeout_s=0.05, video_module=FakeVideoModule)

    assert handle.started is True
    assert handle.vsync_snapshot["scans"] >= 1
    assert handle.hdmi.configure_calls[0][0] == (1280, 720, 8, 60)


def test_display_handle_start_fails_without_vsync_and_keeps_handle():
    monitor = FakeMonitor(advancing=False)
    handle = bringup.DisplayHandle(FakeHdmi(), FakeVdma(0), monitor, [50, 100, 142.857])

    with pytest.raises(bringup.DisplayBringupError) as excinfo:
        handle.start(timeout_s=0.02, video_module=FakeVideoModule)

    assert excinfo.value.display is handle
    assert handle.started is True


def test_display_handle_dma_uncertain_and_stop_halt_readback():
    running = bringup.DisplayHandle(FakeHdmi(), FakeVdma(0), FakeMonitor(), [50, 100, 142.857])
    assert running.dma_uncertain() is True

    halted = bringup.DisplayHandle(FakeHdmi(), FakeVdma(1), FakeMonitor(), [50, 100, 142.857])
    assert halted.dma_uncertain() is False

    faulty = bringup.DisplayHandle(FakeHdmi(), FakeVdma(1 | 0x10), FakeMonitor(), [50, 100, 142.857])
    assert faulty.dma_uncertain() is True

    stopped = bringup.DisplayHandle(FakeHdmi(), FakeVdma(1), FakeMonitor(), [50, 100, 142.857])
    stopped.stop(timeout_s=0.01)
    assert stopped.closed is True
    assert stopped.hdmi.close_calls == 1

    not_halted = bringup.DisplayHandle(FakeHdmi(), FakeVdma(0), FakeMonitor(), [50, 100, 142.857])
    with pytest.raises(bringup.DisplayBringupError):
        not_halted.stop(timeout_s=0.01)


def test_prepare_display_sets_clocks_and_rejects_bad_monitor():
    class FakeClocks:
        fclk0_mhz = 0
        fclk1_mhz = 0
        fclk2_mhz = 0

    class FakePynq:
        Clocks = FakeClocks
        MMIO = staticmethod(lambda base, size: FakeMonitor())

    class FakeOverlay:
        def __init__(self):
            self.hdmi_out = FakeHdmi()
            self.axi_vdma = type("Vdma", (), {"mmio": FakeVdma(1)})()

    handle = bringup.prepare_display(FakeOverlay(), pynq_module=FakePynq)
    assert handle.clocks == [50, 100, 142.857]
    assert handle.monitor.read(0) == 0x4D435631

    class BadPynq(FakePynq):
        MMIO = staticmethod(lambda base, size: FakeMonitor(signature=0))

    with pytest.raises(bringup.DisplayBringupError):
        bringup.prepare_display(FakeOverlay(), pynq_module=BadPynq)


def test_signal_handler_ignores_signals_during_hold():
    hold_mode = {"active": False, "signals": []}
    signals = FakeSignalModule()
    handler = runner.install_hold_signal_handlers(hold_mode, signal_module=signals)
    assert signals.handlers[signals.SIGINT] is handler
    assert signals.handlers[signals.SIGTERM] is handler

    with pytest.raises(KeyboardInterrupt):
        handler(signals.SIGINT, None)

    hold_mode["active"] = True
    assert handler(signals.SIGTERM, None) is None
    assert hold_mode["signals"] == [signals.SIGTERM]


def test_fail_hold_never_closes_frees_or_resets_dma_owned_resources():
    display = FakeDisplay(uncertain=True)
    transport = FakeTransport(active=True)
    buffers = [FakeBuffer(), FakeBuffer()]
    hold_mode = {"active": False, "signals": []}

    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "run.json"
        report = {"status": "FAIL_HOLD", "error": "FRG timeout"}
        held = runner.finalize_run(
            held=True, display=display, transport=transport, allocated=buffers,
            capture_source=None, report=report, output=output,
            hold_mode=hold_mode, hold_loop=False)

        assert held is True
        assert display.stop_calls == 0
        assert transport.close_calls == 0
        assert [buffer.free_calls for buffer in buffers] == [0, 0]
        assert display.reset_calls == 0 and display.reload_calls == 0
        assert transport.reset_calls == 0 and transport.reload_calls == 0
        assert report["buffers_held"] is True
        assert output.is_file()
        assert json.loads(output.read_text(encoding="utf-8"))["status"] == "FAIL_HOLD"
        hold_state = json.loads((Path(str(output) + ".hold.json")).read_text(encoding="utf-8"))
        assert hold_state["status"] == "FAIL_HOLD"
        assert hold_state["buffers_held"] is True
        assert hold_mode["active"] is True


def test_normal_completion_still_stops_display_and_frees_transport():
    display = FakeDisplay(uncertain=False)
    transport = FakeTransport(active=False)
    buffers = [FakeBuffer(), FakeBuffer()]
    hold_mode = {"active": False, "signals": []}

    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "run.json"
        report = {"status": "JPEG_PL_RUN_COMPLETED_NOT_BOARD_VALIDATED"}
        held = runner.finalize_run(
            held=False, display=display, transport=transport, allocated=buffers,
            capture_source=None, report=report, output=output,
            hold_mode=hold_mode, hold_loop=False)

        assert held is False
        assert display.stop_calls == 1
        assert transport.close_calls == 1
        assert [buffer.free_calls for buffer in buffers] == [0, 0]
        assert report["buffers_held"] is False
        assert json.loads(output.read_text(encoding="utf-8"))["status"] == \
            "JPEG_PL_RUN_COMPLETED_NOT_BOARD_VALIDATED"


def test_finalize_escalates_to_hold_when_display_stop_fails():
    display = FakeDisplay(stop_error=RuntimeError("halt not proven"))
    transport = FakeTransport(active=False)
    buffers = [FakeBuffer()]
    hold_mode = {"active": False, "signals": []}

    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "run.json"
        report = {"status": "JPEG_PL_RUN_COMPLETED_NOT_BOARD_VALIDATED"}
        held = runner.finalize_run(
            held=False, display=display, transport=transport, allocated=buffers,
            capture_source=None, report=report, output=output,
            hold_mode=hold_mode, hold_loop=False)

        assert held is True
        assert transport.close_calls == 0
        assert buffers[0].free_calls == 0
        assert report["status"] == "FAIL_HOLD"
        assert report["buffers_held"] is True
        assert "display_stop_error" in report


def test_finalize_escalates_to_hold_when_transport_close_fails():
    display = FakeDisplay(uncertain=False)
    transport = FakeTransport(active=False, close_error=RuntimeError("free failed"))
    hold_mode = {"active": False, "signals": []}

    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "run.json"
        report = {"status": "JPEG_PL_RUN_COMPLETED_NOT_BOARD_VALIDATED"}
        held = runner.finalize_run(
            held=False, display=display, transport=transport, allocated=[],
            capture_source=None, report=report, output=output,
            hold_mode=hold_mode, hold_loop=False)

        assert held is True
        assert display.stop_calls == 1
        assert transport.close_calls == 1
        assert report["status"] == "FAIL_HOLD"
        assert "close_error" in report


def test_finalize_frees_allocated_buffers_when_transport_was_never_built():
    buffers = [FakeBuffer(), FakeBuffer()]
    hold_mode = {"active": False, "signals": []}

    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "run.json"
        report = {"status": "FAILED"}
        held = runner.finalize_run(
            held=False, display=None, transport=None, allocated=buffers,
            capture_source=None, report=report, output=output,
            hold_mode=hold_mode, hold_loop=False)

        assert held is False
        assert [buffer.free_calls for buffer in buffers] == [1, 1]