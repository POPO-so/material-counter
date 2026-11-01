"""Latest real camera Gray8 frame, decoded on demand from raw MJPEG."""
import threading
import time

import numpy as np

from scripts.rle.run_counter import camera_mjpeg_frames, decode_camera_frame


class LateDecodeTimeout(TimeoutError):
    """A decoded frame missed its read deadline but remains usable."""

    def __init__(self, frame):
        self.frame = frame
        super().__init__("no fresh real-camera Gray8 frame")


_UINT64_MAX = (1 << 64) - 1


class LatestCamera:
    def __init__(self, device, *, frames=None):
        if frames is None:
            frames = camera_mjpeg_frames
        self._device = device
        self._frames = frames
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._latest = None
        self._decoded = None
        self._decode_in_progress = False
        self._error = None
        self._captured_total = 0
        self._last_index = None
        self._overwrite_drops = 0
        self._sequence_errors = 0
        self._thread = threading.Thread(target=self._capture, name="gray8-camera", daemon=True)
        self._thread.start()

    def _capture(self):
        try:
            source = self._frames(self._device, 1 << 40)
            try:
                for fallback_index, item in enumerate(source):
                    if self._stop.is_set():
                        break
                    if isinstance(item, tuple) and len(item) == 2:
                        index, frame_data = item
                        injected_gray8 = True
                        if (
                            isinstance(index, bool)
                            or not isinstance(index, int)
                            or not 0 <= index <= _UINT64_MAX
                        ):
                            raise ValueError("injected camera index must be a uint64")
                    else:
                        index, frame_data = fallback_index, item
                        injected_gray8 = False
                    with self._condition:
                        if self._last_index is not None:
                            if index > self._last_index:
                                self._overwrite_drops += index - self._last_index - 1
                            else:
                                self._sequence_errors += 1
                        self._captured_total += 1
                        self._last_index = index
                        self._latest = (index, frame_data, time.monotonic(), injected_gray8)
                        self._condition.notify_all()
            finally:
                source.close()
        except BaseException as error:
            with self._condition:
                self._error = error
                self._condition.notify_all()

    def stats(self):
        with self._condition:
            return {
                "captured_total": self._captured_total,
                "overwrite_drops": self._overwrite_drops,
                "sequence_errors": self._sequence_errors,
            }

    def read(self, *, after=-1, timeout=3.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                if self._error:
                    raise RuntimeError(str(self._error)) from self._error
                if self._decoded is not None and self._decoded[0] > after:
                    if self._latest is None or self._latest[0] <= self._decoded[0]:
                        return self._decoded
                if (not self._decode_in_progress and self._latest is not None and
                        (self._decoded is None or self._latest[0] > self._decoded[0]) and
                        self._latest[0] > after):
                    selected = self._latest
                    self._decode_in_progress = True
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("no fresh real-camera Gray8 frame")
                self._condition.wait(timeout=min(remaining, 0.05))
        try:
            frame_data = selected[1]
            data = frame_data if isinstance(frame_data, np.ndarray) else None
            if (selected[3] and data is not None and data.dtype == np.uint8
                    and data.ndim == 2):
                # Preserve the pre-existing injected Gray8 source contract.
                gray = data
            else:
                gray = decode_camera_frame(frame_data)
        except Exception as error:
            wrapped = RuntimeError(str(error))
            with self._condition:
                self._decode_in_progress = False
                self._error = wrapped
                self._condition.notify_all()
            raise wrapped from error
        except BaseException:
            with self._condition:
                self._decode_in_progress = False
                self._condition.notify_all()
            raise
        decoded = (selected[0], gray, selected[2])
        with self._condition:
            self._decoded = decoded
            self._decode_in_progress = False
            self._condition.notify_all()
            if time.monotonic() > deadline:
                raise LateDecodeTimeout(decoded)
        return decoded

    def close(self):
        self._stop.set()
        self._thread.join(timeout=12)
        if self._thread.is_alive():
            raise TimeoutError("camera reader did not stop; retain process until V4L2 release")
