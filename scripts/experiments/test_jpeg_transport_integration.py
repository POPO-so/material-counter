"""Integration tests for the production transport callback assembly.

These tests call the same `assemble_candidate_callbacks` and
`run_candidate_sequence` used by main, with fake hardware dependencies, so the
overlay/preview ownership boundary is exercised rather than a test-only
lambda.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from scripts.rle.jpeg_transport import JpegRleDmaTransport

RUNNER_PATH = Path(__file__).with_name("run_jpeg_counter.py")
SPEC = importlib.util.spec_from_file_location("jpeg_transport_integration_tests", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

CANDIDATE_SHA = "1" * 64
COLD_BASE = {"kind": "COLD_BASE", "reason": "test", "candidate_mmio_before_load": False}


class FakeMmio:
    def __init__(self, base, values=None):
        self.base = base
        self.values = dict(values or {})

    def read(self, offset):
        return self.values.get(offset, 0)

    def write(self, offset, value):
        self.values[offset] = value


class FakeMmioFactory:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def __call__(self, base, size):
        self.calls.append((base, size))
        self.events.append(("mmio", base))
        if base == runner.DMA_BASE:
            return FakeMmio(base, {0x04: 0x3, 0x34: 0x3})
        if base == runner.RLE_BASE:
            return FakeMmio(base, {0x04: 0x31454C52})
        return FakeMmio(base)


class FakeOverlay:
    def __init__(self):
        self.axi_dma_0 = SimpleNamespace(sendchannel="MM2S", recvchannel="S2MM")


class FakeDisplay:
    def __init__(self, events):
        self.events = events
        self.started = False
        self.closed = False
        self.stop_calls = 0

    def start(self):
        self.events.append(("display_start",))
        self.started = True
        return self

    def stop(self):
        self.events.append(("display_stop",))
        self.stop_calls += 1
        self.closed = True

    def dma_uncertain(self):
        return self.started and not self.closed

    def state(self):
        return {"started": self.started, "closed": self.closed}


class FakeBuffer:
    def __init__(self, events, name):
        self.events = events
        self.name = name
        self.free_calls = 0

    def freebuffer(self):
        self.events.append(("free", self.name))
        self.free_calls += 1


class FakeTransportFactory:
    def __init__(self, events, error=None):
        self.events = events
        self.error = error
        self.calls = []

    def __call__(self, send, recv, dma, rle, jpeg, preview, tx, rx,
                 first_transfer_guard=None):
        self.events.append(("transport_factory",))
        self.calls.append((send, recv, dma, rle, jpeg, preview, tx, rx,
                           first_transfer_guard))
        if self.error is not None:
            raise self.error
        return "transport"


def _sequence_kwargs(events, mmio, overlay, display, transport_factory,
                     *, post_identity=None, stage=None):
    return dict(
        classification=COLD_BASE,
        mmio_factory=mmio,
        overlay_factory=lambda: overlay,
        post_load_identity=post_identity or (lambda obj: {"loaded": True}),
        candidate_dma_check=lambda: runner.check_candidate_dma_idle(
            mmio_factory=mmio),
        preview_factory=lambda: mmio(runner.OVERLAY_BASE, runner.OVERLAY_MAP_BYTES),
        prepare_display_fn=lambda obj: display,
        allocate_tx=lambda: FakeBuffer(events, "tx"),
        allocate_rx=lambda: FakeBuffer(events, "rx"),
        make_transport=runner.build_transport_callback(
            transport_factory=transport_factory, mmio_factory=mmio),
        stage=stage or (lambda name, **fields: events.append(("stage", name))),
    )


def test_production_transport_callback_receives_same_overlay_and_preview():
    events = []
    mmio = FakeMmioFactory(events)
    overlay = FakeOverlay()
    overlay.axi_dma_0.sendchannel = object()
    overlay.axi_dma_0.recvchannel = object()
    display = FakeDisplay(events)
    transport_factory = FakeTransportFactory(events)

    result = runner.run_candidate_sequence(
        **_sequence_kwargs(events, mmio, overlay, display, transport_factory))

    send, recv, dma, rle, jpeg, got_preview, tx, rx, guard = transport_factory.calls[0]
    assert send is overlay.axi_dma_0.sendchannel
    assert recv is overlay.axi_dma_0.recvchannel
    assert got_preview is result["preview"]
    assert tx is result["tx"] and rx is result["rx"]
    assert callable(guard)
    assert guard("MM2S", send, dma)["owned"] is True
    assert guard("S2MM", recv, dma)["owned"] is True
    assert guard("MM2S", recv, dma) is None
    assert guard("MM2S", send, object()) is None
    assert events.index(("display_start",)) < events.index(("transport_factory",))
    assert any(base == runner.OVERLAY_BASE for base, _ in mmio.calls)


def test_main_assembled_callbacks_normal_path_uses_production_signatures(tmp_path):
    events = []
    mmio = FakeMmioFactory(events)
    overlay = FakeOverlay()
    display = FakeDisplay(events)
    transport_factory = FakeTransportFactory(events)
    stage_names = []

    callbacks = runner.assemble_candidate_callbacks(
        artifacts={"bit_path": "/fake/candidate.bit", "bit_sha256": CANDIDATE_SHA},
        output=tmp_path / "run.json",
        report={"status": "STARTING"},
        pynq_module=SimpleNamespace(__file__="/fake/pynq/__init__.py"),
        mmio_factory=mmio,
        overlay_factory=lambda: overlay,
        allocate=lambda shape, dtype: FakeBuffer(events, f"{shape[0]}:{dtype}"),
        dtype="u1",
        display_prepare=lambda obj: display,
        transport_factory=transport_factory,
        post_load_identity=lambda obj: {"loaded": True},
        stage=lambda name, **fields: stage_names.append(name),
    )
    result = runner.run_candidate_sequence(
        classification=COLD_BASE, mmio_factory=mmio, **callbacks)

    assert result["transport"] == "transport"
    assert result["display"] is display
    assert display.started is True
    assert transport_factory.calls
    _, _, _, _, _, got_preview, got_tx, got_rx, guard = transport_factory.calls[0]
    assert got_preview is result["preview"]
    assert got_tx is result["tx"] and got_rx is result["rx"]
    assert callable(guard)
    assert stage_names[:4] == [
        "overlay_download", "overlay_loaded", "post_load_identity", "candidate_dma"
    ]
    assert "display_start" in stage_names and "transport_ready" in stage_names


def test_transport_factory_failure_holds_without_camera_or_release(tmp_path):
    events = []
    mmio = FakeMmioFactory(events)
    overlay = FakeOverlay()
    display = FakeDisplay(events)
    factory = FakeTransportFactory(events, error=RuntimeError("factory failed"))
    callbacks = runner.assemble_candidate_callbacks(
        artifacts={"bit_path": "/fake/candidate.bit", "bit_sha256": CANDIDATE_SHA},
        output=tmp_path / "run.json",
        report={"status": "FAIL_HOLD"},
        pynq_module=SimpleNamespace(__file__="/fake/pynq/__init__.py"),
        mmio_factory=mmio,
        overlay_factory=lambda: overlay,
        allocate=lambda shape, dtype: FakeBuffer(events, f"{shape[0]}:{dtype}"),
        dtype="u1",
        display_prepare=lambda obj: display,
        transport_factory=factory,
        post_load_identity=lambda obj: {"loaded": True},
        stage=lambda name, **fields: events.append(("stage", name)),
    )
    camera_opened = False
    frames_started = False
    failure = None

    try:
        runner.run_candidate_sequence(
            classification=COLD_BASE, mmio_factory=mmio, **callbacks)
    except RuntimeError as exc:
        assert "factory failed" in str(exc)
        assert exc.display is display
        assert len(exc.allocated) == 2
        failure = exc
    else:
        raise AssertionError("factory failure did not raise")

    assert failure is not None
    assert camera_opened is False and frames_started is False
    assert ("transport_ready",) not in events
    assert display.started is True
    assert ("display_stop",) not in events
    assert not any(event[0] == "free" for event in events)

    runner.finalize_run(
        held=True, display=display, transport=None, allocated=failure.allocated,
        capture_source=None, report={"status": "FAIL_HOLD"},
        output=tmp_path / "hold.json",
        hold_mode={"active": False, "signals": []}, hold_loop=False)
    assert display.stop_calls == 0
    assert all(buffer.free_calls == 0 for buffer in failure.allocated)
    assert display.closed is False


class FirstFrameMmioFactory:
    """Shared-state MMIO with PYNQ's started-but-unarmed DMA state."""

    def __init__(self, events):
        self.events = events
        self.devices = {}

    def __call__(self, base, size):
        self.events.append(("mmio", base))
        if base not in self.devices:
            if base == runner.DMA_BASE:
                self.devices[base] = FakeMmio(base, {
                    0x00: 0x10003, 0x04: 0x0, 0x30: 0x10003, 0x34: 0x0,
                    0x18: 0, 0x28: 0, 0x48: 0, 0x58: 0})
            elif base == runner.RLE_BASE:
                self.devices[base] = FakeMmio(base, {0x04: 0x31454C52})
            else:
                self.devices[base] = FakeMmio(base)
        return self.devices[base]


