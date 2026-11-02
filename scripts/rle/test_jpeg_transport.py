import hashlib

import numpy as np
import pytest

import scripts.rle.jpeg_transport as jpeg_transport

from scripts.rle.protocol import HEIGHT, MAX_RUNS, RX_CAPACITY, WIDTH, encode_mask
from scripts.rle.jpeg_transport import (
    JPEG_MAX_BYTES, JpegRleDmaTransport, iter_raw_mjpeg, preflight_jpeg_header,
)


def _baseline_header(*, width=WIDTH, height=HEIGHT, sof=0xC0,
                     sampling=((2, 1), (1, 1), (1, 1)), dri=40):
    buf = bytearray(b"\xff\xd8")
    body = bytearray((8, (height >> 8) & 0xFF, height & 0xFF,
                      (width >> 8) & 0xFF, width & 0xFF, len(sampling)))
    for i, (h, v) in enumerate(sampling):
        body += bytes((i + 1, (h << 4) | v, 0))
    buf += bytes((0xFF, sof)) + (len(body) + 2).to_bytes(2, "big") + body
    buf += b"\xff\xdd\x00\x04" + dri.to_bytes(2, "big")
    buf += b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"  # single SOS
    buf += b"\xff\xd9"
    return bytes(buf)


def test_preflight_accepts_422_baseline_dri40():
    info = preflight_jpeg_header(_baseline_header())
    assert info["width"] == WIDTH and info["height"] == HEIGHT and info["precision"] == 8
    assert info["sampling"] == [(2, 1), (1, 1), (1, 1)]
    assert info["dri"] == 40


def test_preflight_rejects_non_baseline_progressive():
    with pytest.raises(ValueError, match="baseline"):
        preflight_jpeg_header(_baseline_header(sof=0xC2))


def test_preflight_rejects_wrong_geometry():
    with pytest.raises(ValueError, match="640x400"):
        preflight_jpeg_header(_baseline_header(width=320))


def test_preflight_rejects_wrong_restart_interval():
    with pytest.raises(ValueError, match="DRI must be 40"):
        preflight_jpeg_header(_baseline_header(dri=10))


def test_preflight_rejects_non_422_sampling():
    with pytest.raises(ValueError, match="4:2:2"):
        preflight_jpeg_header(_baseline_header(sampling=((1, 1),)))


class Buffer(bytearray):
    def __new__(cls, size, address):
        return super().__new__(cls, size)

    def __init__(self, size, address):
        super().__init__(size)
        self.nbytes = size
        self.physical_address = address
        self.device_address = address
        self.sync_to_count = self.sync_from_count = self.freed = 0

    def sync_to_device(self): self.sync_to_count += 1
    def sync_from_device(self): self.sync_from_count += 1
    def freebuffer(self): self.freed += 1


class Mmio:
    def __init__(self, values=None, events=None, name="mmio"):
        self.values = dict(values or {})
        self.events = events if events is not None else []
        self.name = name
        self.on_write = None
        self.on_read = None

    def read(self, offset):
        if self.on_read: self.on_read(offset)
        value = self.values.get(offset, 0)
        self.events.append((self.name, "read", offset, value))
        if self.name == "jpeg" and offset == 0x24:
            self.values[offset] = 0
        if self.name == "jpeg" and offset == 0x00:
            self.values[offset] = value & ~0x2  # generated AP_CTRL ap_done is COR
        return value

    def write(self, offset, value):
        self.events.append((self.name, "write", offset, value))
        self.values[offset] = value
        if self.on_write: self.on_write(offset, value)


class Channel:
    def __init__(self, name, events):
        self.name, self.events = name, events
        self.wait_count = 0

    def transfer(self, buffer, *, nbytes):
        self.events.append((self.name, "transfer", nbytes))
        rig = getattr(self, "rig", None)
        if rig is not None and rig.driver_started:
            if self.name == "s2mm":
                rig.dma.values[0x48] = buffer.physical_address
                rig.dma.values[0x4C] = 0
                rig.dma.values[0x58] = nbytes
                rig.dma.values[0x34] = 0
                rig.recv_started = True
            else:
                rig.dma.values[0x18] = buffer.physical_address
                rig.dma.values[0x1C] = 0
                rig.dma.values[0x28] = nbytes
                rig.dma.values[0x04] = 0
                rig.send_started = True
        if rig is not None and rig.dma_error:
            rig.dma.values[0x04 if self.name == "mm2s" else 0x34] = 0x10
        if rig is not None and rig.driver_started:
            rig._maybe_complete()
    def wait(self):
        self.events.append((self.name, "wait")); self.wait_count += 1


