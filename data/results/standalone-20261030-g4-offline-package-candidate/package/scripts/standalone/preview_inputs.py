"""Shared Gray8 and binary preview frames for the standalone pipeline."""

import json
import numpy as np

from .shared_frames import WRITING, SharedFrames


FRAME_SHAPE = (400, 640)
POOL_SHAPE = (800, 640)
SLOT_COUNT = 3
METADATA_CAPACITY = 32768


class PreviewLease:
    """A preview slot held in READING state until :meth:`release` is called."""

    __slots__ = ("_frame_lease", "_released", "gray", "binary", "session_uuid",
                 "frame_id", "metadata")

    def __init__(self, frame_lease, gray, binary, metadata):
        self._frame_lease = frame_lease
        self._released = False
        self.gray = gray
        self.binary = binary
        self.session_uuid = frame_lease.session_uuid
        self.frame_id = frame_lease.frame_id
        self.metadata = metadata


class PreviewInputs:
    """Two-plane preview transport backed by ``SharedFrames`` and RawArrays."""

    def __init__(self, frames, metadata_bytes, metadata_lengths):
        self._frames = frames
        self._metadata_bytes = metadata_bytes
        self._metadata_lengths = metadata_lengths

    @classmethod
    def create(cls, ctx):
        """Create the fixed three-slot (800, 640) shared preview pool."""
        frames = SharedFrames.create(ctx, shape=POOL_SHAPE)
        try:
            metadata_bytes = ctx.RawArray("B", SLOT_COUNT * METADATA_CAPACITY)
            metadata_lengths = ctx.RawArray("i", SLOT_COUNT)
        except BaseException:
            frames.close()
            raise
        return cls(frames, metadata_bytes, metadata_lengths)

    def publish(self, gray, binary, session_uuid, frame_id, metadata):
        """Publish a pair of uint8 planes without blocking when no slot is free."""
        _validate_plane(gray, "gray")
        _validate_plane(binary, "binary")
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a dict")
        try:
            encoded = json.dumps(metadata, ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError("metadata must be JSON serializable") from exc
        if len(encoded) > METADATA_CAPACITY:
            return False

        claimed = self._frames.claim_write()
        if claimed is None:
            return False
        slot, planes = claimed
        try:
            planes[:FRAME_SHAPE[0], :] = gray
            planes[FRAME_SHAPE[0]:, :] = binary
            start = slot * METADATA_CAPACITY
            self._metadata_bytes[start:start + len(encoded)] = encoded
            self._metadata_lengths[slot] = len(encoded)
            self._frames.publish(slot, session_uuid, frame_id)
            return True
        except BaseException:
            try:
                if self._frames.statuses()[slot] == WRITING:
                    self._frames.abort_write(slot)
            except Exception:
                pass
            raise

    def acquire_latest(self):
        """Acquire the newest published preview, or return ``None``."""
        frame_lease = self._frames.acquire_latest()
        if frame_lease is None:
            return None
        slot = frame_lease.slot
        try:
            length = self._metadata_lengths[slot]
            if not 0 <= length <= METADATA_CAPACITY:
                raise ValueError("invalid shared metadata length")
            start = slot * METADATA_CAPACITY
            payload = bytes(self._metadata_bytes[start:start + length])
            metadata = json.loads(payload.decode("utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("shared preview metadata is not a dict")
            return PreviewLease(
                frame_lease,
                frame_lease.array[:FRAME_SHAPE[0], :],
                frame_lease.array[FRAME_SHAPE[0]:, :],
                metadata,
            )
        except BaseException:
            self._frames.release_read(slot)
            raise

    def release(self, lease):
        """Release a preview lease and make its slot available to producers."""
        if not isinstance(lease, PreviewLease) or lease._released:
            raise ValueError("lease is invalid or already released")
        self._frames.release_read(lease._frame_lease.slot)
        lease._released = True

    def statuses(self):
        """Return the underlying three-slot status snapshot for shutdown checks."""
        return self._frames.statuses()

    def close(self):
        """Detach this process; the SharedFrames creator also unlinks the pool."""
        self._frames.close()


def _validate_plane(array, name):
    if (not isinstance(array, np.ndarray) or array.dtype != np.uint8
            or array.shape != FRAME_SHAPE):
        raise ValueError("{} must be a uint8 ndarray with shape {}".format(name, FRAME_SHAPE))
