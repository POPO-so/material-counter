import multiprocessing as mp
import uuid

import numpy as np
import pytest

from scripts.standalone.shared_frames import FREE, READING, SLOT_COUNT, WRITING
from scripts.standalone.preview_inputs import PreviewInputs


SHAPE = (400, 640)


def _spawn_consumer(inputs, results):
    lease = inputs.acquire_latest()
    if lease is None:
        results.put(None)
        return
    try:
        results.put((lease.session_uuid, lease.frame_id, lease.metadata,
                     int(lease.gray[0, 0]), int(lease.binary[-1, -1])))
    finally:
        inputs.release(lease)
        inputs.close()


def _images(value):
    return (np.full(SHAPE, value, dtype=np.uint8),
            np.full(SHAPE, value + 1, dtype=np.uint8))


def test_round_trip_preserves_identity_metadata_and_planes():
    inputs = PreviewInputs.create(mp.get_context("spawn"))
    session_id = str(uuid.uuid4())
    gray, binary = _images(21)
    try:
        assert inputs.publish(gray, binary, session_id, 73, {"count": 4, "tag": "ok"})
        lease = inputs.acquire_latest()
        assert lease.session_uuid == uuid.UUID(session_id)
        assert lease.frame_id == 73
        assert lease.metadata == {"count": 4, "tag": "ok"}
        np.testing.assert_array_equal(lease.gray, gray)
        np.testing.assert_array_equal(lease.binary, binary)
        assert np.shares_memory(lease.gray, lease.binary) is False
        inputs.release(lease)
        assert inputs.statuses() == (FREE, FREE, FREE)
    finally:
        inputs.close()


def test_full_reading_slots_drop_without_overwriting_leased_frame():
    inputs = PreviewInputs.create(mp.get_context("spawn"))
    session_id = str(uuid.uuid4())
    held = []
    try:
        for frame_id in range(SLOT_COUNT):
            gray, binary = _images(frame_id)
            assert inputs.publish(gray, binary, session_id, frame_id, {})
            held.append(inputs.acquire_latest())
        gray, binary = _images(90)
        assert inputs.publish(gray, binary, session_id, 90, {}) is False
        assert [int(lease.gray[0, 0]) for lease in held] == [0, 1, 2]
        assert all(state == READING for state in inputs.statuses())
    finally:
        for lease in held:
            inputs.release(lease)
        inputs.close()


def test_oversized_metadata_is_rejected_without_claiming_slot():
    inputs = PreviewInputs.create(mp.get_context("spawn"))
    gray, binary = _images(1)
    try:
        assert inputs.publish(gray, binary, str(uuid.uuid4()), 1,
                              {"value": "x" * 32768}) is False
        assert inputs.statuses() == (FREE, FREE, FREE)
    finally:
        inputs.close()


def test_publish_exception_aborts_the_claimed_slot():
    inputs = PreviewInputs.create(mp.get_context("spawn"))
    gray, binary = _images(1)
    try:
        with pytest.raises(ValueError):
            inputs.publish(gray, binary, "not-a-uuid", 1, {})
        assert inputs.statuses() == (FREE, FREE, FREE)
        assert WRITING not in inputs.statuses()
    finally:
        inputs.close()


@pytest.mark.parametrize("gray,binary", [
    (np.zeros((400, 640), dtype=np.uint16), np.zeros(SHAPE, dtype=np.uint8)),
    (np.zeros((400, 639), dtype=np.uint8), np.zeros(SHAPE, dtype=np.uint8)),
    (np.zeros(SHAPE, dtype=np.uint8), np.zeros((400, 640, 1), dtype=np.uint8)),
])
def test_invalid_images_are_rejected_without_claiming_slot(gray, binary):
    inputs = PreviewInputs.create(mp.get_context("spawn"))
    try:
        with pytest.raises(ValueError):
            inputs.publish(gray, binary, str(uuid.uuid4()), 1, {})
        assert inputs.statuses() == (FREE, FREE, FREE)
    finally:
        inputs.close()


def test_spawn_consumer_reads_shared_ram_and_releases_before_owner_close():
    ctx = mp.get_context("spawn")
    inputs = PreviewInputs.create(ctx)
    result_queue = ctx.Queue()
    session_id = str(uuid.uuid4())
    gray, binary = _images(45)
    assert inputs.publish(gray, binary, session_id, 801, {"objects": 2})
    child = ctx.Process(target=_spawn_consumer, args=(inputs, result_queue))
    child.start()
    child.join(15)
    try:
        assert child.exitcode == 0
        assert result_queue.get(timeout=2) == (uuid.UUID(session_id), 801,
                                               {"objects": 2}, 45, 46)
        assert inputs.statuses() == (FREE, FREE, FREE)
    finally:
        if child.is_alive():
            child.terminate()
            child.join()
        inputs.close()
        result_queue.close()