class Rig:
    def __init__(self, jpeg=b"\xff\xd8raw\xff\xd9", *, jpeg_status=0,
                 rle_packet=None, frg=1, preview=1 << 3, drop=0,
                 dma_error=False, auto_complete=True, late_downstream=False,
                 driver_started=False):
        self.events = []
        self.tx, self.rx = Buffer(JPEG_MAX_BYTES, 0x11000000), Buffer(RX_CAPACITY, 0x12000000)
        self.send, self.recv = Channel("mm2s", self.events), Channel("s2mm", self.events)
        self.send.rig = self.recv.rig = self
        initial_status = 0x0 if driver_started else 0x1002
        initial_control = 0x10003 if driver_started else 0
        self.dma = Mmio({0x00: initial_control, 0x04: initial_status,
                         0x30: initial_control, 0x34: initial_status,
                         0x18: 0, 0x28: 0, 0x48: 0, 0x58: 0}, self.events, "dma")
        self.driver_started = driver_started
        self.guard_calls = []
        self.jpeg_started = False
        self.recv_started = False
        self.send_started = False
        self.rle = Mmio({0x04: 0x31454C52, 0x08: 0, 0x0c: 0,
                         0x10: 0, 0x14: 0, 0x18: 0, 0x1c: 0,
                         0x20: 128, 0x24: 0, 0x28: 0,
                         0x2c: (HEIGHT << 16) | WIDTH, 0x30: MAX_RUNS,
                         0x70: 0x52415731, 0x74: 0}, self.events, "rle")
        self.jpeg = Mmio({0x00: 4, 0x10: 0, 0x18: 0, 0x20: 0xdeadbeef, 0x24: 0}, self.events, "jpeg")
        self.preview = Mmio({0x0000: 0x50424f31, 0x0008: preview,
            0x0018: 9, 0x001c: 9, 0x0010: drop, 0x0200: 0x46524731,
            0x0204: frg, 0x0208: 9, 0x0214: drop}, self.events, "preview")
        self.rle_packet = rle_packet
        self.jpeg_status = jpeg_status
        self.auto_complete = auto_complete
        self.late_downstream = late_downstream
        self.dma_error = dma_error
        def jpeg_valid_read(offset):
            if offset == 0x24 and self.jpeg.values.get(0x00, 0) & 1:
                self.jpeg.values[0x24] = 1
                self.jpeg.values[0x20] = self.jpeg_status
        self.jpeg.on_read = jpeg_valid_read
        self.preview.on_write = self._preview_write
        self.jpeg.on_write = self._jpeg_write

    def _jpeg_write(self, off, val):
        if off == 0 and val == 1 and self.auto_complete:
            self.jpeg_started = True
            if not self.driver_started:
                self._complete()
            else:
                self._maybe_complete()

    def _maybe_complete(self):
        if (self.driver_started and self.auto_complete and self.jpeg_started
                and self.recv_started and self.send_started):
            self._complete()

    def _complete(self):
        packet = self.rle_packet
        if packet is None:
            packet = encode_mask(np.zeros((HEIGHT, WIDTH), np.uint8),
                frame_id=self.jpeg.values[0x18], params_version=self.rle.values[0x14],
                roi=(0, 0, WIDTH, HEIGHT), threshold=128)
        self.rx[:len(packet)] = packet
        self.dma.values[0x58] = len(packet)
        self.rle.values[0x0c] = len(packet)
        self.rle.values[0x08] = 1 if self.late_downstream else 2
        self.dma.values[0x04] = self.dma.values[0x34] = 0 if self.late_downstream else 0x1002
        if self.late_downstream:
            self.rle_state_reads = 0
            def finish_downstream(offset):
                if offset == 0x08:
                    self.rle_state_reads += 1
                    if self.rle_state_reads >= 2:
                        self.rle.values[0x08] = 2
                        self.dma.values[0x04] = self.dma.values[0x34] = 0x1002
            self.rle.on_read = finish_downstream
        self.jpeg.values[0x00] = 6
        self.jpeg.values[0x20] = self.jpeg_status
        self.jpeg.values[0x24] = 1
        self.preview.values[0x0204] = 1
        self.preview.values[0x0208] = self.jpeg.values[0x18]
        self.preview.values[0x0018] = self.jpeg.values[0x18]

    def _preview_write(self, off, val):
        if off == 0x24 and val == 1:
            self.preview.values[0x0008] |= 1 << 4
            self.preview.values[0x001c] = self.preview.values[0x20]

    def guard(self, name, channel, regs):
        expected = self.send if name == "MM2S" else self.recv
        self.guard_calls.append((name, channel is expected, regs is self.dma))
        if regs is not self.dma or channel is not expected:
            return None
        if name == "MM2S":
            base, address = 0x00, 0x18
        elif name == "S2MM":
            base, address = 0x30, 0x48
        else:
            return None
        return {"owned": True,
                "control": self.dma.values.get(base, 0),
                "status": self.dma.values.get(base + 4, 0),
                "address": self.dma.values.get(address, 0),
                "length": self.dma.values.get(base + 0x28, 0)}


