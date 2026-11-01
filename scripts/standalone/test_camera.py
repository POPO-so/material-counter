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
            yield index, np.full((400, 640), index, dtype=np.uint8)
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


def _jpeg(value):
    source = np.full((400, 640), value, dtype=np.uint8)
    ok, encoded = cv2.imencode('.jpg', source)
    assert ok
    return encoded


def test_latest_camera_decodes_only_newest_compressed_packet_once(monkeypatch):
    packets = [_jpeg(v) for v in (20, 90, 180)]
    consumed = []
    all_published = threading.Event()
    original = cv2.imdecode
    def tracked_decode(data, flags):
        consumed.append(bytes(data))
        return original(data, flags)
    monkeypatch.setattr(cv2, 'imdecode', tracked_decode)
    def frames(device, count):
        yield from packets
        all_published.set()
        time.sleep(.1)
    c = LatestCamera('camera', frames=frames)
    try:
        assert all_published.wait(.5)
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


def test_latest_camera_times_out_when_decode_exceeds_read_deadline_and_caches_result(monkeypatch):
    import scripts.standalone.camera as camera_module
    packet = _jpeg(112)
    published = threading.Event()
    calls = []
    original = camera_module.decode_camera_frame
    def slow_decode(raw):
        calls.append(1)
        time.sleep(.06)
        return original(raw)
    monkeypatch.setattr(camera_module, 'decode_camera_frame', slow_decode)
    def frames(device, count):
        yield packet
        published.set()
    c = LatestCamera('camera', frames=frames)
    try:
        assert published.wait(.5)
        with pytest.raises(TimeoutError, match='no fresh real-camera Gray8 frame'):
            c.read(timeout=.02)
        cached = c.read(timeout=.5)
        assert cached[0] == 0
        assert np.array_equal(cached[1], original(packet))
        assert len(calls) == 1
    finally:
        c.close()


def test_latest_camera_clears_decode_state_and_reraises_control_flow_baseexception(monkeypatch):
    import scripts.standalone.camera as camera_module
    packet = _jpeg(61)
    published = threading.Event()
    original = camera_module.decode_camera_frame
    abort = KeyboardInterrupt('decode interrupted')
    def interrupt_decode(_raw):
        raise abort
    monkeypatch.setattr(camera_module, 'decode_camera_frame', interrupt_decode)
    def frames(device, count):
        yield packet
        published.set()
    c = LatestCamera('camera', frames=frames)
    try:
        assert published.wait(.5)
        with pytest.raises(KeyboardInterrupt, match='decode interrupted') as caught:
            c.read(timeout=.5)
        assert caught.value is abort
        assert c._decode_in_progress is False
        monkeypatch.setattr(camera_module, 'decode_camera_frame', original)
        assert c.read(timeout=.5)[0] == 0
    finally:
        c.close()


@pytest.mark.parametrize('source_mode', ['ended', 'idle'])
def test_latest_camera_times_out_when_source_publishes_no_packet(source_mode):
    source_started = threading.Event()
    release_source = threading.Event()
    def frames(device, count):
        source_started.set()
        if source_mode == 'idle':
            release_source.wait(.5)
        if False:
            yield None
    c = LatestCamera('camera', frames=frames)
    try:
        assert source_started.wait(.5)
        with pytest.raises(TimeoutError, match='no fresh real-camera Gray8 frame'):
            c.read(timeout=.03)
        assert c._latest is None and c._decoded is None
    finally:
        release_source.set()
        c.close()


def test_latest_camera_deduplicates_concurrent_reads_for_selected_packet(monkeypatch):
    import scripts.standalone.camera as camera_module
    packet = _jpeg(99)
    published = threading.Event()
    decoding = threading.Event()
    release_decode = threading.Event()
    waiter_waiting = threading.Event()
    decode_calls = []
    original_decode = camera_module.decode_camera_frame
    def blocked_decode(raw):
        decode_calls.append(1)
        decoding.set()
        assert release_decode.wait(.5)
        return original_decode(raw)
    def frames(device, count):
        yield packet
        published.set()
    c = LatestCamera('camera', frames=frames)
    monkeypatch.setattr(camera_module, 'decode_camera_frame', blocked_decode)
    original_wait = c._condition.wait
    def watched_wait(timeout=None):
        waiter_waiting.set()
        return original_wait(timeout)
    monkeypatch.setattr(c._condition, 'wait', watched_wait)
    results = []
    errors = []
    def read_frame():
        try:
            results.append(c.read(timeout=.8))
        except BaseException as error:
            errors.append(error)
    first = threading.Thread(target=read_frame)
    second = threading.Thread(target=read_frame)
    try:
        assert published.wait(.5)
        first.start()
        assert decoding.wait(.5)
        second.start()
        assert waiter_waiting.wait(.5)
        release_decode.set()
        first.join(.5)
        second.join(.5)
        assert not first.is_alive() and not second.is_alive()
        assert not errors
        assert len(results) == 2
        assert results[0][0] == results[1][0] == 0
        assert np.array_equal(results[0][1], results[1][1])
        assert len(decode_calls) == 1
        assert not c._decode_in_progress
    finally:
        release_decode.set()
        c.close()
