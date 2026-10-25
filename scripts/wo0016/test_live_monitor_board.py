"""Injected hardware contracts only: never imports PYNQ or opens a device."""
import asyncio
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_live_monitor as live
from live_monitor_state import LiveMonitorState


@pytest.fixture
def admitted(monkeypatch, tmp_path):
    calls = []
    pf, old, board = live.finite.preflight, live.finite, live.finite.board_once
    args = live.parser().parse_args(["--mode", "board", "--execute", "--manifest", "reviewed.json",
        "--manifest-sha256", "a" * 64, "--expected-boot-id", "test-boot",
        "--stage1-receipt", str(tmp_path / "receipt.json"), "--stage1-receipt-sha256", "b" * 64])
    Path(args.stage1_receipt).write_text("{}", encoding="ascii")
    args.stage1_receipt_sha256 = hashlib.sha256(b"{}").hexdigest()
    monkeypatch.setenv("XILINX_XRT", "/usr")
    plan = SimpleNamespace(ready_for_board=True, manifest={"clock_mhz": 50}, boot_id="test-boot",
        bit_path=Path("loaded.bit"), hp0_range=(0, 0x20000000), hp1_range=(0, 0x20000000))
    def manifest(path, digest, *, approved_boot_id):
        assert (path, digest, approved_boot_id) == (args.manifest, args.manifest_sha256, args.expected_boot_id)
        calls.append("manifest")
        return plan
    monkeypatch.setattr(pf, "validate_manifest", manifest)
    monkeypatch.setattr(pf, "read_boot_id", lambda: "test-boot")
    monkeypatch.setattr(pf, "read_cma_meminfo", lambda: {"CmaFree": 65536, "CmaTotal": 131072})
    monkeypatch.setattr(pf, "read_cma_admission", lambda **kwargs: SimpleNamespace(ranges=((0x10000000, 0x20000000),)))
    monkeypatch.setattr(pf, "audit_process_ownership", lambda: calls.append("ownership") or {})
    monkeypatch.setattr(pf, "read_fpga_manager", lambda: {})
    monkeypatch.setattr(old, "_require_camera_device_unowned", lambda camera: calls.append("camera-owner"))
    monkeypatch.setattr(old, "require_standard_camera_controls", lambda camera: calls.append("camera-controls"))
    monkeypatch.setattr(board, "require_overlay_pass_receipt", lambda path, checked: calls.append("receipt"))
    monkeypatch.setattr(board, "_assert_no_terminal_failure", lambda: None)
    monkeypatch.setattr(board, "read_afi1_snapshot", lambda *args: {"afi1_status": "READ", "registers": {
        "AFI1_RDCHAN_CTRL": {"value": 1}, "AFI1_WRCHAN_CTRL": {"value": 0xf01}}})
    # Keep the actual exact readback validator and address/range validators.
    class Buffer(np.ndarray):
        def __new__(cls, size, address):
            value = np.zeros(size, np.uint8).view(cls)
            value.physical_address = address
            return value
    class Overlay:
        @property
        def axi_dma_0(self):
            calls.append("dma-driver")
            return SimpleNamespace(recvchannel=SimpleNamespace(transferred=live.RX_BYTES), sendchannel=object())
    class API:
        def __init__(self, *args, **kwargs):
            self._pynq = SimpleNamespace(Overlay=self.overlay)
            self.freed = []
            self.address = 0x10000000
        def overlay(self, path, *, download):
            assert download is False
            return Overlay()
        def audit_environment(self, **kwargs): return {"boot_id": "test-boot"}
        def open_mmio(self): calls.append("open-mmio")
        def cma_free_kib(self): return 65536
        def allocate(self, size):
            calls.append("allocate")
            value = Buffer(size, self.address)
            self.address += 0x100000
            return value
        def device_address(self, buffer): return buffer.physical_address
        def read32(self, base, offset):
            if base == pf.DMA_BASE: return board.DMA_IDLE
            return {0x04: pf.PACKET_MAGIC, board.PACKER_SIGNATURE: pf.PACKER_SIGNATURE}.get(offset, 0)
        def free(self, buffer): self.freed.append(id(buffer))
    monkeypatch.setattr(board, "PynqBoardAPI", API)
    session = live.BoardSession(args, LiveMonitorState(mode="board"))
    monkeypatch.setattr(session, "acquire_runtime_lock", lambda: calls.append("lock"))
    return session, calls


