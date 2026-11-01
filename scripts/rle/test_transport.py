import numpy as np
import pytest

from scripts.rle.protocol import (
    HEIGHT, MAX_RUNS, RX_CAPACITY, TX_BYTES, WIDTH, decode_packet,
    encode_mask,
)
from scripts.rle.transport import RleDmaTransport

RAW_TX_BYTES = WIDTH * HEIGHT
RAW_SIGNATURE = 0x52415731


class Buffer(bytearray):
    def __new__(cls, size, address):
        return super().__new__(cls, size)

    def __init__(self, size, address):
        super().__init__(size)
        self.nbytes = size
        self.physical_address = address
        self.device_address = address
        self.sync_to_count = 0
        self.sync_from_count = 0
        self.freed = 0

    def sync_to_device(self):
        self.sync_to_count += 1

    def sync_from_device(self):
        self.sync_from_count += 1

    def freebuffer(self):
        self.freed += 1


class Control:
    def __init__(self, *, raw_capable=True):
        self.values = {0x04: 0x31454C52, 0x08: 0, 0x0C: 0,
                       0x10: 0, 0x14: 0, 0x18: 0, 0x1C: 0,
                       0x20: 128, 0x24: 0, 0x28: 0,
                       0x2C: (HEIGHT << 16) | WIDTH, 0x30: MAX_RUNS}
        if raw_capable:
            self.values[0x70] = RAW_SIGNATURE
            self.values[0x74] = 0
        self.events = []
        self.start_rejected = False

    def read(self, offset):
        return self.values.get(offset, 0)

    def write(self, offset, value):
        self.events.append(("control", offset, value))
        if offset == 0:
            if self.start_rejected:
                raise RuntimeError("AXI-Lite SLVERR")
            self.values[0x08] = 1
        else:
            self.values[offset] = value


class DmaRegisters:
    def __init__(self):
        self.values = {0x04: 0x1002, 0x34: 0x1002, 0x18: 0,
                       0x28: 0, 0x48: 0, 0x58: 0}

    def read(self, offset):
        return self.values.get(offset, 0)


class Channel:
    def __init__(self, name, events, regs):
        self.name = name
        self.events = events
        self.regs = regs
        self.idle = True
        self.wait_count = 0
        self.transferred = 0
        self.pending = None

    def transfer(self, buffer, *, nbytes):
        self.events.append((self.name, "transfer", nbytes))
        self.idle = False
        self.pending = (buffer, nbytes)

    def wait(self):
        self.events.append((self.name, "wait"))
        self.wait_count += 1
        self.idle = True


class FakeRig:
    def __init__(self, *, packet=None, length=None, auto_complete=True,
                 input_mode="packet", raw_capable=True):
        self.events = []
        self.control = Control(raw_capable=raw_capable)
        self.regs = DmaRegisters()
        self.tx = Buffer(RAW_TX_BYTES if input_mode == "raw" else TX_BYTES, 0x11000000)
        self.rx = Buffer(RX_CAPACITY, 0x12000000)
        self.recv = Channel("recv", self.events, self.regs)
        self.send = Channel("send", self.events, self.regs)
        self.packet = packet
        self.length = length
        self.auto_complete = auto_complete

    def complete(self, packet):
        self.rx[:] = b"\xD3" * RX_CAPACITY
        self.rx[:len(packet)] = packet
        actual = len(packet) if self.length is None else self.length
        self.regs.values[0x58] = actual
        self.control.values[0x0C] = len(packet)
        self.control.values[0x08] = 2
        self.regs.values[0x04] = self.regs.values[0x34] = 0x1002
        self.recv.idle = self.send.idle = True
        self.recv.transferred = actual


def make_transport(rig, timeout_s=0.05, input_mode="packet"):
    transport = RleDmaTransport(rig.send, rig.recv, rig.regs,
                                rig.control, rig.tx, rig.rx, timeout_s=timeout_s,
                                input_mode=input_mode)
    if rig.auto_complete:
        old_start = rig.control.write

        def start(offset, value):
            old_start(offset, value)
            if offset == 0 and value == 1:
                packet = rig.packet
                if packet is None:
                    read = rig.control.read
                    left, top = read(0x18) & 0xffff, read(0x18) >> 16
                    right, bottom = read(0x1c) & 0xffff, read(0x1c) >> 16
                    signed = read(0x24)
                    if signed >= 0x80000000:
                        signed -= 1 << 32
                    packet = encode_mask(np.zeros((HEIGHT, WIDTH), dtype=np.uint8),
                        frame_id=read(0x10), params_version=read(0x14),
                        roi=(left, top, right, bottom), threshold=read(0x20),
                        intensity_offset=signed, morph_mode=read(0x28))
                rig.complete(packet)
        rig.control.write = start
    return transport


def test_receive_is_armed_before_rle_frame_and_uses_actual_packet_length():
    rig = FakeRig()
    transport = make_transport(rig)
    gray = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    frame = transport.transfer(gray, 17, params_version=4, roi=(5, 7, 630, 390),
                               threshold=119, intensity_offset=-9, morph_mode=2)

    assert frame.frame_id == 17
    assert frame.params_version == 4
    assert frame.roi == (5, 7, 630, 390)
    assert frame.threshold == 119
    assert frame.intensity_offset == -9
    assert frame.morph_mode == 2
    assert frame.valid and frame.actual_bytes < RX_CAPACITY
    assert rig.tx.sync_to_count == 1 and rig.rx.sync_from_count == 1
    assert rig.recv.wait_count == rig.send.wait_count == 1
    events = rig.events
    recv_transfer = next(i for i, event in enumerate(events) if event[:2] == ("recv", "transfer"))
    send_transfer = next(i for i, event in enumerate(events) if event[:2] == ("send", "transfer"))
    assert recv_transfer < send_transfer
    assert rig.tx.freed == rig.rx.freed == 0


