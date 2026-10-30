import io
import json
import struct
import threading
import time

import numpy as np
import pytest

from frame_protocol import (
    FrameProtocolError,
    LatestFrameHub,
    pack_frame,
    read_frame,
)


def make_frame(frame_id=7, value=12):
    return {
        "frame_id": frame_id,
        "session_id": "session-A",
        "source": "camera",
        "result": {"count": 2},
        "config": {"threshold": 90},
        "gray": np.full((240, 320), value, dtype=np.uint8),
        "mask": np.full((240, 320), 255 - value, dtype=np.uint8),
    }


class ShortReader:
    """File-like stream that deliberately returns fewer bytes than requested."""

    def __init__(self, data, max_chunk=13):
        self._stream = io.BytesIO(data)
        self._max_chunk = max_chunk

    def read(self, size=-1):
        return self._stream.read(min(size, self._max_chunk))


def test_round_trip_preserves_metadata_and_images():
    frame = make_frame()
    decoded = read_frame(io.BytesIO(pack_frame(frame)))

    assert {k: decoded[k] for k in ("frame_id", "session_id", "source", "result", "config")} == {
        k: frame[k] for k in ("frame_id", "session_id", "source", "result", "config")
    }
    assert np.array_equal(decoded["gray"], frame["gray"])
    assert np.array_equal(decoded["mask"], frame["mask"])
    assert decoded["gray"].dtype == np.uint8
    assert decoded["gray"].shape == (240, 320)
    assert decoded["mask"].shape == (240, 320)


def test_round_trip_supports_full_resolution_dynamic_frames():
    frame = make_frame(value=31)
    frame["gray"] = np.full((400, 640), 31, dtype=np.uint8)
    frame["mask"] = np.zeros((400, 640), dtype=np.uint8)
    frame["mask"][15:170, 60:233] = 255
    frame["mask"][399, 639] = 255
    encoded = pack_frame(frame)
    decoded = read_frame(ShortReader(encoded, max_chunk=4093))

    assert decoded["gray"].shape == (400, 640)
    assert decoded["mask"].shape == (400, 640)
    assert abs(int(decoded["gray"][399, 639]) - 31) <= 2
    assert np.array_equal(decoded["mask"], frame["mask"])
    assert len(encoded) < 400 * 640 * 2


def test_compressed_preview_rejects_nonbinary_mask():
    frame = make_frame()
    frame["gray"] = np.zeros((400, 640), dtype=np.uint8)
    frame["mask"] = np.full((400, 640), 224, dtype=np.uint8)
    with pytest.raises(FrameProtocolError, match="binary"):
        pack_frame(frame)


def test_camera_jpeg_is_reused_without_encoding_again(monkeypatch):
    import cv2
    frame = make_frame()
    frame["gray"] = np.full((400, 640), 17, dtype=np.uint8)
    frame["mask"] = np.zeros_like(frame["gray"])
    ok, jpeg = cv2.imencode(".jpg", frame["gray"])
    assert ok
    frame["preview_jpeg"] = jpeg.tobytes()
    monkeypatch.setattr(cv2, "imencode", lambda *_a, **_kw: pytest.fail("encoded twice"))
    decoded = read_frame(io.BytesIO(pack_frame(frame)))
    assert decoded["gray"].shape == (400, 640)
    assert abs(int(decoded["gray"][0, 0]) - 17) <= 2
    assert not decoded["mask"].any()


def test_compressed_preview_rejects_invalid_image_and_dimensions():
    frame = make_frame()
    frame["gray"] = np.zeros((400, 640), dtype=np.uint8)
    frame["mask"] = np.zeros_like(frame["gray"])
    encoded = bytearray(pack_frame(frame))
    metadata_size = struct.unpack_from("!I", encoded, 12)[0]
    encoded[24 + metadata_size:24 + metadata_size + 2] = b"xx"
    with pytest.raises(FrameProtocolError, match="valid image"):
        read_frame(io.BytesIO(encoded))
    encoded = bytearray(pack_frame(frame))
    struct.pack_into("!HH", encoded, 8, 65535, 65535)
    with pytest.raises(FrameProtocolError, match="dimensions"):
        read_frame(io.BytesIO(encoded))


def test_read_frame_handles_short_reads():
    decoded = read_frame(ShortReader(pack_frame(make_frame()), max_chunk=7))
    assert decoded["frame_id"] == 7
    assert decoded["gray"][0, 0] == 12


def test_optional_capture_time_round_trips_and_is_absent_by_default():
    frame = make_frame()
    frame["capture_time_ns"] = 1_786_123_456_789_012_345
    decoded = read_frame(io.BytesIO(pack_frame(frame)))
    assert decoded["capture_time_ns"] == frame["capture_time_ns"]

    decoded_without_timestamp = read_frame(io.BytesIO(pack_frame(make_frame())))
    assert "capture_time_ns" not in decoded_without_timestamp


