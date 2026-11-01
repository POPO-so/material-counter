import multiprocessing as mp
import time
import uuid

import numpy as np
import pytest

from scripts.standalone.shared_frames import (
    FREE,
    READY,
    READING,
    WRITING,
    SharedFrames,
)


def _spawn_publish(frames, result_queue, session_id, frame_id, fill):
    slot = frames.claim_write()
    if slot is None:
        result_queue.put(None)
        return
    index, pixels = slot
    pixels[:] = fill
    frames.publish(index, session_id, frame_id, capture_time_ns=123456789)
    result_queue.put(index)
    frames.close()


def _spawn_abandon_write(frames, ready):
    slot = frames.claim_write()
    ready.put(slot[0])
    frames.close()


def _spawn_abandon_read(frames, ready):
    lease = frames.acquire_latest()
    ready.put(lease.slot)
    frames.close()


def _spawn_reclaim_read(frames, slot, result):
    try:
        frames.reclaim_reader(slot, dead_consumer=True)
    except PermissionError:
        result.put("rejected")
    except AttributeError:
        result.put("missing")
    else:
        result.put("unexpected reclaim")
    finally:
        frames.close()


def _spawn_try_claim_after_close(frames, attached, resume, result):
    attached.set()
    resume.wait(5)
    try:
        frames.claim_write()
    except RuntimeError:
        result.put("rejected")
    else:
        result.put("unexpected claim")
    finally:
        frames.close()


@pytest.fixture
def frames():
    value = SharedFrames.create(ctx=mp.get_context("spawn"), shape=(8, 12))
    yield value
    if not value.closed:
        value.close()


def test_spawn_process_writes_and_parent_reads_shared_pixels(frames):
    ctx = mp.get_context("spawn")
    result = ctx.Queue()
    session_id = uuid.uuid4()
    process = ctx.Process(target=_spawn_publish, args=(frames, result, session_id, 41, 173))
    process.start()
    process.join(10)
    assert process.exitcode == 0
    assert result.get(timeout=2) is not None

    lease = frames.acquire_latest()
    assert lease is not None
    assert (lease.session_uuid, lease.frame_id) == (session_id, 41)
    assert lease.capture_time_ns == 123456789
    assert lease.array.shape == (8, 12)
    assert lease.array.dtype == np.uint8
    assert np.all(lease.array == 173)
    frames.release_read(lease.slot)


def test_full_slots_drop_publish_without_backpressure(frames):
    claimed = [frames.claim_write() for _ in range(3)]
    assert [item[0] for item in claimed] == [0, 1, 2]
    assert frames.claim_write() is None
    for slot, pixels in claimed:
        pixels[:] = slot
        frames.publish(slot, uuid.uuid4(), slot)
    assert frames.claim_write() is None
    assert list(frames.statuses()) == [READY, READY, READY]
    assert frames.stats_snapshot()["ready"] == 3
    assert frames.claim_write() is None
    assert frames.stats_snapshot()["write_claim_drops"] == 3


def test_latest_reader_records_dropped_older_ready_frames(frames):
    for frame_id in (10, 11, 12):
        slot, pixels = frames.claim_write()
        pixels.fill(frame_id)
        frames.publish(slot, uuid.uuid4(), frame_id)
    lease = frames.acquire_latest()
    assert lease.frame_id == 12
    stats = frames.stats_snapshot()
    assert stats["ready"] == 0 and stats["reading"] == 1
    assert stats["dropped_ready_frames"] == 2
    frames.release_read(lease.slot)


def test_publish_carries_capture_time(frames):
    slot, _pixels = frames.claim_write()
    try:
        frames.publish(slot, uuid.UUID(int=9), 81, capture_time_ns=1234)
        lease = frames.acquire_latest()
        assert lease.capture_time_ns == 1234
        frames.release_read(lease.slot)
    finally:
        if frames.statuses()[slot] == WRITING:
            frames.abort_write(slot)
        elif frames.statuses()[slot] == READING:
            frames.release_read(slot)


@pytest.mark.parametrize('capture_time_ns', [-1, True, 1 << 64])
def test_publish_rejects_invalid_capture_time_without_losing_write_lease(frames, capture_time_ns):
    slot, _pixels = frames.claim_write()
    try:
        with pytest.raises(ValueError):
            frames.publish(slot, uuid.UUID(int=9), 82, capture_time_ns=capture_time_ns)
        assert frames.statuses()[slot] == WRITING
    finally:
        if frames.statuses()[slot] == WRITING:
            frames.abort_write(slot)