def transport(rig, timeout_s=0.02, first_transfer_guard="auto", rx_capture=None):
    if first_transfer_guard == "auto":
        first_transfer_guard = rig.guard if rig.driver_started else None
    kwargs = {}
    if first_transfer_guard is not None:
        kwargs["first_transfer_guard"] = first_transfer_guard
    if rx_capture is not None:
        kwargs["rx_capture"] = rx_capture
    return JpegRleDmaTransport(rig.send, rig.recv, rig.dma, rig.rle,
        rig.jpeg, rig.preview, rig.tx, rig.rx, timeout_s=timeout_s, **kwargs)


def test_accepts_raw_jpeg_with_exact_byte_length_order_and_single_mask_accept():
    rig = Rig(jpeg=b"\xff\xd8abc\xff\xd9")
    frame = transport(rig).transfer(b"\xff\xd8abc\xff\xd9", 9, params_version=3)
    assert frame.frame_id == 9 and frame.params_version == 3 and frame.status == 0
    assert rig.tx[:7] == b"\xff\xd8abc\xff\xd9"
    assert rig.tx.sync_to_count == rig.rx.sync_from_count == 1
    assert sum(e[:3] == ("preview", "write", 0x24) for e in rig.events) == 1
    assert ("mm2s", "transfer", 7) in rig.events
    assert rig.events.index(("s2mm", "transfer", RX_CAPACITY)) < rig.events.index(("rle", "write", 0, 1))
    assert rig.events.index(("jpeg", "write", 0, 1)) < rig.events.index(("mm2s", "transfer", 7))


def test_deferred_mask_accept_waits_for_same_frame_count_and_boxes_commit():
    rig = Rig(preview=(1 << 3) | 1)
    t = transport(rig)
    frame = t.transfer(b"\xff\xd8abc\xff\xd9", 9, auto_accept=False)
    assert frame.frame_id == 9
    assert not any(e[:3] == ("preview", "write", 0x24) for e in rig.events)

    t.publish_overlay(9, [(10, 20, 19, 29)], line_x=321)

    assert not any(e[:3] == ("preview", "write", 0x44) for e in rig.events)
    assert ("preview", "write", 0x100, 10 | (19 << 16)) in rig.events
    assert ("preview", "write", 0x104, 20 | (29 << 16)) in rig.events
    commit = rig.events.index(("preview", "write", 0x40, 1))
    accept = rig.events.index(("preview", "write", 0x24, 1))
    assert commit < accept
    assert t._pending_frame_id is None


def test_deferred_frame_cannot_be_replaced_or_published_with_another_id():
    rig = Rig()
    t = transport(rig)
    t.transfer(b"\xff\xd8abc\xff\xd9", 9, auto_accept=False)
    with pytest.raises(RuntimeError, match="previous completed frame"):
        t.transfer(b"\xff\xd8def\xff\xd9", 10, auto_accept=False)
    with pytest.raises(ValueError, match="pending completed frame"):
        t.publish_overlay(10, [], line_x=320)
    assert not any(e[:3] == ("preview", "write", 0x24) for e in rig.events)


@pytest.mark.parametrize("state", [0x1, 0x4, 0x5])
def test_rle_busy_or_error_preflight_does_not_write_buffer_or_start_channels(state):
    rig = Rig()
    rig.rle.values[0x08] = state
    original = bytes(rig.tx[:8])
    with pytest.raises(RuntimeError, match="RLE pipeline"):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    assert bytes(rig.tx[:8]) == original
    assert not any(e[0] in ("s2mm", "mm2s") and e[1] == "transfer" for e in rig.events)


def test_jpeg_non_idle_preflight_does_not_write_buffer_or_start_channels():
    rig = Rig()
    rig.jpeg.values[0x00] = 0x2  # ap_done, but ap_idle=0
    original = bytes(rig.tx[:8])
    with pytest.raises(RuntimeError, match="JPEG core is not idle"):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    assert bytes(rig.tx[:8]) == original
    assert not any(e[0] in ("s2mm", "mm2s") and e[1] == "transfer" for e in rig.events)


def test_raw_input_mode_is_configured_before_dma_and_pipeline():
    rig = Rig(driver_started=True)
    transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    events = rig.events
    mode_index = events.index(("rle", "write", 0x74, 1))
    first_dma = min(i for i, event in enumerate(events)
                    if event[0] in ("s2mm", "mm2s") and event[1] == "transfer")
    rle_start = events.index(("rle", "write", 0x00, 1))
    jpeg_start = events.index(("jpeg", "write", 0x00, 1))
    assert mode_index < first_dma
    assert mode_index < rle_start
    assert mode_index < jpeg_start
    assert rig.rle.values[0x74] == 1


def test_raw_capability_missing_is_rejected_before_tx_or_start():
    rig = Rig(driver_started=True)
    rig.rle.values[0x70] = 0
    tx_before = bytes(rig.tx[:8])
    with pytest.raises(RuntimeError, match="raw input capability"):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    _assert_no_start_or_tx_write(rig, tx_before)
    assert not any(event[:3] == ("rle", "write", 0x74)
                   for event in rig.events)


