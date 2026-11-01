from __future__ import annotations

from types import SimpleNamespace
import sys

import cv2
import numpy as np
import pytest

from scripts.rle.jpeg_runtime import process_jpeg_frame, read_frg_detections
from scripts.wo0016.stream_counter import Detection


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    from scripts.rle.components import build_native
    return build_native(tmp_path_factory.mktemp("jpeg-counter")
                        / ("rle.dll" if sys.platform == "win32" else "rle.so"))


class FeatureControl:
    def __init__(self, *, frame_id=27, state=1, records=None):
        self.values = {
            0x0200: 0x46524731,
            0x0204: state,
            0x0208: frame_id,
            0x020C: len(records or []),
            0x0214: 3,
        }
        self.events = []
        for index, record in enumerate(records or []):
            for word_index, word in enumerate(record):
                self.values[0x1000 + 4 * (8 * index + word_index)] = word

    def read(self, offset):
        self.events.append(("read", offset))
        return self.values.get(offset, 0)


def record(*, area=100, sum_x=3200, sum_y=4200,
           x0=10, x1=19, y0=20, y1=29, frame_id=27):
    return [area, sum_x, sum_y, x0 | (x1 << 16), y0 | (y1 << 16), frame_id, 0, 0]


def test_reads_and_validates_fixed_frg1_records():
    control = FeatureControl(records=[record(sum_x=1450, sum_y=2450)])

    detections = read_frg_detections(control, 27)

    assert detections == [Detection(10, 19, 20, 29, 100, 14.5, 24.5)]
    assert control.events[-3:] == [("read", 0x0204), ("read", 0x0208), ("read", 0x020C)]


@pytest.mark.parametrize(
    "control, frame_id, error",
    [
        (FeatureControl(frame_id=26, records=[record(frame_id=26)]), 27, "frame ID"),
        (FeatureControl(state=3, records=[record()]), 27, "overflow"),
        (FeatureControl(records=[record(sum_x=1450, sum_y=2450, frame_id=26)]), 27, "record frame ID"),
        (FeatureControl(records=[[0, 0, 0, 0, 0, 27, 0, 0]]), 27, "geometry"),
    ],
)
def test_rejects_stale_overflow_or_malformed_frg(control, frame_id, error):
    with pytest.raises(RuntimeError, match=error):
        read_frg_detections(control, frame_id)


def test_process_frame_tracks_pl_features_then_commits_matching_hdmi_result():
    feature_control = FeatureControl(records=[record(sum_x=1450, sum_y=2450)])
    sequence = []

    class Transport:
        preview_control = feature_control

        def transfer(self, jpeg, frame_id, **kwargs):
            sequence.append(("transfer", frame_id, kwargs.get("auto_accept")))
            return SimpleNamespace(frame_id=frame_id, valid=True, status=0)

        def publish_overlay(self, frame_id, boxes, *, line_x):
            sequence.append(("publish", frame_id, boxes, line_x))

    class Tracker:
        line_x = 321

        def update_rle(self, frame, *, capture_monotonic_ns):
            sequence.append(("track", frame.frame_id, capture_monotonic_ns))
            return SimpleNamespace(total_count=8,
                detections=(Detection(10, 19, 20, 29, 100, 14.5, 24.5),))

    result = process_jpeg_frame(
        Transport(), Tracker(), b"\xff\xd8jpeg\xff\xd9", 27,
        capture_monotonic_ns=123456,
    )

    assert result.total_count == 8
    assert sequence[0] == ("transfer", 27, False)
    assert sequence[1][0:2] == ("track", 27)
    assert sequence[2] == ("publish", 27, [(10, 20, 19, 29)], 321)


def _ring_mask(centers, outer=24, inner=8):
    mask = np.zeros((400, 640), np.uint8)
    for cx, cy in centers:
        cv2.circle(mask, (cx, cy), outer // 2, 255, -1)
        cv2.circle(mask, (cx, cy), inner // 2, 0, -1)
    return mask


@pytest.mark.parametrize("centers,expected", [
    ([(100, 200), (121, 201)], 2),
    ([(100, 200), (121, 200), (142, 200), (163, 200)], 4),
])
def test_process_frame_splits_touching_rings_via_run_based_counter(library, centers, expected):
    from scripts.standalone.count_controls import make_runtime_counter
    from scripts.rle.protocol import encode_mask, decode_packet

    mask = _ring_mask(centers)
    frame = decode_packet(encode_mask(mask, frame_id=0))
    control = FeatureControl(frame_id=0, state=1)  # FRG empty and valid

    class Transport:
        preview_control = control
        last_boxes = None

        def transfer(self, jpeg, frame_id, **kwargs):
            return frame

        def publish_overlay(self, frame_id, boxes, *, line_x):
            self.last_boxes = boxes

    transport = Transport()
    tracker = make_runtime_counter(library, profile="field-v1")
    result = process_jpeg_frame(transport, tracker, b"\xff\xd8j\xff\xd9", 0,
                                capture_monotonic_ns=1)

    assert len(result.detections) == expected
    assert len(transport.last_boxes) == expected
