import multiprocessing as mp
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
    frames.publish(index, session_id, frame_id)
    result_queue.put(index)
    frames.close()


def _spawn_abandon_write(frames, ready):
    slot = frames.claim_write()
    ready.put(slot[0])
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