def test_raw_mode_readback_failure_is_rejected_before_tx_or_start():
    rig = Rig(driver_started=True)

    def ignore_mode(offset, value):
        if offset == 0x74:
            rig.rle.values[0x74] = 0

    rig.rle.on_write = ignore_mode
    tx_before = bytes(rig.tx[:8])
    with pytest.raises(RuntimeError, match="readback mismatch"):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    _assert_no_start_or_tx_write(rig, tx_before)


def test_rx_capture_matches_decode_input(monkeypatch):
    decoded = []
    real_decode = jpeg_transport.decode_packet

    def record(packet, *args, **kwargs):
        decoded.append(bytes(packet))
        return real_decode(packet, *args, **kwargs)

    monkeypatch.setattr(jpeg_transport, "decode_packet", record)
    captures = []
    rig = Rig(driver_started=True)
    transport_with_capture = transport(
        rig, rx_capture=lambda frame_id, packet, metadata: captures.append(
            (frame_id, packet, metadata)))
    transport_with_capture.transfer(b"\xff\xd8a\xff\xd9", 9)

    assert captures and decoded
    frame_id, packet, metadata = captures[0]
    assert frame_id == 9
    assert packet == decoded[0]
    assert metadata["length"] == len(decoded[0])
    assert metadata["length"] == metadata["dma_bytes"] == metadata["rle_bytes"]
    assert metadata["length"] < metadata["rx_nbytes"]
    assert metadata["sha256"] == hashlib.sha256(decoded[0]).hexdigest()
    assert metadata["head_hex"] == decoded[0][:64].hex()
    assert metadata["tail_hex"] == decoded[0][-64:].hex()
    assert len(metadata["header_words"]) == 16
    assert metadata["rx_physical_address"] == rig.rx.physical_address
    assert metadata["hardware_s2mm_da"] == rig.rx.physical_address
    assert metadata["physical_matches_da"] is True
    assert metadata["input_mode"] == 1
    assert metadata["raw_capability"] == 0x52415731
    assert metadata["s2mm_idle"] is True
    assert metadata["completion_state"]["dma_length_matches_rle"] is True

    transport_with_capture.transfer(b"\xff\xd8b\xff\xd9", 10)
    assert len(captures) == 1
    assert len(decoded) == 2


def test_rx_capture_failure_does_not_mask_decode_error():
    rig = Rig(driver_started=True, rle_packet=b"malformed" * 256)

    def bad_capture(*args):
        raise OSError("capture failed")

    transport_with_capture = transport(rig, rx_capture=bad_capture)
    with pytest.raises(ValueError, match="RLE header magic or version"):
        transport_with_capture.transfer(b"\xff\xd8a\xff\xd9", 9)
    assert "capture failed" in (transport_with_capture.rx_capture_error or "")


def test_rx_capture_attempts_once_and_preserves_first_error():
    calls = []

    def bad_capture(*args):
        calls.append(args)
        raise OSError("capture failed")

    rig = Rig(driver_started=True)
    transport_with_capture = transport(rig, rx_capture=bad_capture)
    assert transport_with_capture.transfer(b"\xff\xd8a\xff\xd9", 9).frame_id == 9
    first_error = transport_with_capture.rx_capture_error
    assert "capture failed" in (first_error or "")
    assert len(calls) == 1
    assert transport_with_capture._rx_capture_done is True

    assert transport_with_capture.transfer(b"\xff\xd8b\xff\xd9", 10).frame_id == 10
    assert len(calls) == 1
    assert transport_with_capture.rx_capture_error == first_error


def test_rx_capture_metadata_read_failure_marks_attempt_and_preserves_error():
    rig = Rig(driver_started=True)
    calls = []
    transport_with_capture = transport(
        rig, rx_capture=lambda *args: calls.append(args))
    real_read = rig.dma.read

    def fail_status(offset):
        if offset == jpeg_transport.DMA_S2MM_STATUS:
            raise OSError("metadata read failed")
        return real_read(offset)

    rig.dma.read = fail_status
    transport_with_capture._capture_rx(9, b"\x00" * 64, 64, 64)
    assert calls == []
    assert transport_with_capture._rx_capture_done is True
    first_error = transport_with_capture.rx_capture_error
    assert "metadata read failed" in (first_error or "")

    rig.dma.read = real_read
    transport_with_capture._capture_rx(9, b"\x00" * 64, 64, 64)
    assert calls == []
    assert transport_with_capture.rx_capture_error == first_error


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit])
def test_rx_capture_control_exceptions_propagate_unchanged(monkeypatch, exc_type):
    decoded = []
    real_decode = jpeg_transport.decode_packet

    def record(packet, *args, **kwargs):
        decoded.append(bytes(packet))
        return real_decode(packet, *args, **kwargs)

    monkeypatch.setattr(jpeg_transport, "decode_packet", record)
    calls = []

    def control_capture(*args):
        calls.append(args)
        raise exc_type("stop")

    rig = Rig(driver_started=True)
    transport_with_capture = transport(rig, rx_capture=control_capture)
    with pytest.raises(exc_type, match="stop"):
        transport_with_capture.transfer(b"\xff\xd8a\xff\xd9", 9)

    assert len(calls) == 1
    assert decoded == []
    assert transport_with_capture._rx_capture_done is True
    assert transport_with_capture.rx_capture_error is None
    # Capture runs after completion returned DMA ownership, so the existing
    # main-flow held expression receives the same state as any decode-stage
    # exception; this change does not alter its HOLD determination.
    assert transport_with_capture.dma_may_be_active is False
    transfers = [event for event in rig.events if event[1] == "transfer"]
    assert [event[0] for event in transfers] == ["s2mm", "mm2s"]


