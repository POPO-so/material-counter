import multiprocessing as mp
from pathlib import Path
import time
import uuid

import numpy as np
import pytest

from scripts.standalone.g1_run import (
    _compositor_process,
    _cpu_usage_percent,
    _parse_proc_stat,
    _probe_display_buffers,
    _producer_watchdog_expired,
    _sync_sample_due,
    _wait_for_output_identity,
)
from scripts.standalone.shared_frames import READING, WRITING, SharedFrames


class Buffer:
    def __init__(self):
        self.freed = False

    def freebuffer(self):
        self.freed = True


class Owner:
    def __init__(self):
        self.resources = []

    def retain(self, resource):
        self.resources.append(resource)
        return resource


def test_display_admission_probe_allocates_and_releases_four_gray8_slots():
    owner = Owner()
    buffers = []
    requests = []

    def allocate(*, shape, dtype):
        requests.append((shape, dtype))
        buffer = Buffer()
        buffers.append(buffer)
        return buffer

    _probe_display_buffers(owner, allocate)

    assert requests == [((720, 1280), "u1")] * 4
    assert owner.resources == buffers
    assert all(buffer.freed for buffer in buffers)


def test_display_admission_probe_releases_allocations_when_a_later_slot_fails():
    owner = Owner()
    buffers = []

    def allocate(*, shape, dtype):
        assert (shape, dtype) == ((720, 1280), "u1")
        if len(buffers) == 2:
            raise RuntimeError("BO allocation failed")
        buffer = Buffer()
        buffers.append(buffer)
        return buffer

    with pytest.raises(RuntimeError, match="BO allocation failed"):
        _probe_display_buffers(owner, allocate)

    assert owner.resources == buffers
    assert len(buffers) == 2
    assert all(buffer.freed for buffer in buffers)


def test_proc_stat_parser_returns_each_core_without_guest_double_counting():
    ticks = _parse_proc_stat(
        "cpu  999 0 999 999 0 0 0 0 0 0\n"
        "cpu0 100 10 20 400 10 3 2 1 900 800\n"
        "cpu1 0 0 0 500 0 0 0 0 100 50\n"
    )

    assert ticks == {
        "cpu0": (100, 10, 20, 400, 10, 3, 2, 1),
        "cpu1": (0, 0, 0, 500, 0, 0, 0, 0),
    }


def test_per_core_cpu_percent_uses_idle_and_iowait_deltas():
    before = {
        "cpu0": (100, 10, 20, 400, 10, 3, 2, 1),
        "cpu1": (0, 0, 0, 500, 0, 0, 0, 0),
        "cpu2": (1, 0, 0, 20, 0, 0, 0, 0),
    }
    after = {
        "cpu0": (110, 10, 25, 450, 12, 3, 2, 1),
        "cpu1": (0, 0, 10, 540, 0, 0, 0, 0),
        "cpu3": (9, 9, 9, 9, 9, 9, 9, 9),
    }

    utilization = _cpu_usage_percent(before, after)

    assert utilization == pytest.approx({"cpu0": 100 * 15 / 67, "cpu1": 20.0})


def test_spawned_compositor_publishes_direct_gray8_frame_with_stage_metadata():
    fonts = (
        (Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/arial.ttf")),
        (Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")),
    )
    font_pair = next(((font, latin) for font, latin in fonts if font.is_file() and latin.is_file()), None)
    if font_pair is None:
        pytest.skip("no local CJK and Latin font pair is available")

    ctx = mp.get_context("spawn")
    frames = SharedFrames.create(ctx)
    stop = ctx.Event()
    updates = ctx.Queue(maxsize=1)
    errors = ctx.Queue(maxsize=1)
    counters = ctx.Array("Q", [0] * 4, lock=False)
    session = uuid.uuid4()
    process = ctx.Process(
        target=_compositor_process,
        args=(frames, stop, updates, errors, counters),
        kwargs={"source": "synthetic", "camera_device": "unused",
                "font_path": str(font_pair[0]), "latin_font_path": str(font_pair[1]),
                "target_fps": 10, "session_uuid": str(session)},
    )
    lease = None
    process.start()
    try:
        deadline = time.monotonic() + 8
        while lease is None and time.monotonic() < deadline:
            lease = frames.acquire_latest()
            if lease is None:
                if not process.is_alive():
                    pytest.fail(f"compositor exited early: {errors.get(timeout=1)}")
                time.sleep(0.01)

        assert lease is not None, "compositor did not publish a frame before the deadline"
        assert lease.session_uuid == session
        assert lease.array.shape == (720, 1280)
        assert lease.array.dtype == np.uint8
        assert lease.scale_ns > 0
        assert lease.draw_ns >= 0
        assert lease.published_ns > 0
        assert np.all(lease.array[10, :32] == 0)
        frames.release_read(lease.slot)
        lease = None
    finally:
        if lease is not None:
            frames.release_read(lease.slot)
        stop.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        for slot, state in enumerate(frames.statuses()):
            if state == READING:
                frames.release_read(slot)
            elif state == WRITING:
                frames.reclaim_writer(slot, dead_producer=not process.is_alive())
        frames.close()
        updates.close()
        errors.close()
    assert process.exitcode == 0
    assert counters[1] >= 1


def test_output_sync_returns_only_after_exact_session_and_frame_are_observed():
    session = uuid.uuid4().int
    observations = iter((
        {"session": session + 1, "frame": 12, "sampled_monotonic": time.monotonic()},
        {"session": session, "frame": 12, "sampled_monotonic": time.monotonic()},
    ))

    class Owner:
        def call(self, _function, _monitor, timeout):
            assert timeout == 0.5
            return next(observations)

    observed, observed_ns = _wait_for_output_identity(Owner(), object(), session, 12)

    assert observed["session"] == session
    assert observed["frame"] == 12
    assert observed_ns > 0


def test_output_sync_marks_a_superseded_frame_as_missed_without_waiting():
    session = uuid.uuid4().int

    class Owner:
        def call(self, _function, _monitor, timeout):
            return {"session": session, "frame": 18,
                    "sampled_monotonic": time.monotonic()}

    started = time.monotonic()
    observed, observed_ns = _wait_for_output_identity(Owner(), object(), session, 17)

    assert observed["frame"] == 18
    assert observed_ns is None
    assert time.monotonic() - started < 0.1


def test_producer_watchdog_allows_spawn_and_camera_startup_before_first_frame():
    assert not _producer_watchdog_expired(0, 100.0, 129.9)
    assert _producer_watchdog_expired(0, 100.0, 130.0)


def test_producer_watchdog_still_detects_stall_after_first_publish():
    assert not _producer_watchdog_expired(1, 100.0, 104.9)
    assert _producer_watchdog_expired(1, 100.0, 105.0)


def test_sync_latency_sampling_uses_every_fourth_submitted_frame():
    assert [_sync_sample_due(index) for index in range(10)] == [
        True, False, False, False, True, False, False, False, True, False]
