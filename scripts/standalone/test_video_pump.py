import multiprocessing as mp
import threading
import time
from types import SimpleNamespace
import uuid

import numpy as np
import pytest

from scripts.standalone.shared_frames import FREE, SharedFrames
from scripts.standalone.driver_owner import DriverOwner
from scripts.standalone.video_telemetry import TelemetryWindow


try:
    from scripts.standalone import video_pump as video_pump_module
    from scripts.standalone.video_pump import DISPLAY_FAIL_HOLD, RUNNING, STOPPED, VideoPump
except ModuleNotFoundError:
    video_pump_module = None
    VideoPump = None
    DISPLAY_FAIL_HOLD = "DISPLAY_FAIL_HOLD"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"


class FakeHDMI:
    def __init__(self, shape):
        self.shape = shape
        self.created = []
        self.submitted = []

    def newframe(self):
        frame = np.zeros(self.shape, dtype=np.uint8)
        self.created.append(frame)
        return frame

    def writeframe(self, frame):
        self.submitted.append(frame)


class FakeOwner:
    def __init__(self):
        self.retained = []
        self.calls = []

    def call(self, function, *args, timeout=2.0):
        self.calls.append((function, args, timeout))
        return function(*args)

    def retain(self, resource):
        self.retained.append(resource)
        return resource


@pytest.fixture
def frames():
    value = SharedFrames.create(ctx=mp.get_context("spawn"), shape=(8, 12))
    yield value
    if not value.closed:
        assert value.statuses() == (FREE, FREE, FREE)
        value.close()


def _publish(frames, identity, pixels, *, scale_ns=2_000_000, draw_ns=3_000_000,
             capture_time_ns=0):
    slot, target = frames.claim_write()
    target[:] = pixels
    frames.publish(slot, identity, 1, scale_ns=scale_ns, draw_ns=draw_ns,
                   capture_time_ns=capture_time_ns)


def _pump_type():
    if VideoPump is None:
        pytest.fail("VideoPump is not implemented")
    return VideoPump


def test_success_records_submit_latency_and_no_failure_or_skip(frames):
    pump_type = _pump_type()
    identity = uuid.uuid4()
    hdmi = FakeHDMI((8, 12))
    owner = FakeOwner()
    _publish(frames, identity, 117)

    pump = pump_type(frames, type("HW", (), {"hdmi": hdmi})(), owner, identity)
    try:
        deadline = time.monotonic() + 1
        while pump.submitted == 0 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert pump.submitted == 1
        assert pump.submit_failures == 0
        assert pump.skipped == 0
        assert list(pump.submit_latency_ms)
        assert len(hdmi.created) == len(hdmi.submitted) == 1
        assert hdmi.submitted[0] is hdmi.created[0]
        assert np.all(hdmi.submitted[0] == 117)
        assert [call[0] for call in owner.calls] == [hdmi.newframe, hdmi.writeframe]
        assert owner.calls[1][2] == pump_type.DEFAULT_SUBMIT_TIMEOUT
        assert list(pump.timings_ms["copy_submit_ms"])
        assert list(pump.timings_ms["scale_ms"]) == [2.0]
        assert list(pump.timings_ms["draw_ms"]) == [3.0]
    finally:
        pump.request_stop()
        assert pump.join(1)
    assert frames.statuses() == (FREE, FREE, FREE)
    assert pump.state == STOPPED


def test_request_stop_does_not_wait_for_blocked_writeframe(frames):
    pump_type = _pump_type()
    identity = uuid.uuid4()
    started = threading.Event()
    release = threading.Event()
    hdmi = FakeHDMI((8, 12))

    def blocked_write(frame):
        started.set()
        release.wait(2)

    hdmi.writeframe = blocked_write
    owner = FakeOwner()
    _publish(frames, identity, 42)
    pump = pump_type(frames, type("HW", (), {"hdmi": hdmi})(), owner, identity)
    try:
        assert started.wait(1)
        before = time.monotonic()
        pump.request_stop()
        assert time.monotonic() - before < 0.05
        assert pump.state == RUNNING
        assert not pump.join(0.01)
    finally:
        release.set()
    assert pump.join(1)
    assert pump.state == STOPPED
    assert frames.statuses() == (FREE, FREE, FREE)


def test_submit_timeout_counts_failure_not_skip(frames):
    pump_type = _pump_type()
    identity = uuid.uuid4()
    started = threading.Event()
    never_release = threading.Event()
    hdmi = FakeHDMI((8, 12))

    def timed_out_write(frame):
        started.set()
        never_release.wait(1)

    hdmi.writeframe = timed_out_write
    owner = DriverOwner()
    _publish(frames, identity, 231)
    pump = pump_type(frames, type("HW", (), {"hdmi": hdmi})(), owner,
                     identity, submit_timeout=0.03)
    try:
        assert started.wait(1)
        assert pump.join(1)
        assert pump.state == DISPLAY_FAIL_HOLD
        assert "exceeded" in pump.error.lower() or "timeout" in pump.error.lower()
        assert len(owner.retained) == 1
        assert owner.retained[0] is hdmi.created[0]
        assert pump.submitted == 0
        assert pump.submit_failures == 1
        assert pump.skipped == 0
        assert frames.statuses() == (FREE, FREE, FREE)
    finally:
        never_release.set()