def test_successful_admission_orders_all_gates_before_dma_and_frees_once(admitted):
    session, calls = admitted
    session.open()
    assert calls[0:2] == ["lock", "manifest"]
    assert calls.index("receipt") < calls.index("open-mmio") < calls.index("allocate") < calls.index("dma-driver")
    assert calls.count("allocate") == 4
    session.close()
    session.close()
    assert len(set(session.api.freed)) == len(session.api.freed) == 4


def test_camera_warmup_control_report_reaches_live_monitor_json(monkeypatch):
    args = live.parser().parse_args(["--mode", "board"])
    state = LiveMonitorState(mode="board")
    session = live.BoardSession(args, state)
    session.dma = SimpleNamespace(recvchannel=object(), sendchannel=object())
    report = {
        "required_controls_verified": True,
        "required_controls": {
            "auto_exposure": 1,
            "exposure_time_absolute": 50,
            "brightness": 0,
            "gamma": 100,
        },
        "gain": {
            "requested": 0,
            "value": None,
            "status": "not_exposed",
            "verification": "not_verified",
            "message": "UVC 驱动未暴露 gain 控件，未能通过 V4L2 验证",
            "driver_response": "unknown control 'gain'",
            "set_response": "unknown control 'gain'",
        },
    }

    class FakeStream:
        def __init__(self, **kwargs):
            pass

        async def run(self, frames, *, stop_requested):
            async for _ in frames:
                pass

    async def camera_frames(*args, **kwargs):
        callback = kwargs.get("camera_controls_callback")
        if callback is not None:
            callback(report)
        yield np.zeros((240, 320), dtype=np.uint8)

    monkeypatch.setattr(live, "DoubleBufferStream", FakeStream)
    monkeypatch.setattr(live.finite, "_camera_gray_frames_impl", camera_frames)

    asyncio.run(session.run(counter=object(), publish=lambda *args: None))

    snapshot = state.snapshot()
    assert snapshot["camera_controls"] == report
    assert snapshot["dashboard"]["camera_controls"]["gain"]["message"] == (
        "UVC 驱动未暴露 gain 控件，未能通过 V4L2 验证")


@pytest.mark.parametrize("failure", ["boot", "receipt-hash", "cma", "camera", "owner", "signature"])
def test_admission_rejects_before_dma_driver(admitted, monkeypatch, failure):
    session, calls = admitted
    pf, old = live.finite.preflight, live.finite
    def reject(*args, **kwargs): raise ValueError("injected admission rejection")
    if failure == "boot": monkeypatch.setattr(pf, "read_boot_id", lambda: "wrong")
    elif failure == "receipt-hash": session.args.stage1_receipt_sha256 = "c" * 64
    elif failure == "cma": monkeypatch.setattr(pf, "read_cma_meminfo", lambda: {"CmaFree": 1})
    elif failure == "camera": monkeypatch.setattr(old, "_require_camera_device_unowned", reject)
    elif failure == "owner": monkeypatch.setattr(pf, "audit_process_ownership", reject)
    else: monkeypatch.setattr(old.board_once.PynqBoardAPI, "read32", lambda *args: 0)
    try:
        with pytest.raises((ValueError, RuntimeError)):
            session.open()
        assert "dma-driver" not in calls
        assert "allocate" not in calls
    finally:
        session.close()


def test_sigterm_sets_request_and_restores_handler():
    import signal
    import threading
    stop = threading.Event()
    previous = signal.getsignal(signal.SIGTERM)
    with live.stop_signals(stop):
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert stop.is_set()
    assert signal.getsignal(signal.SIGTERM) == previous
