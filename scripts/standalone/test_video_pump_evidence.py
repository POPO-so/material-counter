import threading
import time
import types
import uuid

import numpy as np
import pytest

from scripts.standalone.integrated_run import resolve_display_held
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