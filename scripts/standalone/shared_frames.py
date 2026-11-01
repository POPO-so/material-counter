"""Three-slot, ordinary-RAM frame exchange for standalone display processes."""

from multiprocessing import shared_memory
from contextlib import contextmanager
import os
import time
import uuid

import numpy as np


FREE = 0
WRITING = 1
READY = 2
READING = 3

SLOT_COUNT = 3
DEFAULT_SHAPE = (720, 1280)
_UINT64_MAX = (1 << 64) - 1


class FrameLease:
    """A zero-copy view of a frame held in READING state until released."""

    __slots__ = ("slot", "session_uuid", "frame_id", "sequence", "array",
                 "scale_ns", "draw_ns", "compose_ns", "capture_time_ns",
                 "published_ns", "source_publish_ns")

    def __init__(self, slot, session_uuid, frame_id, sequence, array,
                 scale_ns, draw_ns, compose_ns, capture_time_ns, published_ns,
                 source_publish_ns=0):
        self.slot = slot
        self.session_uuid = session_uuid
        self.frame_id = frame_id
        self.sequence = sequence
        self.array = array
        self.scale_ns = scale_ns
        self.draw_ns = draw_ns
        self.compose_ns = compose_ns
        self.capture_time_ns = capture_time_ns
        self.published_ns = published_ns
        self.source_publish_ns = source_publish_ns