def test_repeated_call_accepts_prior_completed_rle_and_idle_jpeg_state():
    rig = Rig()
    t = transport(rig)
    assert t.transfer(b"\xff\xd8a\xff\xd9", 9).frame_id == 9
    assert t.transfer(b"\xff\xd8b\xff\xd9", 10).frame_id == 10


def _assert_no_start_or_tx_write(rig, tx_before):
    assert bytes(rig.tx[:len(tx_before)]) == tx_before
    assert not any(e[0] in ("s2mm", "mm2s") and e[1] == "transfer"
                   for e in rig.events)
    assert not any(e[:3] == ("rle", "write", 0x00) for e in rig.events)
    assert not any(e[:3] == ("jpeg", "write", 0x00) for e in rig.events)


def test_driver_started_status_zero_without_ownership_proof_is_rejected():
    rig = Rig(driver_started=True)
    tx_before = bytes(rig.tx[:8])
    with pytest.raises(RuntimeError, match="not idle"):
        transport(rig, first_transfer_guard=None).transfer(
            b"\xff\xd8a\xff\xd9", 9)
    _assert_no_start_or_tx_write(rig, tx_before)


def test_driver_started_status_zero_with_proof_is_accepted():
    rig = Rig(driver_started=True)
    frame = transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    assert frame.frame_id == 9
    assert len(rig.guard_calls) == 2


@pytest.mark.parametrize("zero_channel", ["mm2s", "s2mm"])
def test_driver_started_single_channel_status_zero_is_accepted(zero_channel):
    rig = Rig(driver_started=True)
    idle_offset = 0x04 if zero_channel == "mm2s" else 0x34
    other = 0x34 if zero_channel == "mm2s" else 0x04
    rig.dma.values[other] = 0x1002
    frame = transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    assert frame.frame_id == 9


def test_second_frame_after_completion_does_not_use_first_exception():
    rig = Rig(driver_started=True)
    t = transport(rig)
    assert t.transfer(b"\xff\xd8a\xff\xd9", 9).frame_id == 9
    calls_after_first = len(rig.guard_calls)
    assert t.transfer(b"\xff\xd8b\xff\xd9", 10).frame_id == 10
    assert len(rig.guard_calls) == calls_after_first


def test_new_transport_on_committed_active_transfer_is_rejected():
    rig = Rig(driver_started=True)
    rig.dma.values[0x48] = 0x12000000
    rig.dma.values[0x58] = RX_CAPACITY
    rig.dma.values[0x34] = 0
    tx_before = bytes(rig.tx[:8])
    with pytest.raises(RuntimeError, match="not idle"):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    _assert_no_start_or_tx_write(rig, tx_before)


@pytest.mark.parametrize("channel,address,length", [
    ("s2mm", 0x12000000, 0),
    ("s2mm", 0, RX_CAPACITY),
    ("mm2s", 0x11000000, 0),
    ("mm2s", 0, JPEG_MAX_BYTES),
])
def test_partial_submission_anomaly_is_rejected(channel, address, length):
    rig = Rig(driver_started=True)
    base = 0x00 if channel == "mm2s" else 0x30
    addr_offset = 0x18 if channel == "mm2s" else 0x48
    rig.dma.values[base + 4] = 0
    rig.dma.values[addr_offset] = address
    rig.dma.values[base + 0x28] = length
    tx_before = bytes(rig.tx[:8])
    with pytest.raises(RuntimeError, match="not idle"):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    _assert_no_start_or_tx_write(rig, tx_before)


def test_error_status_is_rejected_before_first_transfer_exception():
    rig = Rig(driver_started=True)
    rig.dma.values[0x04] = 0x10
    tx_before = bytes(rig.tx[:8])
    with pytest.raises(RuntimeError, match="error bits"):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    _assert_no_start_or_tx_write(rig, tx_before)


@pytest.mark.parametrize("guard", [
    None,
    lambda *args: None,
    lambda *args: {"owned": False, "control": 1, "status": 0,
                   "address": 0, "length": 0},
])
def test_missing_or_unowned_first_transfer_proof_is_rejected(guard):
    rig = Rig(driver_started=True)
    tx_before = bytes(rig.tx[:8])
    with pytest.raises(RuntimeError, match="not idle"):
        transport(rig, first_transfer_guard=guard).transfer(
            b"\xff\xd8a\xff\xd9", 9)
    _assert_no_start_or_tx_write(rig, tx_before)