def test_publish_carries_producer_stage_times_and_monotonic_publish_time(frames):
    slot, pixels = frames.claim_write()
    pixels.fill(5)
    published_after = time.monotonic_ns()

    try:
        frames.publish(slot, uuid.UUID(int=9), 81, scale_ns=2_500_000,
                       draw_ns=1_250_000, compose_ns=4_000_000)
        lease = frames.acquire_latest()
        assert lease.frame_id == 81
        assert lease.scale_ns == 2_500_000
        assert lease.draw_ns == 1_250_000
        assert lease.compose_ns == 4_000_000
        assert lease.published_ns >= published_after
        assert lease.published_ns <= time.monotonic_ns()
        frames.release_read(lease.slot)
    finally:
        if frames.statuses()[slot] == WRITING:
            frames.abort_write(slot)
        elif frames.statuses()[slot] == READING:
            frames.release_read(slot)


@pytest.mark.parametrize("name,value", [("scale_ns", -1), ("draw_ns", True),
                                         ("scale_ns", 1.5)])
def test_publish_rejects_invalid_stage_times_without_losing_write_lease(frames, name, value):
    slot, _pixels = frames.claim_write()
    try:
        with pytest.raises(ValueError):
            frames.publish(slot, uuid.UUID(int=9), 82, **{name: value})
        assert frames.statuses()[slot] == WRITING
    finally:
        if frames.statuses()[slot] == WRITING:
            frames.abort_write(slot)


def test_reader_lease_cannot_be_overwritten_and_old_ready_frames_are_dropped(frames):
    for frame_id in (1, 2):
        slot, pixels = frames.claim_write()
        pixels[:] = frame_id
        frames.publish(slot, uuid.UUID(int=7), frame_id)
    newest = frames.acquire_latest()
    assert newest.frame_id == 2
    assert newest.sequence == 2
    assert frames.statuses() == (FREE, READING, FREE)
    assert frames.statuses()[newest.slot] == READING
    replacement = frames.claim_write()
    assert replacement is not None
    assert replacement[0] != newest.slot
    replacement[1][:] = 3
    frames.publish(replacement[0], uuid.UUID(int=8), 3)
    assert np.all(newest.array == 2)
    frames.release_read(newest.slot)


def test_abandoned_writer_requires_explicit_dead_producer_reclaim(frames):
    ctx = mp.get_context("spawn")
    slot_queue = ctx.Queue()
    process = ctx.Process(target=_spawn_abandon_write, args=(frames, slot_queue))
    process.start()
    process.join(10)
    assert process.exitcode == 0
    slot = slot_queue.get(timeout=2)
    assert frames.statuses()[slot] == WRITING
    with pytest.raises(ValueError):
        frames.reclaim_writer(slot, dead_producer=False)
    assert frames.statuses()[slot] == WRITING
    frames.reclaim_writer(slot, dead_producer=True)
    assert frames.statuses()[slot] == FREE


def test_abandoned_reader_reclaim_requires_confirmed_dead_consumer(frames):
    slot, pixels = frames.claim_write()
    pixels.fill(23)
    frames.publish(slot, uuid.uuid4(), 1)
    ctx = mp.get_context("spawn")
    slot_queue = ctx.Queue()
    process = ctx.Process(target=_spawn_abandon_read, args=(frames, slot_queue))
    process.start()
    process.join(10)
    assert process.exitcode == 0
    slot = slot_queue.get(timeout=2)
    assert not process.is_alive()
    assert frames.statuses()[slot] == READING

    try:
        with pytest.raises(ValueError):
            frames.reclaim_reader(slot)
        with pytest.raises(ValueError):
            frames.reclaim_reader(slot, dead_consumer=False)
        assert frames.statuses()[slot] == READING

        frames.reclaim_reader(slot, dead_consumer=True)
        assert frames.statuses()[slot] == FREE
        replacement = frames.claim_write()
        assert replacement is not None and replacement[0] == slot
        frames.abort_write(replacement[0])
    finally:
        if frames.statuses()[slot] == READING:
            frames.release_read(slot)


@pytest.mark.parametrize("state", ["free", "ready"])
def test_reader_reclaim_rejects_non_reading_slot(frames, state):
    slot = 0
    if state == "ready":
        slot, pixels = frames.claim_write()
        pixels.fill(8)
        frames.publish(slot, uuid.uuid4(), 1)
    with pytest.raises(RuntimeError):
        frames.reclaim_reader(slot, dead_consumer=True)
    assert frames.statuses()[slot] == (FREE if state == "free" else READY)


def test_non_owner_cannot_reclaim_reader(frames):
    slot, pixels = frames.claim_write()
    pixels.fill(11)
    frames.publish(slot, uuid.uuid4(), 1)
    lease = frames.acquire_latest()
    ctx = mp.get_context("spawn")
    result = ctx.Queue()
    process = ctx.Process(target=_spawn_reclaim_read, args=(frames, lease.slot, result))
    process.start()
    process.join(10)
    assert process.exitcode == 0
    try:
        assert result.get(timeout=2) == "rejected"
        assert frames.statuses()[lease.slot] == READING
    finally:
        if frames.statuses()[lease.slot] == READING:
            frames.release_read(lease.slot)


