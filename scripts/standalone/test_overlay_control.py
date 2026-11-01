import threading
import types

import pytest

from scripts.standalone.overlay_control import (
    ALLOWED_OFFSETS, ID_MCO1, OverlayControl, OverlayControlError,
)


class FakeMmio:
    def __init__(self, values=None, frame_step=1):
        self.values = dict(values or {
            0x04: ID_MCO1, 0x00: 0x1, 0x0C: 0x2, 0x1C: 0x0, 0x08: 10,
        })
        self.frame_step = frame_step
        self.reads = []
        self.write_log = []
        self.fail_read = False
        self.fail_write = False
        self.write_value_override = None

    def read(self, offset):
        if self.fail_read:
            raise RuntimeError("AXI read failed")
        self.reads.append(offset)
        if offset == 0x08 and self.frame_step and self.reads.count(0x08) > 1:
            self.values[0x08] = (self.values.get(0x08, 0) + self.frame_step) & 0xFFFFFFFF
        return self.values.get(offset, 0)

    def write(self, offset, value):
        if self.fail_write:
            raise RuntimeError("AXI write failed")
        self.write_log.append((offset, value))
        self.values[offset] = (value if self.write_value_override is None
                               else self.write_value_override)


class FakeOwner:
    def __init__(self):
        self.calls = []
        self.threads = set()

    def call(self, function, *args, timeout=2.0):
        self.threads.add(threading.get_ident())
        self.calls.append((getattr(function, "__name__", str(function)), args))
        return function(*args)


def _overlay(mmio, *, nested=True):
    ip = types.SimpleNamespace(mmio=mmio)
    if nested:
        return types.SimpleNamespace(hdmi_out=types.SimpleNamespace(axis_gray_overlay=ip))
    return types.SimpleNamespace(hdmi_out=types.SimpleNamespace(),
                                 hdmi_out_axis_gray_overlay=ip)


def _control(mmio=None, owner=None, **kwargs):
    mmio = mmio or FakeMmio()
    owner = owner or FakeOwner()
    kwargs.setdefault("sof_timeout_s", 0.2)
    kwargs.setdefault("frame_timeout_s", 0.2)
    kwargs.setdefault("poll_s", 0.001)
    return OverlayControl(_overlay(mmio), owner, **kwargs), mmio, owner


def test_preflight_reads_required_registers_and_writes_nothing():
    control, mmio, owner = _control()
    report = control.preflight()
    assert report["identity"] == ID_MCO1
    assert report["ctrl"] == 1
    assert report["status"] & 0x2
    assert report["line_thick"] == 0
    assert report["frame_count_after"] > report["frame_count_before"]
    assert mmio.write_log == []
    assert len(owner.calls) == len(mmio.reads)


def test_preflight_rejects_id_mismatch():
    mmio = FakeMmio({0x04: 0x12345678, 0x00: 1, 0x0C: 2, 0x1C: 0, 0x08: 1})
    control, _, _ = _control(mmio)
    with pytest.raises(OverlayControlError, match="ID"):
        control.preflight()
    assert mmio.write_log == []


def test_preflight_rejects_non_bypass_ctrl():
    mmio = FakeMmio({0x04: ID_MCO1, 0x00: 0x2, 0x0C: 2, 0x1C: 0, 0x08: 1})
    control, _, _ = _control(mmio)
    with pytest.raises(OverlayControlError, match="bypass"):
        control.preflight()
    assert mmio.write_log == []


def test_preflight_rejects_coord_error():
    mmio = FakeMmio({0x04: ID_MCO1, 0x00: 1, 0x0C: 0x6, 0x1C: 0, 0x08: 1})
    control, _, _ = _control(mmio)
    with pytest.raises(OverlayControlError, match="coord_error"):
        control.preflight()
    assert mmio.write_log == []


def test_preflight_rejects_nonzero_line_thick():
    mmio = FakeMmio({0x04: ID_MCO1, 0x00: 1, 0x0C: 2, 0x1C: 3, 0x08: 1})
    control, _, _ = _control(mmio)
    with pytest.raises(OverlayControlError, match="LINE_THICK"):
        control.preflight()
    assert mmio.write_log == []


def test_preflight_rejects_stalled_frame_counter():
    mmio = FakeMmio({0x04: ID_MCO1, 0x00: 1, 0x0C: 2, 0x1C: 0, 0x08: 7},
                    frame_step=0)
    control, _, _ = _control(mmio)
    with pytest.raises(OverlayControlError, match="did not advance"):
        control.preflight()
    assert mmio.write_log == []


def test_first_write_is_the_only_permitted_write():
    control, mmio, owner = _control()
    report = control.first_write()
    assert mmio.write_log == [(0x00, 0x00000000)]
    assert report["ctrl"] == 0
    assert report["writes"] == 1
    assert len(owner.calls) == len(mmio.reads) + len(mmio.write_log)


def test_first_write_cannot_run_twice():
    control, mmio, _ = _control()
    control.first_write()
    with pytest.raises(OverlayControlError, match="only once"):
        control.first_write()
    assert mmio.write_log == [(0x00, 0x00000000)]


def test_write_allowlist_rejects_other_registers():
    control, mmio, owner = _control()
    before = len(owner.calls)
    with pytest.raises(OverlayControlError, match="not permitted"):
        control.write(0x10, 1)
    assert mmio.write_log == []
    assert len(owner.calls) == before


def test_offset_whitelist_rejects_aliases_and_outside_map():
    control, mmio, _ = _control()
    with pytest.raises(OverlayControlError, match="documented"):
        control.read(0x60)
    with pytest.raises(OverlayControlError, match="documented"):
        control.read(0x100)
    with pytest.raises(OverlayControlError, match="documented"):
        control.write(0x5E, 0)
    assert mmio.reads == []
    assert mmio.write_log == []


def test_mmio_failure_is_not_retried():
    mmio = FakeMmio()
    mmio.fail_write = True
    control, _, owner = _control(mmio)
    write_calls = []
    real_call = owner.call

    def tracking_call(function, *args, timeout=2.0):
        if getattr(function, "__name__", "") == "write":
            write_calls.append(args)
        return real_call(function, *args, timeout=timeout)

    owner.call = tracking_call
    with pytest.raises(OverlayControlError, match="MMIO operation failed"):
        control.first_write()
    assert len(write_calls) == 1
    assert mmio.write_log == []


def test_readback_mismatch_after_write_does_not_retry():
    mmio = FakeMmio()
    mmio.write_value_override = 0x1
    control, _, _ = _control(mmio)
    with pytest.raises(OverlayControlError, match="readback"):
        control.first_write()
    assert mmio.write_log == [(0x00, 0x00000000)]


def test_resolves_flat_instance_name():
    mmio = FakeMmio()
    control = OverlayControl(_overlay(mmio, nested=False), FakeOwner(),
                             sof_timeout_s=0.2, frame_timeout_s=0.2, poll_s=0.001)
    assert control.preflight()["identity"] == ID_MCO1


def test_missing_overlay_mmio_is_rejected():
    overlay = types.SimpleNamespace(hdmi_out=types.SimpleNamespace())
    with pytest.raises(OverlayControlError, match="lacks"):
        OverlayControl(overlay, FakeOwner())


def test_allowed_offsets_cover_only_documented_map():
    assert 0x00 in ALLOWED_OFFSETS
    assert 0x5C in ALLOWED_OFFSETS
    assert 0x60 not in ALLOWED_OFFSETS
    assert all(offset % 4 == 0 for offset in ALLOWED_OFFSETS)