def test_completed_transport_can_process_next_frame():
    rig = FakeRig()
    transport = make_transport(rig)
    gray = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    assert transport.transfer(gray, 1).frame_id == 1
    assert transport.transfer(gray, 2).frame_id == 2
    transport.close()
    transport.close()
    assert rig.tx.freed == rig.rx.freed == 1


def test_overflow_packet_is_returned_as_invalid_but_dma_is_quiescent():
    gray = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    mask = np.zeros_like(gray)
    mask[:, ::2] = 255
    packet = encode_mask(mask, frame_id=18, max_runs=MAX_RUNS)
    rig = FakeRig(packet=packet)
    transport = make_transport(rig)
    frame = transport.transfer(gray, 18)
    assert not frame.valid
    assert frame.run_count == MAX_RUNS
    assert frame.total_run_count > MAX_RUNS
    assert not transport.dma_may_be_active


@pytest.mark.parametrize("length_delta", [-4, 4])
def test_disagreement_between_dma_and_hardware_actual_length_is_rejected(length_delta):
    packet = encode_mask(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), frame_id=19)
    rig = FakeRig(packet=packet, length=len(packet) + length_delta)
    transport = make_transport(rig)
    with pytest.raises(RuntimeError, match="length"):
        transport.transfer(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), 19)
    assert not transport.dma_may_be_active


def test_stale_rx_tail_is_not_parsed():
    packet = encode_mask(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), frame_id=20)
    rig = FakeRig(packet=packet)
    transport = make_transport(rig)
    frame = transport.transfer(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), 20)
    assert frame.actual_bytes == len(packet)
    assert rig.rx[frame.actual_bytes:] == b"\xD3" * (RX_CAPACITY-frame.actual_bytes)


def test_preflight_failure_happens_before_dma_and_does_not_free_buffers():
    rig = FakeRig()
    rig.control.values[0x04] = 0
    transport = make_transport(rig)
    with pytest.raises(RuntimeError, match="signature"):
        transport.transfer(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), 21)
    assert not rig.events
    assert rig.tx.freed == rig.rx.freed == 0


def test_uncertain_dma_ownership_blocks_close_after_timeout():
    rig = FakeRig(auto_complete=False)
    transport = make_transport(rig, timeout_s=0.001)
    with pytest.raises(TimeoutError):
        transport.transfer(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), 22)
    assert transport.dma_may_be_active
    with pytest.raises(RuntimeError, match="ownership"):
        transport.transfer(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), 23)
    with pytest.raises(RuntimeError, match="DMA-owned"):
        transport.close()
    assert rig.tx.freed == rig.rx.freed == 0


def test_invalid_output_identity_is_rejected_after_safe_completion():
    packet = encode_mask(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), frame_id=23)
    rig = FakeRig(packet=packet)
    transport = make_transport(rig)
    with pytest.raises(ValueError, match="frame ID"):
        transport.transfer(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), 24)
    assert not transport.dma_may_be_active


def test_slverr_on_start_keeps_both_buffers_held():
    rig = FakeRig()
    rig.control.start_rejected = True
    transport = make_transport(rig)
    with pytest.raises(RuntimeError, match="SLVERR"):
        transport.transfer(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), 25)
    assert transport.dma_may_be_active
    with pytest.raises(RuntimeError, match="DMA-owned"):
        transport.close()
    assert rig.tx.freed == rig.rx.freed == 0


def test_raw_mode_checks_capability_then_sends_exact_gray8_frame():
    gray = np.arange(HEIGHT * WIDTH, dtype=np.uint32).astype(np.uint8).reshape(HEIGHT, WIDTH)
    rig = FakeRig(input_mode="raw")
    transport = make_transport(rig, input_mode="raw")
    frame = transport.transfer(gray, 31)
    assert frame.frame_id == 31
    assert bytes(rig.tx) == gray.tobytes()
    assert rig.tx.sync_to_count == 1
    assert ("send", "transfer", RAW_TX_BYTES) in rig.events
    assert rig.control.values[0x74] == 1


def test_raw_mode_rejects_unknown_capability_before_any_dma():
    rig = FakeRig(input_mode="raw", raw_capable=False)
    transport = make_transport(rig, input_mode="raw")
    with pytest.raises(RuntimeError, match="RAW1"):
        transport.transfer(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), 32)
    assert not rig.events


def test_raw_mode_rejects_non_gray8_payload_before_dma():
    rig = FakeRig(input_mode="raw")
    transport = make_transport(rig, input_mode="raw")
    with pytest.raises(ValueError, match="shape|uint8"):
        transport.transfer(np.zeros((HEIGHT, WIDTH - 1), dtype=np.uint8), 33)
    assert not rig.events


def test_raw_mode_rejects_rle_output_length_disagreement_after_dma_quiesces():
    packet = encode_mask(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), frame_id=34)
    rig = FakeRig(packet=packet, length=len(packet) + 4, input_mode="raw")
    transport = make_transport(rig, input_mode="raw")
    with pytest.raises(RuntimeError, match="length"):
        transport.transfer(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), 34)
    assert ("send", "transfer", RAW_TX_BYTES) in rig.events
    assert not transport.dma_may_be_active