def test_mismatched_session_counts_skip_not_submit_failure(frames):
    pump_type = _pump_type()
    identity = uuid.uuid4()
    _publish(frames, uuid.uuid4(), 9)
    hdmi = FakeHDMI((8, 12))
    owner = FakeOwner()
    pump = pump_type(frames, type("HW", (), {"hdmi": hdmi})(), owner, identity)
    try:
        assert pump.join(1)
        assert pump.state == DISPLAY_FAIL_HOLD
        assert "session" in pump.error.lower()
        assert pump.skipped == 1
        assert pump.submit_failures == 0
        assert owner.calls == []
        assert frames.statuses() == (FREE, FREE, FREE)
    finally:
        pump.request_stop()


def test_success_records_capture_to_submit_telemetry(frames, monkeypatch):
    class RecordingTelemetry(TelemetryWindow):
        def __init__(self):
            super().__init__(window_s=5)
            self.recorded = []

        def record_frame(self, frame):
            self.recorded.append(frame)
            super().record_frame(frame)

    pump_type = _pump_type()
    identity = uuid.uuid4()
    hdmi = FakeHDMI((8, 12))
    owner = FakeOwner()
    telemetry = RecordingTelemetry()
    capture_time_ns = time.monotonic_ns()
    _publish(frames, identity, 71, capture_time_ns=capture_time_ns)
    lease = frames.acquire_latest()
    expected_published_ns = lease.published_ns

    timestamp = iter(range(expected_published_ns + 1, expected_published_ns + 100))
    monkeypatch.setattr(
        video_pump_module,
        "time",
        SimpleNamespace(monotonic=time.monotonic, monotonic_ns=lambda: next(timestamp)),
    )

    class LeaseOutput:
        def __init__(self, held_lease):
            self.held_lease = held_lease

        def acquire_latest(self):
            held_lease, self.held_lease = self.held_lease, None
            return held_lease

        def release_read(self, slot):
            frames.release_read(slot)

    outputs = LeaseOutput(lease)
    pump = pump_type(outputs, type("HW", (), {"hdmi": hdmi})(), owner, identity,
                     telemetry=telemetry)
    try:
        deadline = time.monotonic() + 1
        while pump.submitted == 0 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert pump.submitted == 1
        sample = telemetry.sample(time.monotonic_ns())
        assert len(telemetry.recorded) == 1
        recorded = telemetry.recorded[0]
        assert recorded.capture_ns == capture_time_ns
        assert recorded.compose_done_ns == expected_published_ns
        assert recorded.submit_done_ns > recorded.compose_done_ns
        assert sample["e2e_ms"]["count"] >= 1
        assert sample["submit_ms"]["count"] >= 1
        assert sample["frame_count"] >= 1
    finally:
        pump.request_stop()
        assert pump.join(1)


def test_zero_capture_time_does_not_record_e2e_sample(frames):
    class RecordingTelemetry(TelemetryWindow):
        def __init__(self):
            super().__init__(window_s=5)
            self.record_calls = 0

        def record_frame(self, frame):
            self.record_calls += 1
            super().record_frame(frame)

    pump_type = _pump_type()
    identity = uuid.uuid4()
    telemetry = RecordingTelemetry()
    _publish(frames, identity, 29, capture_time_ns=0)
    pump = pump_type(
        frames, type("HW", (), {"hdmi": FakeHDMI((8, 12))})(), FakeOwner(),
        identity, telemetry=telemetry,
    )
    try:
        deadline = time.monotonic() + 1
        while pump.submitted == 0 and time.monotonic() < deadline:
            time.sleep(0.001)
        sample = telemetry.sample(time.monotonic_ns())
        assert pump.submitted == 1
        assert telemetry.record_calls == 0
        assert sample["e2e_ms"]["count"] == 0
        assert sample["frame_count"] == 0
    finally:
        pump.request_stop()
        assert pump.join(1)


def test_telemetry_failure_does_not_fail_display_submission(frames):
    class BrokenTelemetry:
        def record_frame(self, frame):
            raise RuntimeError("telemetry unavailable")

    pump_type = _pump_type()
    identity = uuid.uuid4()
    _publish(frames, identity, 91, capture_time_ns=time.monotonic_ns())
    pump = pump_type(
        frames, type("HW", (), {"hdmi": FakeHDMI((8, 12))})(), FakeOwner(),
        identity, telemetry=BrokenTelemetry(),
    )
    try:
        deadline = time.monotonic() + 1
        while pump.submitted == 0 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert pump.submitted == 1
        assert pump.submit_failures == 0
        assert pump.state != DISPLAY_FAIL_HOLD
    finally:
        pump.request_stop()
        assert pump.join(1)


def test_acquire_error_does_not_count_submit_failure():
    class BrokenOutputs:
        def acquire_latest(self):
            raise OSError("output pool unavailable")

    pump = _pump_type()(
        BrokenOutputs(), type("HW", (), {"hdmi": FakeHDMI((8, 12))})(),
        FakeOwner(), uuid.uuid4(),
    )
    assert pump.join(1)
    assert pump.state == DISPLAY_FAIL_HOLD
    assert pump.submit_failures == 0


def test_idle_stop_leaves_no_reading_lease(frames):
    pump_type = _pump_type()
    identity = uuid.uuid4()
    hdmi = FakeHDMI((8, 12))
    pump = pump_type(frames, type("HW", (), {"hdmi": hdmi})(), FakeOwner(), identity)
    time.sleep(0.01)
    pump.request_stop()
    assert pump.join(1)
    assert frames.statuses() == (FREE, FREE, FREE)
