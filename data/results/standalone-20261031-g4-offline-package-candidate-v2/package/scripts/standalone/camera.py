"""Latest real camera Gray8 frame, using the existing raw-MJPEG decoder."""
import threading
import time


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
        self._thread = threading.Thread(target=self._capture, name="gray8-camera", daemon=True)
        self._thread.start()

    def _capture(self):
        try:
            source = self._frames(self._device, 1 << 40)
            try:
                for index, gray in enumerate(source):
                    if self._stop.is_set():
                        break
                    with self._condition:
                        self._latest = (index, gray, time.monotonic())
                        self._condition.notify_all()
            finally:
                source.close()
        except BaseException as error:
            with self._condition:
                self._error = error
                self._condition.notify_all()

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
