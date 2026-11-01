import threading
import time
import types
import uuid

import numpy as np
import pytest

from scripts.standalone.driver_owner import DriverOwner
from scripts.standalone.integrated_run import resolve_display_held
from scripts.standalone.vdma_diag import SCHEMA as VDMA_SNAPSHOT_SCHEMA
from scripts.standalone.video_pump import (
    DISPLAY_FAIL_HOLD, RUNNING, STOPPED, VideoPump,
)


class FakeHDMI:
    def __init__(self, *, fail_newframe=None, fail_writeframe=None):
        self.fail_newframe = fail_newframe
        self.fail_writeframe = fail_writeframe
        self.submitted = []

    def newframe(self):
        if self.fail_newframe:
            raise RuntimeError(self.fail_newframe)
        return np.zeros((4, 6), dtype=np.uint8)

    def writeframe(self, frame):
        if self.fail_writeframe:
            raise RuntimeError(self.fail_writeframe)
        self.submitted.append(frame)


class FakeLease:
    def __init__(self, identity, slot=0):
        self.session_uuid = identity
        self.frame_id = 7
        self.array = np.zeros((4, 6), dtype=np.uint8)
        self.slot = slot
        self.scale_ns = 1_000_000
        self.draw_ns = 2_000_000


class FakeOutputs:
    def __init__(self, identity, *, fail_release=None):
        self.identity = identity
        self.fail_release = fail_release
        self.claimed = False
        self.released = []

    def acquire_latest(self):
        if self.claimed:
            return None
        self.claimed = True
        return FakeLease(self.identity)

    def release_read(self, slot):
        self.released.append(slot)
        if self.fail_release:
            raise RuntimeError(self.fail_release)


class FakeOwner:
    def __init__(self):
        self.retained = []
        self.calls = []

    def call(self, function, *args, timeout=2.0):
        self.calls.append((getattr(function, "__name__", str(function)), timeout))
        return function(*args)

    def retain(self, resource):
        self.retained.append(resource)
        return resource


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _pump(hdmi, outputs, owner, identity):
    return VideoPump(outputs, types.SimpleNamespace(hdmi=hdmi), owner, identity)


def test_writeframe_failure_records_stage_error_and_frame_id():
    identity = uuid.uuid4()
    hdmi = FakeHDMI(fail_writeframe="VDMA stalled")
    outputs = FakeOutputs(identity)
    pump = _pump(hdmi, outputs, FakeOwner(), identity)
    assert _wait_for(lambda: pump.state == DISPLAY_FAIL_HOLD)
    status = pump.status()
    assert status["failed_stage"] == "writeframe"
    assert "VDMA stalled" in status["error"]
    assert status["failed_at"] is not None
    assert status["last_frame_id"] == 7
    assert status["submitted"] == 0
    assert status["alive"] is False


def test_newframe_failure_records_newframe_stage():
    identity = uuid.uuid4()
    hdmi = FakeHDMI(fail_newframe="CMA allocate failed")
    outputs = FakeOutputs(identity)
    pump = _pump(hdmi, outputs, FakeOwner(), identity)
    assert _wait_for(lambda: pump.state == DISPLAY_FAIL_HOLD)
    status = pump.status()
    assert status["failed_stage"] == "newframe"
    assert "CMA allocate failed" in status["error"]


def test_first_failure_is_preserved_when_release_also_fails():
    identity = uuid.uuid4()
    hdmi = FakeHDMI(fail_writeframe="writeframe first")
    outputs = FakeOutputs(identity, fail_release="release second")
    pump = _pump(hdmi, outputs, FakeOwner(), identity)
    assert _wait_for(lambda: pump.state == DISPLAY_FAIL_HOLD)
    status = pump.status()
    assert status["failed_stage"] == "writeframe"
    assert "writeframe first" in status["error"]
    assert "release second" not in status["error"]


def test_normal_stop_records_stopped_without_error():
    identity = uuid.uuid4()
    hdmi = FakeHDMI()
    outputs = FakeOutputs(identity)
    pump = _pump(hdmi, outputs, FakeOwner(), identity)
    assert _wait_for(lambda: pump.submitted >= 1)
    pump.request_stop()
    assert pump.join(3.0)
    status = pump.status()
    assert status["state"] == STOPPED
    assert status["error"] is None
    assert status["failed_stage"] is None
    assert status["submitted"] >= 1


def test_submit_timeout_default_is_not_extended():
    assert VideoPump.DEFAULT_SUBMIT_TIMEOUT == 1.0
    identity = uuid.uuid4()
    pump = _pump(FakeHDMI(), FakeOutputs(identity), FakeOwner(), identity)
    assert pump.submit_timeout == 1.0
    pump.request_stop()
    pump.join(3.0)


@pytest.mark.parametrize(
    "pre,join_ok,post,expected",
    [
        (DISPLAY_FAIL_HOLD, True, DISPLAY_FAIL_HOLD,
         (True, "pre_existing_display_fail_hold")),
        (DISPLAY_FAIL_HOLD, False, DISPLAY_FAIL_HOLD,
         (True, "pre_existing_display_fail_hold")),
        (RUNNING, False, RUNNING, (True, "video_pump_join_timeout")),
        (RUNNING, True, DISPLAY_FAIL_HOLD,
         (True, "video_pump_display_fail_hold")),
        (RUNNING, True, STOPPED, (False, None)),
    ],
)
def test_resolve_display_held_classifies_the_cleanup_reason(pre, join_ok, post, expected):
    assert resolve_display_held(pre, join_ok, post) == expected