@pytest.mark.parametrize("retry_id", [10, 9])
def test_duplicate_or_decreasing_id_after_completion_is_rejected_before_start(retry_id):
    rig = Rig()
    t = transport(rig)
    assert t.transfer(b"\xff\xd8a\xff\xd9", 10).frame_id == 10
    starts_before = sum(e[1] == "transfer" for e in rig.events if e[0] in ("s2mm", "mm2s"))
    accepts_before = sum(e[:3] == ("preview", "write", 0x24) for e in rig.events)
    with pytest.raises(ValueError, match="strictly increase"):
        t.transfer(b"\xff\xd8b\xff\xd9", retry_id)
    assert sum(e[1] == "transfer" for e in rig.events if e[0] in ("s2mm", "mm2s")) == starts_before
    assert sum(e[:3] == ("preview", "write", 0x24) for e in rig.events) == accepts_before


def test_preflight_rejection_does_not_consume_frame_id():
    rig = Rig()
    rig.rle.values[0x08] = 1
    t = transport(rig)
    with pytest.raises(RuntimeError, match="RLE pipeline"):
        t.transfer(b"\xff\xd8a\xff\xd9", 9)
    rig.rle.values[0x08] = 0
    assert t.transfer(b"\xff\xd8b\xff\xd9", 9).frame_id == 9


def test_start_attempt_with_jpeg_error_consumes_id_for_retry():
    rig = Rig(jpeg_status=1)
    t = transport(rig)
    with pytest.raises(RuntimeError, match="JPEG status error"):
        t.transfer(b"\xff\xd8a\xff\xd9", 9)
    starts_after_failure = sum(e[1] == "transfer" for e in rig.events if e[0] in ("s2mm", "mm2s"))
    accepts_after_failure = sum(e[:3] == ("preview", "write", 0x24) for e in rig.events)
    with pytest.raises(ValueError, match="strictly increase"):
        t.transfer(b"\xff\xd8b\xff\xd9", 9)
    assert sum(e[1] == "transfer" for e in rig.events if e[0] in ("s2mm", "mm2s")) == starts_after_failure
    assert sum(e[:3] == ("preview", "write", 0x24) for e in rig.events) == accepts_after_failure == 0


def test_unconsumed_rle_done_on_first_call_is_rejected():
    rig = Rig()
    rig.rle.values[0x08] = 0x2
    with pytest.raises(RuntimeError, match="unconsumed prior frame"):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    assert not any(e[0] in ("s2mm", "mm2s") and e[1] == "transfer" for e in rig.events)


def test_latched_ap_done_allows_downstream_to_finish_later():
    rig = Rig(late_downstream=True)
    assert transport(rig, timeout_s=0.02).transfer(b"\xff\xd8a\xff\xd9", 9).frame_id == 9


def test_transient_busy_and_stale_frg_preview_ids_wait_for_current_frame():
    rig = Rig()
    original = rig.jpeg.on_write
    def arrange_transition(off, value):
        original(off, value)
        if off == 0 and value == 1:
            # Old frame ID makes its overflow/busy flags stale; the next poll
            # reports the requested frame with clean, completed FRG status.
            snapshots = iter(((8, 0x6, 8, 8), (8, 1, 9, 9)))
            accepted = False
            preview_write = rig.preview.on_write
            def note_accept(write_offset, write_value):
                nonlocal accepted
                preview_write(write_offset, write_value)
                if write_offset == 0x0024 and write_value == 1:
                    accepted = True
            rig.preview.on_write = note_accept
            def next_preview(offset):
                if offset == 0x0008 and not accepted:
                    status, state, frg_id, captured_id = next(snapshots, (8, 1, 9, 9))
                    rig.preview.values[0x0008] = status
                    rig.preview.values[0x0204] = state
                    rig.preview.values[0x0208] = frg_id
                    rig.preview.values[0x0018] = captured_id
            rig.preview.on_read = next_preview
    rig.jpeg.on_write = arrange_transition
    result = transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    assert result.frame_id == 9
    assert sum(e[:3] == ("preview", "write", 0x24) for e in rig.events) == 1


def test_persistent_frg_busy_times_out_without_mask_accept():
    rig = Rig()
    original = rig.jpeg.on_write
    def remain_busy(off, value):
        original(off, value)
        if off == 0 and value == 1:
            rig.preview.values[0x0204] = 0x4
    rig.jpeg.on_write = remain_busy
    with pytest.raises(TimeoutError, match="FRG or preview capture"):
        transport(rig, timeout_s=0.002).transfer(b"\xff\xd8a\xff\xd9", 9)
    assert not any(e[:3] == ("preview", "write", 0x24) for e in rig.events)


