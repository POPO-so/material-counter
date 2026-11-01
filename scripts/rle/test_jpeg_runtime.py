from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.rle.jpeg_runtime import process_jpeg_frame, read_frg_detections
from scripts.wo0016.stream_counter import Detection


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

        def publish_overlay(self, frame_id, total_count, boxes, *, line_x):
            sequence.append(("publish", frame_id, total_count, boxes, line_x))

    class Tracker:
        line_x = 321

        def update_detections(self, frame_id, detections, *, capture_monotonic_ns):
            sequence.append(("track", frame_id, detections, capture_monotonic_ns))
            return SimpleNamespace(total_count=8, detections=tuple(detections))

    result = process_jpeg_frame(
        Transport(), Tracker(), b"\xff\xd8jpeg\xff\xd9", 27,
        capture_monotonic_ns=123456,
    )

    assert result.total_count == 8
    assert sequence[0] == ("transfer", 27, False)
    assert sequence[1][0:2] == ("track", 27)
    assert sequence[2] == ("publish", 27, 8, [(10, 20, 19, 29)], 321)