STALLED_MM2S = {
    0x00: 0x00011001,      # RS | FrmCnt_IrqEn | IRQFrameCount=1
    0x04: 0x00000000,      # no FrmCnt_Irq, no halt, no error
    0x28: (3 << 16) | 2,   # RdFrmStore stays on 3 in both samples
    0x2C: 0x62000000,
    0x50: 720,
    0x54: 1280,
    0x58: 1280,
    0x5C: 0x1E000000,
    0x60: 0x1E200000,
    0x64: 0x1E400000,
    0x68: 0x1E600000,
}


def _blocked_writeframe():
    started = threading.Event()
    release = threading.Event()

    def write(frame):
        started.set()
        release.wait(2)

    return started, release, write


def test_writeframe_timeout_records_owner_timing_without_mmio():
    identity = uuid.uuid4()
    started, release, write = _blocked_writeframe()
    hdmi = FakeHDMI()
    hdmi.writeframe = write
    owner = DriverOwner()
    pump = VideoPump(FakeOutputs(identity), types.SimpleNamespace(hdmi=hdmi),
                     owner, identity, submit_timeout=0.03)
    try:
        assert started.wait(1)
        assert pump.join(1)
        diag = pump.diagnostics()
        failure = diag["first_failure"]
        assert failure["stage"] == "writeframe"
        assert "exceeded" in failure["error"]
        assert failure["writeframe_submitted_at"] is not None
        assert failure["writeframe_returned_at"] is None
        assert failure["owner"]["operation"].endswith("write")
        assert failure["owner"]["started_at"] >= failure["owner"]["submitted_at"]
        assert failure["owner"]["completed_at"] is None
        assert diag["last_success"] is None
        assert diag["vdma_capture_enabled"] is False
        assert diag["vdma_snapshot"] is None
        assert pump.status()["state"] == DISPLAY_FAIL_HOLD
        # The owner thread keeps executing the timed-out operation afterwards.
        release.set()
        assert _wait_for(lambda: owner.diagnostics()["completed_at"] is not None)
        after = owner.diagnostics()
        assert after["state"] == "FAIL_HOLD"
        # The timed-out writeframe is never reported as a successful job.
        assert after["last_success"]["operation"].endswith("newframe")
    finally:
        release.set()


def test_failure_snapshot_is_bounded_and_keeps_the_first_error():
    identity = uuid.uuid4()
    started, release, write = _blocked_writeframe()
    hdmi = FakeHDMI()
    hdmi.writeframe = write

    def broken_read(offset):
        raise OSError("mmio unavailable")

    pump = VideoPump(FakeOutputs(identity), types.SimpleNamespace(hdmi=hdmi),
                     DriverOwner(), identity, submit_timeout=0.03,
                     vdma_read32=broken_read)
    try:
        assert started.wait(1)
        assert pump.join(1)
        diag = pump.diagnostics()
        snapshot = diag["vdma_snapshot"]
        assert snapshot["schema"] == VDMA_SNAPSHOT_SCHEMA
        assert len(snapshot["samples"]) == 2
        assert len(snapshot["read_errors"]) == 2 * 11
        assert "mmio unavailable" not in pump.error
        assert diag["first_failure"]["error"] == pump.error
        assert diag["first_failure"]["stage"] == "writeframe"
    finally:
        release.set()


def test_stalled_frame_count_snapshot_shows_no_irq_and_no_store_progress():
    identity = uuid.uuid4()
    started, release, write = _blocked_writeframe()
    hdmi = FakeHDMI()
    hdmi.writeframe = write
    pump = VideoPump(FakeOutputs(identity), types.SimpleNamespace(hdmi=hdmi),
                     DriverOwner(), identity, submit_timeout=0.03,
                     vdma_read32=lambda offset: STALLED_MM2S[offset])
    try:
        assert started.wait(1)
        assert pump.join(1)
        snapshot = pump.diagnostics()["vdma_snapshot"]
        decoded = snapshot["decoded"]
        assert decoded["running"] is True
        assert decoded["halted"] is False
        assert decoded["frmcnt_irq"] is False
        assert decoded["frmcnt_irq_observed"] is False
        assert decoded["rd_frm_store"] == 3
        assert decoded["rd_frm_store_moved"] is False
        assert snapshot["read_errors"] == []
    finally:
        release.set()


def test_normal_frames_never_read_vdma_registers():
    identity = uuid.uuid4()
    reads = []

    def read32(offset):
        reads.append(offset)
        return 0

    pump = VideoPump(FakeOutputs(identity), types.SimpleNamespace(hdmi=FakeHDMI()),
                     FakeOwner(), identity, vdma_read32=read32)
    assert _wait_for(lambda: pump.submitted >= 1)
    pump.request_stop()
    assert pump.join(3.0)
    assert reads == []
    assert pump.diagnostics()["vdma_snapshot"] is None


def test_last_success_records_frame_and_owner_timing():
    identity = uuid.uuid4()
    pump = VideoPump(FakeOutputs(identity), types.SimpleNamespace(hdmi=FakeHDMI()),
                     DriverOwner(), identity)
    assert _wait_for(lambda: pump.submitted >= 1)
    pump.request_stop()
    assert pump.join(3.0)
    success = pump.diagnostics()["last_success"]
    assert success["frame_id"] == 7
    assert success["submitted"] == 1
    assert success["returned_at"] >= success["submitted_at"]
    assert success["writeframe_ms"] >= 0
    assert success["owner"]["operation"].endswith("writeframe")
    assert (success["owner"]["submitted_at"] <= success["owner"]["started_at"]
            <= success["owner"]["completed_at"])