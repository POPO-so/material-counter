"""Latest real camera Gray8 frame, decoded on demand from raw MJPEG."""
import threading
import time

from scripts.rle.run_counter import camera_mjpeg_frames, decode_camera_frame


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
        self._thread = threading.Thread(target=self._capture, name="mjpeg-camera", daemon=True)
        self._thread.start()

    def _capture(self):
        try:
            source = self._frames(self._device, 1 << 40)
            try:
                for index, raw in enumerate(source):
                    if self._stop.is_set():
                        break
                    packet = (index, raw, time.monotonic())
                    with self._condition:
                        self._latest = packet
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
            gray = decode_camera_frame(selected[1])
        except BaseException as error:
            wrapped = RuntimeError(str(error))
            with self._condition:
                self._decode_in_progress = False
                self._error = wrapped
                self._condition.notify_all()
            raise wrapped from error
        decoded = (selected[0], gray, selected[2])
        with self._condition:
            self._decoded = decoded
            self._decode_in_progress = False
            self._condition.notify_all()
        return decoded

    def close(self):
        self._stop.set()
        self._thread.join(timeout=12)
        if self._thread.is_alive():
            raise TimeoutError("camera reader did not stop; retain process until V4L2 release")
