import threading
import time
import cv2
import numpy as np
import pytest
try:
    from scripts.standalone.camera import LatestCamera
except ModuleNotFoundError:
    LatestCamera = None

def test_camera_keeps_original_gray8_and_returns_only_a_fresh_frame():
    if LatestCamera is None:
        pytest.fail("live Gray8 camera source is not implemented")
    def frames(device,count):
        assert device == "/dev/v4l/by-id/test"
        for index in range(3):
            yield _jpeg(index)
            time.sleep(.02)
    c=LatestCamera("/dev/v4l/by-id/test",frames=frames)
    try:
        first=c.read(timeout=.5)
        second=c.read(after=first[0],timeout=.5)
        assert second[0] > first[0]
        assert second[1].shape == (400,640) and second[1].dtype == np.uint8
    finally:
        c.close()

def test_camera_failure_and_timeout_are_explicit():
    if LatestCamera is None:
        pytest.fail("live Gray8 camera source is not implemented")
    def broken(device,count):
        raise OSError("USB disconnected")
        yield
    c=LatestCamera("camera",frames=broken)
    try:
        with pytest.raises(RuntimeError,match="USB disconnected"):
            c.read(timeout=.3)
    finally:
        c.close()

def test_close_waits_for_blocking_camera_read_to_finish():
    if LatestCamera is None:
        pytest.fail("live Gray8 camera source is not implemented")
    reading = threading.Event()
    def frames(device,count):
        reading.set()
        time.sleep(2.2)
        if False:
            yield None
    c=LatestCamera("camera",frames=frames)
    assert reading.wait(timeout=.5), "camera reader did not enter its blocking read"
    c.close()


def _jpeg(value):
    source = np.full((400, 640), value, dtype=np.uint8)
    ok, encoded = cv2.imencode('.jpg', source)
    assert ok
    return encoded


def test_latest_camera_decodes_only_newest_compressed_packet_once(monkeypatch):
    packets = [_jpeg(v) for v in (20, 90, 180)]
    consumed = []
    original = cv2.imdecode
    def tracked_decode(data, flags):
        consumed.append(bytes(data))
        return original(data, flags)
    monkeypatch.setattr(cv2, 'imdecode', tracked_decode)
    def frames(device, count):
        yield from packets
        time.sleep(.1)
    c = LatestCamera('camera', frames=frames)
    try:
        result = c.read(timeout=.5)
        assert result[0] == 2
        assert np.array_equal(result[1], original(packets[-1], cv2.IMREAD_GRAYSCALE))
        assert len(consumed) == 1
        assert consumed[0] == packets[-1].tobytes()
    finally:
        c.close()


def test_latest_camera_keeps_capture_sequence_and_timestamp_when_latest_advances(monkeypatch):
    first, second = _jpeg(40), _jpeg(150)
    decoding = threading.Event()
    release_decode = threading.Event()
    source_advanced = threading.Event()
    original = cv2.imdecode
    def blocked_decode(data, flags):
        decoding.set()
        assert release_decode.wait(.5)
        return original(data, flags)
    monkeypatch.setattr(cv2, 'imdecode', blocked_decode)
    def frames(device, count):
        yield first
        assert source_advanced.wait(.5)
        time.sleep(.01)
        yield second
    c = LatestCamera('camera', frames=frames)
    result = []
    reader = threading.Thread(target=lambda: result.append(c.read(timeout=.8)))
    try:
        reader.start()
        assert decoding.wait(.5)
        first_capture = c._latest
        source_advanced.set()
        deadline = time.monotonic() + .5
        while c._latest[0] < 1 and time.monotonic() < deadline:
            time.sleep(.005)
        captured = c._latest
        release_decode.set()
        reader.join(.5)
        assert not reader.is_alive()
        assert result[0][0] == 0
        assert result[0][2] == first_capture[2]
        monkeypatch.setattr(cv2, 'imdecode', original)
        newer = c.read(after=result[0][0], timeout=.5)
        assert newer[0] == 1
        assert newer[2] == captured[2]
    finally:
        release_decode.set()
        c.close()


def test_latest_camera_reuses_cached_decode_without_new_capture(monkeypatch):
    packet = _jpeg(77)
    calls = []
    original = cv2.imdecode
    def tracked_decode(data, flags):
        calls.append(1)
        return original(data, flags)
    monkeypatch.setattr(cv2, 'imdecode', tracked_decode)
    def frames(device, count):
        yield packet
    c = LatestCamera('camera', frames=frames)
    try:
        first = c.read(timeout=.5)
        again = c.read(timeout=.5)
        assert again[0] == first[0]
        assert again[2] == first[2]
        assert np.array_equal(again[1], first[1])
        assert len(calls) == 1
    finally:
        c.close()


def test_latest_camera_wraps_decode_validation_error_for_reader():
    def frames(device, count):
        yield np.zeros((400, 640), dtype=np.uint8)
    c = LatestCamera('camera', frames=frames)
    try:
        with pytest.raises(RuntimeError, match='camera must return raw MJPEG with JPEG SOI') as error:
            c.read(timeout=.5)
        assert isinstance(error.value.__cause__, ValueError)
    finally:
        c.close()