@pytest.mark.parametrize("cut", [1, 8, 19, 24, 100])
def test_truncated_frame_is_rejected(cut):
    encoded = pack_frame(make_frame())
    with pytest.raises(FrameProtocolError):
        read_frame(io.BytesIO(encoded[:-cut]))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f.update(gray=np.zeros((240, 320), dtype=np.uint16)),
        lambda f: f.update(mask=np.zeros((239, 320), dtype=np.uint8)),
        lambda f: f.update(frame_id=-1),
        lambda f: f.update(capture_time_ns=-1),
        lambda f: f.update(capture_time_ns=True),
        lambda f: f.update(capture_time_ns=1.5),
        lambda f: f.update(capture_time_ns=2**64),
        lambda f: f.update(result=object()),
        lambda f: f.pop("session_id"),
    ],
)
def test_invalid_frames_are_rejected(mutate):
    frame = make_frame()
    mutate(frame)
    with pytest.raises((FrameProtocolError, TypeError, ValueError)):
        pack_frame(frame)


def test_invalid_wire_header_and_metadata_are_rejected():
    encoded = bytearray(pack_frame(make_frame()))
    encoded[4] = 99  # unsupported version
    with pytest.raises(FrameProtocolError):
        read_frame(io.BytesIO(encoded))

    encoded = bytearray(pack_frame(make_frame()))
    struct.pack_into("!I", encoded, 16, 1)  # wrong Binary8 byte count
    with pytest.raises(FrameProtocolError):
        read_frame(io.BytesIO(encoded))

    encoded = bytearray(pack_frame(make_frame()))
    encoded[20] = ord("!")  # malformed JSON at the beginning of metadata
    with pytest.raises(FrameProtocolError):
        read_frame(io.BytesIO(encoded))


def test_hub_only_retains_latest_frame_and_clients_are_counted():
    hub = LatestFrameHub()
    assert hub.client_count == 0
    with hub.client():
        assert hub.client_count == 1
        hub.publish(make_frame(frame_id=1, value=1))
        hub.publish(make_frame(frame_id=2, value=2))
        seq, encoded = hub.wait_next(after_seq=0, timeout_s=0)
        assert seq == 2
        assert read_frame(io.BytesIO(encoded))["frame_id"] == 2
        assert hub.wait_next(after_seq=seq, timeout_s=0) is None
    assert hub.client_count == 0
    hub.close()


def test_hub_metadata_size_tracks_latest_published_frame():
    hub = LatestFrameHub()
    first = make_frame(frame_id=1)
    first["result"] = {"value": "a"}
    hub.publish(first)
    first_size = hub.latest_metadata_size
    assert first_size == len(json.dumps(
        {key: first[key] for key in (
            "frame_id", "session_id", "source", "result", "config")},
        ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))

    second = make_frame(frame_id=2)
    second["result"] = {"value": "b" * 137}
    second["gray"] = np.zeros((400, 640), dtype=np.uint8)
    second["mask"] = np.zeros((400, 640), dtype=np.uint8)
    hub.publish(second)
    expected_second_size = len(json.dumps(
        {key: second[key] for key in (
            "frame_id", "session_id", "source", "result", "config")},
        ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    assert hub.latest_metadata_size == expected_second_size > first_size
    hub.close()


def test_hub_serializes_before_reusable_input_buffer_changes():
    hub = LatestFrameHub()
    frame = make_frame(value=4)
    hub.publish(frame)
    frame["gray"][:] = 200
    frame["mask"][:] = 3

    _, encoded = hub.wait_next(after_seq=0, timeout_s=0)
    snapshot = read_frame(io.BytesIO(encoded))
    assert snapshot["gray"][0, 0] == 4
    assert snapshot["mask"][0, 0] == 251
    hub.close()


def test_wait_next_wakes_for_publish_and_close():
    hub = LatestFrameHub()
    output = []
    waiter = threading.Thread(target=lambda: output.append(hub.wait_next(0, 1.0)))
    waiter.start()
    time.sleep(0.02)
    hub.publish(make_frame(frame_id=3))
    waiter.join(timeout=1)
    assert not waiter.is_alive()
    assert output[0][0] == 1

    closed = LatestFrameHub()
    output.clear()
    waiter = threading.Thread(target=lambda: output.append(closed.wait_next(0, 1.0)))
    waiter.start()
    time.sleep(0.02)
    closed.close()
    waiter.join(timeout=1)
    assert output == [None]