@pytest.mark.parametrize("jpeg", [b"", b"x\xd8x\xff\xd9", b"\xff\xd8x\xff", b"\xff\xd8\xff\xd9x"])
def test_rejects_empty_or_bad_jpeg_markers_before_start(jpeg):
    rig = Rig()
    with pytest.raises(ValueError): transport(rig).transfer(jpeg, 1)
    assert not any(e[0] in ("s2mm", "mm2s") and e[1] == "transfer" for e in rig.events)


def test_accepts_exact_512000_boundary_and_rejects_overflow():
    jpeg = b"\xff\xd8" + b"x" * (JPEG_MAX_BYTES - 4) + b"\xff\xd9"
    rig = Rig(); transport(rig).transfer(jpeg, 10)
    assert ("mm2s", "transfer", JPEG_MAX_BYTES) in rig.events
    rig = Rig()
    with pytest.raises(ValueError): transport(rig).transfer(b"\xff\xd8" + b"x" * (JPEG_MAX_BYTES - 2) + b"\xff\xd9", 10)


@pytest.mark.parametrize("mutate", ["tx-align", "rx-align", "overlap", "address-mismatch", "tx-size"])
def test_rejects_bad_dma_buffer_spans(mutate):
    rig = Rig()
    if mutate == "tx-align": rig.tx.physical_address += 1; rig.tx.device_address += 1
    if mutate == "rx-align": rig.rx.physical_address += 1; rig.rx.device_address += 1
    if mutate == "overlap": rig.rx.physical_address = rig.rx.device_address = rig.tx.physical_address + 8
    if mutate == "address-mismatch": rig.tx.device_address += 8
    if mutate == "tx-size": rig.tx.nbytes -= 1
    with pytest.raises(ValueError): transport(rig).transfer(b"\xff\xd8a\xff\xd9", 2)


@pytest.mark.parametrize("jpeg_status", [1, 0x100])
def test_nonzero_jpeg_status_never_accepts(jpeg_status):
    rig = Rig(jpeg_status=jpeg_status)
    with pytest.raises(RuntimeError, match="JPEG"):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    assert not any(e[:3] == ("preview", "write", 0x24) for e in rig.events)


@pytest.mark.parametrize("frg,frg_id,preview,captured_id,error", [
    (0, 9, 8, 9, TimeoutError), (3, 9, 8, 9, RuntimeError),
    (5, 9, 8, 9, TimeoutError), (1, 8, 8, 9, TimeoutError),
    (1, 9, 0, 9, TimeoutError), (1, 9, 8, 8, TimeoutError),
])
def test_rejects_invalid_frg_or_preview_identity(frg, frg_id, preview, captured_id, error):
    rig = Rig()
    original = rig.jpeg.on_write
    def inject(off, value):
        original(off, value)
        if off == 0 and value == 1:
            rig.preview.values[0x0204] = frg
            rig.preview.values[0x0208] = frg_id
            rig.preview.values[0x0008] = preview
            rig.preview.values[0x0018] = captured_id
    rig.jpeg.on_write = inject
    with pytest.raises(error):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    assert not any(e[:3] == ("preview", "write", 0x24) for e in rig.events)


def test_dropped_counter_change_blocks_accept():
    rig = Rig(drop=3)
    old = rig.jpeg.on_write
    def changed_drop(off, val):
        old(off, val)
        if off == 0 and val == 1: rig.preview.values[0x0214] = 4
    rig.jpeg.on_write = changed_drop
    with pytest.raises(RuntimeError): transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)


def test_dropped_counter_change_rejects_even_while_frg_id_is_stale():
    rig = Rig(drop=3)
    original = rig.jpeg.on_write
    def stale_frame_with_drop(off, value):
        original(off, value)
        if off == 0 and value == 1:
            rig.preview.values[0x0208] = 8
            rig.preview.values[0x0214] = 4
    rig.jpeg.on_write = stale_frame_with_drop
    with pytest.raises(RuntimeError, match="dropped counter changed"):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    assert not any(e[:3] == ("preview", "write", 0x24) for e in rig.events)


def test_malformed_or_wrong_identity_rle_never_accepts():
    packet = bytearray(encode_mask(np.zeros((HEIGHT, WIDTH), np.uint8), frame_id=8))
    rig = Rig(rle_packet=packet)
    with pytest.raises((ValueError, RuntimeError)): transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    assert not any(e[:3] == ("preview", "write", 0x24) for e in rig.events)


def test_malformed_rle_and_dma_error_never_accept():
    rig = Rig(rle_packet=b"malformed")
    with pytest.raises((ValueError, RuntimeError)):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    rig = Rig(dma_error=True)
    with pytest.raises(RuntimeError, match="DMA error"):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)


def test_rle_overflow_status_never_accepts():
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[:, ::2] = 255
    packet = encode_mask(mask, frame_id=9, max_runs=1)
    rig = Rig(rle_packet=packet)
    with pytest.raises((ValueError, RuntimeError)):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    assert not any(e[:3] == ("preview", "write", 0x24) for e in rig.events)


