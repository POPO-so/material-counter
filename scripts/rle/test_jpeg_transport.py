import numpy as np
import pytest

from scripts.rle.protocol import HEIGHT, MAX_RUNS, RX_CAPACITY, WIDTH, encode_mask
from scripts.rle.jpeg_transport import (
    JPEG_MAX_BYTES, JpegRleDmaTransport, iter_raw_mjpeg,
)


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
        if getattr(self, "rig", None) and self.rig.dma_error:
            self.rig.dma.values[0x04 if self.name == "mm2s" else 0x34] = 0x10
    def wait(self):
        self.events.append((self.name, "wait")); self.wait_count += 1


class Rig:
    def __init__(self, jpeg=b"\xff\xd8raw\xff\xd9", *, jpeg_status=0,
                 rle_packet=None, frg=1, preview=1 << 3, drop=0,
                 dma_error=False, auto_complete=True, late_downstream=False):
        self.events = []
        self.tx, self.rx = Buffer(JPEG_MAX_BYTES, 0x11000000), Buffer(RX_CAPACITY, 0x12000000)
        self.send, self.recv = Channel("mm2s", self.events), Channel("s2mm", self.events)
        self.send.rig = self.recv.rig = self
        self.dma = Mmio({0x04: 0x1002, 0x34: 0x1002, 0x18: 0, 0x28: 0,
                         0x48: 0, 0x58: 0}, self.events, "dma")
        self.rle = Mmio({0x04: 0x31454C52, 0x08: 0, 0x0c: 0,
                         0x10: 0, 0x14: 0, 0x18: 0, 0x1c: 0,
                         0x20: 128, 0x24: 0, 0x28: 0,
                         0x2c: (HEIGHT << 16) | WIDTH, 0x30: MAX_RUNS}, self.events, "rle")
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


def transport(rig, timeout_s=0.02):
    return JpegRleDmaTransport(rig.send, rig.recv, rig.dma, rig.rle,
        rig.jpeg, rig.preview, rig.tx, rig.rx, timeout_s=timeout_s)


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


def test_allows_pynq_first_channel_start_before_idle_status_is_asserted():
    rig = Rig()
    rig.dma.values[0x04] = rig.dma.values[0x34] = 0
    for channel in (rig.send, rig.recv):
        channel._first_transfer = True
        channel._active_buffer = None
        channel.running = True

    frame = transport(rig).transfer(b"\xff\xd8abc\xff\xd9", 9)

    assert frame.frame_id == 9


def test_deferred_mask_accept_waits_for_same_frame_count_and_boxes_commit():
    rig = Rig(preview=(1 << 3) | 1)
    t = transport(rig)
    frame = t.transfer(b"\xff\xd8abc\xff\xd9", 9, auto_accept=False)
    assert frame.frame_id == 9
    assert not any(e[:3] == ("preview", "write", 0x24) for e in rig.events)

    t.publish_overlay(9, 8, [(10, 20, 19, 29)], line_x=321)

    assert ("preview", "write", 0x44, 8) in rig.events
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
        t.publish_overlay(10, 1, [], line_x=320)
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


def test_repeated_call_accepts_prior_completed_rle_and_idle_jpeg_state():
    rig = Rig()
    t = transport(rig)
    assert t.transfer(b"\xff\xd8a\xff\xd9", 9).frame_id == 9
    assert t.transfer(b"\xff\xd8b\xff\xd9", 10).frame_id == 10


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


def test_raw_camera_configures_mjpg_size_and_never_decodes(monkeypatch):
    import cv2
    class Capture:
        def __init__(self, *args): self.props = {}; self.i = 0; self.released = False
        def set(self, key, val): self.props[key] = val; return True
        def get(self, key): return self.props.get(key, 0)
        def isOpened(self): return True
        def read(self):
            self.i += 1
            return (True, np.frombuffer(b"\xff\xd8x\xff\xd9", np.uint8)) if self.i == 1 else (False, None)
        def release(self): self.released = True
    capture = Capture()
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: capture)
    monkeypatch.setattr(cv2, "imdecode", lambda *args: pytest.fail("software JPEG decode called"))
    frames = list(iter_raw_mjpeg(0, cv2_module=cv2))
    assert frames == [b"\xff\xd8x\xff\xd9"]
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
