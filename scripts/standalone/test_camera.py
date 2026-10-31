import threading
import time
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
            yield np.full((400,640),index,dtype=np.uint8)
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

def test_camera_stats_report_produced_overwritten_and_sequence_errors():
    if LatestCamera is None:
        pytest.fail("live Gray8 camera source is not implemented")
    produced = threading.Event()
    advance = threading.Semaphore(0)
    def frames(device, count):
        indices = (0, 1, 2, 7, 8, 3, 4)
        for position, index in enumerate(indices):
            yield index, np.full((2, 2), index, dtype=np.uint8)
            if position < 5:
                advance.acquire()
        produced.set()
    c = LatestCamera("camera", frames=frames)
    try:
        after = -1
        for expected in (0, 1, 2, 7, 8):
            frame = c.read(after=after, timeout=.5)
            assert frame[0] == expected
            after = frame[0]
            advance.release()
        assert produced.wait(timeout=.5), "camera generator did not finish"
        assert c.stats() == {
            "captured_total": 7,
            "overwrite_drops": 4,
            "sequence_errors": 1,
        }
    finally:
        advance.release()
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