def test_displayed_id_mismatch_after_accept_is_reported():
    rig = Rig()
    original = rig.preview.on_write
    def wrong_display(off, value):
        original(off, value)
        if off == 0x24: rig.preview.values[0x001c] = value + 1
    rig.preview.on_write = wrong_display
    with pytest.raises(TimeoutError, match="preview mask display"):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)


@pytest.mark.parametrize("offset", [1.5, True, "1"])
def test_rejects_non_integral_or_bool_intensity_offset(offset):
    rig = Rig()
    with pytest.raises(TypeError, match="intensity_offset"):
        transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9, intensity_offset=offset)
    assert not any(e[0] in ("s2mm", "mm2s") and e[1] == "transfer" for e in rig.events)


def test_old_displayed_id_waits_until_new_frame_is_displayed():
    rig = Rig()
    original = rig.preview.on_write
    def delay_display(off, value):
        original(off, value)
        if off == 0x24 and value == 1:
            rig.preview.values[0x001c] = 8
            reads = 0
            def advance(offset):
                nonlocal reads
                if offset == 0x0008:
                    reads += 1
                    if reads >= 2:
                        rig.preview.values[0x001c] = 9
            rig.preview.on_read = advance
    rig.preview.on_write = delay_display
    result = transport(rig).transfer(b"\xff\xd8a\xff\xd9", 9)
    assert result.frame_id == 9


def test_start_timeout_holds_buffers_and_forbids_retry_or_close():
    rig = Rig(auto_complete=False)
    t = transport(rig, timeout_s=0.001)
    with pytest.raises(TimeoutError): t.transfer(b"\xff\xd8a\xff\xd9", 9)
    with pytest.raises(RuntimeError, match="ownership"): t.transfer(b"\xff\xd8a\xff\xd9", 10)
    with pytest.raises(RuntimeError, match="DMA-owned"): t.close()
    assert rig.tx.freed == rig.rx.freed == 0


def test_driver_started_timeout_holds_buffers_and_forbids_retry_or_close():
    rig = Rig(driver_started=True, auto_complete=False)
    t = transport(rig, timeout_s=0.001)
    with pytest.raises(TimeoutError): t.transfer(b"\xff\xd8a\xff\xd9", 9)
    with pytest.raises(RuntimeError, match="ownership"): t.transfer(b"\xff\xd8a\xff\xd9", 10)
    with pytest.raises(RuntimeError, match="DMA-owned"): t.close()
    assert rig.tx.freed == rig.rx.freed == 0


def test_raw_camera_configures_mjpg_size_and_never_decodes(monkeypatch):
    import cv2
    payload = _baseline_header()
    class Capture:
        def __init__(self, *args): self.props = {}; self.i = 0; self.released = False
        def set(self, key, val): self.props[key] = val; return True
        def get(self, key): return self.props.get(key, 0)
        def isOpened(self): return True
        def read(self):
            self.i += 1
            return (True, np.frombuffer(payload, np.uint8)) if self.i == 1 else (False, None)
        def release(self): self.released = True
    capture = Capture()
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: capture)
    monkeypatch.setattr(cv2, "imdecode", lambda *args: pytest.fail("software JPEG decode called"))
    frames = list(iter_raw_mjpeg(0, cv2_module=cv2))
    assert frames == [payload]
    assert capture.props[cv2.CAP_PROP_FRAME_WIDTH] == WIDTH
    assert capture.props[cv2.CAP_PROP_FRAME_HEIGHT] == HEIGHT
    assert capture.props[cv2.CAP_PROP_CONVERT_RGB] == 0
    assert capture.released


@pytest.mark.parametrize("jpeg", [b"decoded pixels", b"\xff\xd8missing-eoi", b"missing-soi\xff\xd9"])
def test_raw_camera_rejects_non_jpeg_or_truncated_packet(monkeypatch, jpeg):
    import cv2
    class Capture:
        def __init__(self, *args): self.props = {}
        def set(self, key, val): self.props[key] = val; return True
        def get(self, key): return self.props.get(key, 0)
        def isOpened(self): return True
        def read(self): return True, np.frombuffer(jpeg, np.uint8)
        def release(self): pass
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: Capture())

    with pytest.raises(RuntimeError, match="complete JPEG"):
        next(iter_raw_mjpeg(0, cv2_module=cv2))


def test_raw_camera_rejects_negotiated_geometry_mismatch(monkeypatch):
    import cv2
    class Capture:
        def __init__(self, *args): self.props = {}
        def set(self, key, val): self.props[key] = val; return True
        def get(self, key):
            return 320 if key == cv2.CAP_PROP_FRAME_WIDTH else self.props.get(key, 0)
        def isOpened(self): return True
        def read(self): return True, np.frombuffer(b"\xff\xd8x\xff\xd9", np.uint8)
        def release(self): pass
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: Capture())

    with pytest.raises(RuntimeError, match="expected MJPG 640x400"):
        next(iter_raw_mjpeg(0, cv2_module=cv2))