class SharedFrames:
    """Shared-memory frame slots with metadata transitions guarded by one lock."""

    def __init__(self, name, shape, lock, statuses, session_ids, frame_ids, sequences,
                 next_sequence, owner_closed, owner_pid, scale_times, draw_times,
                 compose_times, capture_times, published_times, source_publish_times=None,
                 dropped_ready=None, dropped_claims=None, condition=None):
        self.name = name
        self.shape = tuple(shape)
        self._lock = lock
        self._statuses = statuses
        self._session_ids = session_ids
        self._frame_ids = frame_ids
        self._sequences = sequences
        self._next_sequence = next_sequence
        self._owner_closed = owner_closed
        self._owner_pid = owner_pid
        self._scale_times = scale_times
        self._draw_times = draw_times
        self._compose_times = compose_times
        self._capture_times = capture_times
        self._published_times = published_times
        self._source_publish_times = source_publish_times
        self._condition = condition
        self._dropped_ready = dropped_ready
        self._dropped_claims = dropped_claims
        self._shm = shared_memory.SharedMemory(name=name, create=False)
        self._closed = False

    @classmethod
    def create(cls, ctx, shape=DEFAULT_SHAPE):
        """Create the owner instance and its three shared pixel buffers."""
        shape = _validate_shape(shape)
        slot_bytes = int(np.prod(shape, dtype=np.int64))
        shm = shared_memory.SharedMemory(create=True, size=SLOT_COUNT * slot_bytes)
        try:
            instance = cls(
                shm.name,
                shape,
                ctx.Lock(),
                ctx.Array("b", [FREE] * SLOT_COUNT, lock=False),
                ctx.Array("B", [0] * (16 * SLOT_COUNT), lock=False),
                ctx.Array("Q", [0] * SLOT_COUNT, lock=False),
                ctx.Array("Q", [0] * SLOT_COUNT, lock=False),
                ctx.Value("Q", 0, lock=False),
                ctx.Value("b", 0, lock=False),
                os.getpid(),
                ctx.Array("Q", [0] * SLOT_COUNT, lock=False),
                ctx.Array("Q", [0] * SLOT_COUNT, lock=False),
                ctx.Array("Q", [0] * SLOT_COUNT, lock=False),
                ctx.Array("Q", [0] * SLOT_COUNT, lock=False),
                ctx.Array("Q", [0] * SLOT_COUNT, lock=False),
                ctx.Array("Q", [0] * SLOT_COUNT, lock=False),
                ctx.Value("Q", 0, lock=False),
                ctx.Value("Q", 0, lock=False),
                ctx.Condition(),
            )
            shm.close()
            return instance
        except BaseException:
            shm.close()
            shm.unlink()
            raise

    @property
    def closed(self):
        return self._closed

    def __getstate__(self):
        self._ensure_open()
        return {
            "name": self.name,
            "shape": self.shape,
            "lock": self._lock,
            "statuses": self._statuses,
            "session_ids": self._session_ids,
            "frame_ids": self._frame_ids,
            "sequences": self._sequences,
            "next_sequence": self._next_sequence,
            "owner_closed": self._owner_closed,
            "owner_pid": self._owner_pid,
            "scale_times": self._scale_times,
            "draw_times": self._draw_times,
            "compose_times": self._compose_times,
            "capture_times": self._capture_times,
            "published_times": self._published_times,
            "source_publish_times": self._source_publish_times,
            "condition": self._condition,
            "dropped_ready": self._dropped_ready,
            "dropped_claims": self._dropped_claims,
        }

    def __setstate__(self, state):
        self.__init__(
            state["name"], state["shape"], state["lock"], state["statuses"],
            state["session_ids"], state["frame_ids"], state["sequences"],
            state["next_sequence"], state["owner_closed"], state["owner_pid"],
            state["scale_times"], state["draw_times"], state["compose_times"],
            state["capture_times"], state["published_times"],
            state.get("source_publish_times"),
            state.get("dropped_ready"),
            state.get("dropped_claims"), state.get("condition"),
        )

    def claim_write(self):
        """Claim a FREE slot without waiting; return ``(index, uint8 view)`` or None."""
        self._ensure_open()
        if not self._lock.acquire(False):
            return None
        try:
            self._require_shared_open()
            for slot in range(SLOT_COUNT):
                if self._statuses[slot] == FREE:
                    self._statuses[slot] = WRITING
                    return slot, self._array(slot)
            self._dropped_claims.value += 1
            return None
        finally:
            self._lock.release()

    def publish(self, slot, session_uuid, frame_id, *, scale_ns=0, draw_ns=0,
                compose_ns=0, capture_time_ns=0, source_publish_ns=0):
        """Publish a WRITING slot as READY with validated identity and a monotonic sequence."""
        self._ensure_open()
        slot = _validate_slot(slot)
        identity = _validate_uuid(session_uuid)
        frame_id = _validate_uint64(frame_id, "frame_id")
        scale_ns = _validate_stage_ns(scale_ns, "scale_ns")
        draw_ns = _validate_stage_ns(draw_ns, "draw_ns")
        compose_ns = _validate_stage_ns(compose_ns, "compose_ns")
        capture_time_ns = _validate_uint64(capture_time_ns, "capture_time_ns")
        source_publish_ns = _validate_uint64(source_publish_ns, "source_publish_ns")
        with self._locked():
            self._require_shared_open()
            self._require_status(slot, WRITING, "publish")
            if self._next_sequence.value == _UINT64_MAX:
                raise OverflowError("shared frame sequence exhausted uint64")
            sequence = self._next_sequence.value + 1
            start = slot * 16
            self._session_ids[start:start + 16] = identity.bytes
            self._frame_ids[slot] = frame_id
            self._sequences[slot] = sequence
            self._next_sequence.value = sequence
            self._scale_times[slot] = scale_ns
            self._draw_times[slot] = draw_ns
            self._compose_times[slot] = compose_ns
            self._capture_times[slot] = capture_time_ns
            self._published_times[slot] = time.monotonic_ns()
            self._source_publish_times[slot] = source_publish_ns
            self._statuses[slot] = READY
        with self._condition:
            self._condition.notify_all()
        return sequence

    def wait_for_frame(self, timeout=None, stop=None):
        """Wait for a published frame, stop request, or timeout; return a lease or None."""
        self._ensure_open()
        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        while True:
            if stop is not None and stop.is_set():
                return None
            try:
                lease = self.acquire_latest()
            except RuntimeError:
                if self._owner_closed.value:
                    return None
                raise
            if lease is not None:
                return lease
            if deadline is not None and time.monotonic() >= deadline:
                return None
            remaining = 0.05 if deadline is None else min(0.05, max(0.0, deadline - time.monotonic()))
            with self._condition:
                with self._lock:
                    if self._owner_closed.value or any(state == READY for state in self._statuses):
                        continue
                self._condition.wait(remaining)

    def abort_write(self, slot):
        """Return a WRITING slot to FREE."""
        self._ensure_open()
        slot = _validate_slot(slot)
        with self._locked():
            self._require_shared_open()
            self._require_status(slot, WRITING, "abort_write")
            self._statuses[slot] = FREE

    def acquire_latest(self):
        """Acquire the newest READY frame and discard older READY frames."""
        self._ensure_open()
        with self._locked():
            self._require_shared_open()
            ready = [slot for slot in range(SLOT_COUNT) if self._statuses[slot] == READY]
            if not ready:
                return None
            slot = max(ready, key=lambda index: self._sequences[index])
            for old_slot in ready:
                if old_slot != slot:
                    self._statuses[old_slot] = FREE
                    self._dropped_ready.value += 1
            self._statuses[slot] = READING
            start = slot * 16
            identity = uuid.UUID(bytes=bytes(self._session_ids[start:start + 16]))
            return FrameLease(
                slot,
                identity,
                self._frame_ids[slot],
                self._sequences[slot],
                self._array(slot),
                self._scale_times[slot],
                self._draw_times[slot],
                self._compose_times[slot],
                self._capture_times[slot],
                self._published_times[slot],
                self._source_publish_times[slot],
            )

    def stats_snapshot(self):
        """Return bounded pool occupancy and cumulative frame-drop counters."""
        self._ensure_open()
        with self._locked():
            self._require_shared_open()
            states = tuple(self._statuses[index] for index in range(SLOT_COUNT))
            return {"capacity": SLOT_COUNT, "ready": states.count(READY),
                    "writing": states.count(WRITING), "reading": states.count(READING),
                    "dropped_ready_frames": int(self._dropped_ready.value),
                    "write_claim_drops": int(self._dropped_claims.value)}

    def release_read(self, slot):
        """Return a READING slot to FREE; duplicate or invalid release is rejected."""
        self._ensure_open()
        slot = _validate_slot(slot)
        with self._locked():
            self._require_shared_open()
            self._require_status(slot, READING, "release_read")
            self._statuses[slot] = FREE

    def reclaim_writer(self, slot, *, dead_producer=False):
        """Reclaim WRITING only after the owner confirms its producer has exited."""
        self._ensure_open()
        self._require_owner()
        if dead_producer is not True:
            raise ValueError("dead_producer=True is required after confirming producer exit")
        slot = _validate_slot(slot)
        with self._locked():
            self._require_shared_open()
            self._require_status(slot, WRITING, "reclaim_writer")
            self._statuses[slot] = FREE

    def reclaim_reader(self, slot, *, dead_consumer=False):
        """Reclaim READING after joining the consumer and confirming it is not alive.

        The owner may pass ``dead_consumer=True`` only after ``consumer.join()``
        has returned and ``consumer.is_alive()`` is false. This flag is the
        caller's confirmation; this method does not inspect a process handle.
        """
        self._ensure_open()
        self._require_owner()
        if dead_consumer is not True:
            raise ValueError(
                "dead_consumer=True is required after consumer.join() and not consumer.is_alive()"
            )
        slot = _validate_slot(slot)
        with self._locked():
            self._require_shared_open()
            self._require_status(slot, READING, "reclaim_reader")
            self._statuses[slot] = FREE

    def statuses(self):
        """Return a consistent snapshot of the three slot states."""
        self._ensure_open()
        with self._locked():
            self._require_shared_open()
            return tuple(self._statuses[index] for index in range(SLOT_COUNT))

    def close(self):
        """Detach this process; the owner also unlinks after checking active leases."""
        if self._closed:
            return
        if os.getpid() == self._owner_pid:
            with self._locked():
                self._require_shared_open()
                active = [state for state in self._statuses if state in (WRITING, READING)]
                if active:
                    raise RuntimeError("cannot close while a slot is WRITING or READING")
                for slot in range(SLOT_COUNT):
                    if self._statuses[slot] == READY:
                        self._statuses[slot] = FREE
                self._owner_closed.value = 1
                self._shm.unlink()
            with self._condition:
                self._condition.notify_all()
        self._shm.close()
        self._closed = True

    @contextmanager
    def _locked(self):
        if not self._lock.acquire(timeout=0.05):
            raise TimeoutError("shared frame lock unavailable; preserve pool for recovery")
        try:
            yield
        finally:
            self._lock.release()

    def _array(self, slot):
        slot_bytes = int(np.prod(self.shape, dtype=np.int64))
        return np.ndarray(self.shape, dtype=np.uint8, buffer=self._shm.buf,
                          offset=slot * slot_bytes)

    def _require_status(self, slot, expected, operation):
        actual = self._statuses[slot]
        if actual != expected:
            raise RuntimeError(
                "{} requires slot {} state {}, got {}".format(operation, slot, expected, actual)
            )

    def _require_owner(self):
        if os.getpid() != self._owner_pid:
            raise PermissionError("only the creating process may reclaim or unlink shared frames")

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("shared frames are closed")

    def _require_shared_open(self):
        if self._owner_closed.value:
            raise RuntimeError("shared frames owner has closed the shared memory")


def _validate_shape(shape):
    try:
        dimensions = tuple(shape)
    except TypeError as exc:
        raise ValueError("shape must contain exactly two positive dimensions") from exc
    if (len(dimensions) != 2 or any(isinstance(value, bool) or not isinstance(value, int)
                                    or value <= 0 for value in dimensions)):
        raise ValueError("shape must contain exactly two positive integer dimensions")
    return dimensions


def _validate_slot(slot):
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < SLOT_COUNT:
        raise ValueError("slot must be an integer from 0 to 2")
    return slot


def _validate_uuid(value):
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except (ValueError, AttributeError) as exc:
            raise ValueError("session_uuid must be a valid UUID") from exc
    raise ValueError("session_uuid must be a UUID or UUID string")


def _validate_uint64(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _UINT64_MAX:
        raise ValueError("{} must be an integer from 0 to 2**64-1".format(name))
    return value


def _validate_stage_ns(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _UINT64_MAX:
        raise ValueError("{} must be a non-negative integer nanosecond duration".format(name))
    return value