def test_invalid_identity_and_shape_are_rejected(frames):
    slot, _pixels = frames.claim_write()
    with pytest.raises(ValueError):
        frames.publish(slot, "not-a-uuid", 1)
    with pytest.raises(ValueError):
        frames.publish(slot, uuid.uuid4(), 1 << 64)
    with pytest.raises(ValueError):
        SharedFrames.create(ctx=mp.get_context("spawn"), shape=(8, 12, 3))
    frames.abort_write(slot)


def test_illegal_transitions_and_duplicate_release_are_rejected(frames):
    with pytest.raises(RuntimeError):
        frames.release_read(0)
    slot, _pixels = frames.claim_write()
    with pytest.raises(RuntimeError):
        frames.release_read(slot)
    frames.abort_write(slot)
    with pytest.raises(RuntimeError):
        frames.abort_write(slot)
    reading_slot, pixels = frames.claim_write()
    pixels[:] = 4
    frames.publish(reading_slot, uuid.uuid4(), 4)
    frames.acquire_latest()
    frames.release_read(reading_slot)
    with pytest.raises(RuntimeError):
        frames.release_read(reading_slot)


def test_parent_close_refuses_active_slots_and_child_close_only_detaches(frames):
    slot, _pixels = frames.claim_write()
    with pytest.raises(RuntimeError):
        frames.close()
    frames.abort_write(slot)
    slot, pixels = frames.claim_write()
    pixels[:] = 9
    frames.publish(slot, uuid.uuid4(), 1)
    lease = frames.acquire_latest()
    with pytest.raises(RuntimeError):
        frames.close()
    frames.release_read(lease.slot)
    frames.close()
    assert frames.closed


def test_owner_close_prevents_a_spawned_process_from_claiming_after_unlink(frames):
    ctx = mp.get_context("spawn")
    attached, resume, result = ctx.Event(), ctx.Event(), ctx.Queue()
    process = ctx.Process(
        target=_spawn_try_claim_after_close,
        args=(frames, attached, resume, result),
    )
    process.start()
    assert attached.wait(5)
    frames.close()
    resume.set()
    process.join(10)
    assert process.exitcode == 0
    assert result.get(timeout=2) == "rejected"


def _spawn_wait_frame(frames, stop, result, attached=None):
    if attached is not None:
        attached.set()
    lease = frames.wait_for_frame(timeout=5, stop=stop)
    result.put(None if lease is None else lease.frame_id)
    if lease is not None:
        frames.release_read(lease.slot)
    frames.close()


def test_wait_for_frame_wakes_spawned_consumer_and_has_no_lost_publish(frames):
    ctx = mp.get_context("spawn")
    stop, attached, result = ctx.Event(), ctx.Event(), ctx.Queue()
    process = ctx.Process(target=_spawn_wait_frame, args=(frames, stop, result, attached))
    process.start()
    assert attached.wait(3)
    time.sleep(0.05)
    slot, pixels = frames.claim_write()
    pixels.fill(7)
    frames.publish(slot, uuid.uuid4(), 91)
    process.join(5)
    assert process.exitcode == 0
    assert result.get(timeout=1) == 91


def test_wait_for_frame_stop_and_timeout_are_bounded(frames):
    stop = mp.get_context("spawn").Event()
    started = time.monotonic()
    assert frames.wait_for_frame(timeout=0.08, stop=stop) is None
    assert time.monotonic() - started < 0.5
    stop.set()
    assert frames.wait_for_frame(timeout=2, stop=stop) is None


def test_owner_close_wakes_a_waiting_spawned_consumer(frames):
    ctx = mp.get_context("spawn")
    stop, attached, result = ctx.Event(), ctx.Event(), ctx.Queue()
    process = ctx.Process(target=_spawn_wait_frame, args=(frames, stop, result, attached))
    process.start()
    assert attached.wait(3)
    time.sleep(0.05)
    frames.close()
    process.join(3)
    assert process.exitcode == 0
    assert result.get(timeout=1) is None



def test_reader_never_waits_forever_if_a_crashed_writer_left_the_lock_held():
    import multiprocessing as mp
    import threading
    pool = SharedFrames.create(mp.get_context("spawn"), shape=(4, 8))
    errors = []
    def read():
        try:
            pool.acquire_latest()
        except TimeoutError as error:
            errors.append(error)
    pool._lock.acquire()
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    try:
        thread.join(timeout=0.3)
        assert not thread.is_alive(), "IPC reader hung on an unavailable producer lock"
        assert errors
    finally:
        pool._lock.release()
        thread.join(timeout=0.3)
        pool.close()