def test_production_guard_allows_only_an_unarmed_started_first_transfer():
    events = []
    mmio = FirstFrameMmioFactory(events)
    overlay = FakeOverlay()
    overlay.axi_dma_0.sendchannel = object()
    overlay.axi_dma_0.recvchannel = object()
    factory = FakeTransportFactory(events)
    runner.build_transport_callback(
        transport_factory=factory, mmio_factory=mmio)(
        object(), object(), overlay, object())
    send, recv, dma, rle, jpeg, preview, tx, rx, guard = factory.calls[0]

    transport = JpegRleDmaTransport(
        send, recv, dma, rle, jpeg, preview, tx, rx,
        first_transfer_guard=guard)
    transport._require_unsubmitted_first_transfer("MM2S", 0, 0x18, 0x28)
    transport._require_unsubmitted_first_transfer("S2MM", 0, 0x48, 0x58)

    dma.values[0x28] = 7
    try:
        transport._require_unsubmitted_first_transfer("MM2S", 0, 0x18, 0x28)
    except RuntimeError as exc:
        assert "committed first transfer" in str(exc)
    else:
        raise AssertionError("committed first transfer was accepted")
    dma.values[0x28] = 0
    dma.values[0x18] = 0x11000000
    try:
        transport._require_unsubmitted_first_transfer("MM2S", 0, 0x18, 0x28)
    except RuntimeError as exc:
        assert "committed first transfer" in str(exc)
    else:
        raise AssertionError("committed address was accepted")