"""Latest real camera Gray8 frame, using the existing raw-MJPEG decoder."""
import threading
import time


_UINT64_MAX = (1 << 64) - 1


class LatestCamera:
    def __init__(self, device, *, frames=None):
        if frames is None:
            from scripts.rle.run_counter import camera_frames
            frames = camera_frames
        self._device = device
        self._frames = frames
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._latest = None
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
                        index, gray = item
                        if (
                            isinstance(index, bool)
                            or not isinstance(index, int)
                            or not 0 <= index <= _UINT64_MAX
                        ):
                            raise ValueError("injected camera index must be a uint64")
                    else:
                        index, gray = fallback_index, item
                    with self._condition:
                        if self._last_index is not None:
                            if index > self._last_index:
                                self._overwrite_drops += index - self._last_index - 1
                            else:
                                self._sequence_errors += 1
                        self._captured_total += 1
                        self._last_index = index
                        self._latest = (index, gray, time.monotonic())
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
                if self._latest is not None and self._latest[0] > after:
                    return self._latest
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("no fresh real-camera Gray8 frame")
                self._condition.wait(timeout=min(remaining, 0.05))

    def close(self):
        self._stop.set()
        self._thread.join(timeout=12)
        if self._thread.is_alive():
            raise TimeoutError("camera reader did not stop; retain process until V4L2 release")
