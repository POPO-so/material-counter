"""Offline end-to-end checks for hollow-nut split detections in the live monitor."""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_live_monitor as live
import run_stream_counter as finite
import split_touching
import stream_counter
from live_monitor_state import LiveMonitorState
from stream_counter import MaskCounter


# Compact masks from the camera-only replay fixtures in test_stream_counter.py.
_PAIR_HEX = (
    "03f8003ff803ffe01fff81fffc0fffe0fe1f87e07c7e01e3e00fbf003df801f7c00fbe007df807c7c03e3f03e0fc7f07fff03fff80fff807ff801ffc0020800ffe007ff803ffe03fff03fff81fffe1f83f8f80fcfc03e7c01f3e00f9f003ef801f7c01fbf00f8f80fc7e0fc1fdfe0fffe07fff01fff007ff001ff800"
)
_TRIPLE_HEX = (
    "000c0000001ffc000000fff8000003ffe000000fff8000007fff000003f8fe00000fc1f800003e03f00001f007c00007c01f00003f007e0000fc01f9fe03f007efff07c01f7ffc1f80fffff83f03ffffe0fe3fffffc1fffffc3f87ffffe07e0fffff00f83ffffc03e07fffe00f80ffdf801f07fffe007c1ffffc01f0fffff00f87ffffe07e1fffff81f87fffffffc3f87ffffe0fc0fffff87e01ffffe1f803ffff07c00ffff81f003e01c07c00f80001f803e00007e01f80001fc07c00003f83f00000ffff800001fffe000007fff000000fffc000001ffe0000007ff0000000"
)


def _captured_component(kind: str) -> np.ndarray:
    if kind == "pair":
        packed, shape, expected_area = _PAIR_HEX, (47, 21), 553
    elif kind == "triple":
        packed, shape, expected_area = _TRIPLE_HEX, (47, 38), 885
    else:
        raise ValueError(kind)
    bits = np.unpackbits(np.frombuffer(bytes.fromhex(packed), dtype=np.uint8))
    component = bits[: shape[0] * shape[1]].reshape(shape).astype(np.uint8) * 255
    assert np.count_nonzero(component) == expected_area
    return component


def _frame(component: np.ndarray, left: int, *, top: int = 108) -> np.ndarray:
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[top:top + component.shape[0], left:left + component.shape[1]] = component
    return mask


def _full_width_config(*flags: str) -> dict:
    args = finite._parser().parse_args(["--counting-profile", "full-width-mixed", *flags])
    return finite.counting_configuration(args)


@pytest.mark.parametrize(("kind", "expected_children"), [("pair", 2), ("triple", 3)])
def test_real_hollow_masks_keep_child_geometry_through_live_counter(kind, expected_children):
    component = _captured_component(kind)
    config = _full_width_config("--enable-touching-split")
    counter = MaskCounter(**config)
    result = counter.update(0, _frame(component, 100))

    pieces = split_touching.split_touching_component(
        component, min_split_area=config["min_split_area"],
        min_child_area=max(config["min_area"], config["min_split_area"] // 2),
    )
    expected = sorted(
        (
            int(np.count_nonzero(piece)),
            100 + int(np.nonzero(piece)[1].min()),
            100 + int(np.nonzero(piece)[1].max()),
            108 + int(np.nonzero(piece)[0].min()),
            108 + int(np.nonzero(piece)[0].max()),
            100 + float(np.nonzero(piece)[1].mean()),
            108 + float(np.nonzero(piece)[0].mean()),
        )
        for piece in pieces
    )

    def geometry(detection):
        return (
            detection.area, detection.x_min, detection.x_max, detection.y_min,
            detection.y_max, detection.centroid_x, detection.centroid_y,
        )

    assert len(pieces) == expected_children
    assert len(result.raw_detections) == expected_children
    assert len(result.detections) == expected_children
    assert sorted(map(geometry, result.raw_detections)) == expected
    assert sorted(map(geometry, result.detections)) == expected
    assert sum(d.area for d in result.raw_detections) == np.count_nonzero(component)
    assert len(result.tracks) == expected_children
    assert sorted(track["detection"]["area"] for track in result.tracks) == sorted(
        row[0] for row in expected
    )


def test_finite_and_live_cli_configs_share_split_gate_and_area_cap(monkeypatch):
    finite_default = _full_width_config()
    live_default = finite.counting_configuration(live.parser().parse_args([]))
    assert live_default == finite_default
    assert finite_default["enable_touching_split"] is False
    assert finite_default["min_split_area"] == 280
    assert finite_default["max_split_area"] == 900

    finite_enabled = _full_width_config("--enable-touching-split", "--min-split-area", "320")
    live_enabled = finite.counting_configuration(
        live.parser().parse_args(["--enable-touching-split", "--min-split-area", "320"])
    )
    assert live_enabled == finite_enabled
    assert live_enabled["enable_touching_split"] is True
    assert live_enabled["min_split_area"] == 320
    assert live_enabled["max_split_area"] == 900
    MaskCounter(**live_enabled)

    def unexpected(*args, **kwargs):
        pytest.fail("the default-off configuration must bypass the split helper")

    monkeypatch.setattr(stream_counter, "split_touching_component", unexpected)
    default_result = MaskCounter(**live_default).update(
        0, _frame(_captured_component("pair"), 100)
    )
    assert len(default_result.raw_detections) == 1
    assert default_result.raw_detections[0].area == 553


def test_live_counter_skips_split_below_minimum_area(monkeypatch):
    component = np.zeros((21, 10), dtype=np.uint8)
    component[:8, :] = 255
    component[13:, :] = 255
    component[8:13, 4:6] = 255
    assert np.count_nonzero(component) == 170
    config = _full_width_config("--enable-touching-split", "--min-split-area", "320")

    def unexpected(*args, **kwargs):
        pytest.fail("components below min_split_area must bypass the split helper")

    monkeypatch.setattr(stream_counter, "split_touching_component", unexpected)
    result = MaskCounter(**config).update(0, _frame(component, 100))
    assert len(result.raw_detections) == 1
    assert result.raw_detections[0].area == 170


def test_live_counter_skips_split_above_maximum_area(monkeypatch):
    component = np.full((30, 31), 255, dtype=np.uint8)
    assert np.count_nonzero(component) == 930
    config = _full_width_config("--enable-touching-split")
    # Isolate the splitter cap from the profile's separate max_area CCL filter.
    config["max_area"] = 1200

    def unexpected(*args, **kwargs):
        pytest.fail("components above max_split_area must bypass the split helper")

    monkeypatch.setattr(stream_counter, "split_touching_component", unexpected)
    result = MaskCounter(**config).update(0, _frame(component, 100))
    assert len(result.raw_detections) == 1
    assert result.raw_detections[0].area == 930


@pytest.mark.parametrize(("kind", "expected_count"), [("pair", 2), ("triple", 3)])
def test_split_crossings_reach_live_telemetry_and_render_child_labels(
    kind, expected_count, monkeypatch, tmp_path
):
    component = _captured_component(kind)
    counter = MaskCounter(**_full_width_config("--enable-touching-split"))
    now = [0.0]
    state = LiveMonitorState(mode="mock", history_limit=32, clock=lambda: now[0])
    frame_results = {}
    display_results = {}

    for frame_id, left in enumerate(range(70, 201, 10)):
        now[0] = frame_id / 20
        mask = _frame(component, left)
        result = counter.update(frame_id, mask)
        frame_results[frame_id] = result
        display_results[frame_id] = state.complete_frame(result)

    count_events = [event for result in frame_results.values() for event in result.events]
    assert len(count_events) == expected_count
    assert {event.track_id for event in count_events} == set(range(1, expected_count + 1))
    crossing_area_by_track = {}
    for event in count_events:
        track = next(track for track in frame_results[event.frame_id].tracks
                     if track["track_id"] == event.track_id)
        crossing_area_by_track[event.track_id] = track["detection"]["area"]
        assert track["forward_velocity"] == pytest.approx(10.0)

    telemetry = state.snapshot()["dashboard"]
    assert telemetry["total_count"] == expected_count
    assert telemetry["px_per_frame"] == pytest.approx(10.0)
    assert telemetry["px_per_second"] == pytest.approx(200.0)
    assert telemetry["last_crossing_area_px"] in {
        track["detection"]["area"]
        for event in count_events
        for track in frame_results[event.frame_id].tracks
        if track["track_id"] == event.track_id
    }
    crossing_logs = [row for row in telemetry["events"] if "越线计数" in row["message"]]
    assert len(crossing_logs) == expected_count
    for event in count_events:
        log = next(row for row in crossing_logs if row["track_id"] == event.track_id)
        assert log["area_px"] == crossing_area_by_track[event.track_id]
        assert f"T{event.track_id}" in log["message"]
        assert f"{crossing_area_by_track[event.track_id]}px" in log["message"]
        assert log["px_per_frame"] == pytest.approx(10.0)
    assert telemetry["last_crossing_area_px"] == crossing_logs[-1]["area_px"]

    import mjpeg_preview

    texts = []
    rectangles = []
    original_put_text = mjpeg_preview.cv2.putText
    original_rectangle = mjpeg_preview.cv2.rectangle

    def record_text(image, text, *args, **kwargs):
        texts.append(str(text))
        return original_put_text(image, text, *args, **kwargs)

    def record_rectangle(image, first, second, *args, **kwargs):
        rectangles.append((tuple(first), tuple(second)))
        return original_rectangle(image, first, second, *args, **kwargs)

    monkeypatch.setattr(mjpeg_preview.cv2, "putText", record_text)
    monkeypatch.setattr(mjpeg_preview.cv2, "rectangle", record_rectangle)
    rendered = mjpeg_preview.render_preview_frame(
        _frame(component, 200), total_count=expected_count, line_y=120,
        source_gray=np.zeros((240, 320), np.uint8),
        count_axis="x", line_x=160, tracks=display_results[13].tracks,
        roi_top=0, roi_bottom=240,
    )
    assert rendered.shape == (240, 320, 3)
    assert mjpeg_preview.cv2.imwrite(str(tmp_path / f"{kind}-split-live-tracks.png"), rendered)
    for track in display_results[13].tracks:
        detection = track["detection"]
        label = f"T{track['track_id']} {detection['area']}px"
        assert label in texts
        assert ((detection["x_min"], detection["y_min"]),
                (detection["x_max"], detection["y_max"])) in rectangles


def test_prediction_gap_recovery_tracks_real_split_children():
    component = _captured_component("pair")
    config = _full_width_config("--enable-touching-split")
    config["max_missing"] = 0
    assert config["prediction_max_missing"] >= 1
    counter = MaskCounter(**config)
    now = [0.0]
    state = LiveMonitorState(mode="mock", history_limit=8, clock=lambda: now[0])
    pieces = split_touching.split_touching_component(
        component, min_split_area=config["min_split_area"],
        min_child_area=max(config["min_area"], config["min_split_area"] // 2),
    )
    max_center_x = max(float(np.nonzero(piece)[1].mean()) for piece in pieces)
    left_before_gap = int(160 - config["hysteresis"] - 5 - max_center_x)

    before_gap = counter.update(0, _frame(component, left_before_gap - 10))
    state.complete_frame(before_gap)
    now[0] = 0.1
    before_gap = counter.update(1, _frame(component, left_before_gap))
    state.complete_frame(before_gap)
    assert len(before_gap.tracks) == 2
    assert all(track["confirmed"] and track["armed"] for track in before_gap.tracks)

    now[0] = 0.2
    missing = counter.update(2, np.zeros((240, 320), dtype=np.uint8))
    state.complete_frame(missing)
    now[0] = 0.3
    recovered = counter.update(3, _frame(component, left_before_gap + 20))
    state.complete_frame(recovered)

    assert len(recovered.raw_detections) == 2
    assert len(recovered.events) == 2
    assert all(event.inferred_gap for event in recovered.events)
    assert {event.track_id for event in recovered.events} == {1, 2}
    telemetry = state.snapshot()["dashboard"]
    assert telemetry["total_count"] == 2
    assert telemetry["last_crossing_area_px"] in {
        track["detection"]["area"] for track in recovered.tracks
    }
    gap_logs = [row for row in telemetry["events"] if "越线计数" in row["message"]]
    assert len(gap_logs) == 2
    assert all("触发速度预测跨帧恢复" in row["message"] for row in gap_logs)
    assert all(row["px_per_frame"] == pytest.approx(10.0) for row in gap_logs)


@pytest.mark.parametrize("scenario", ["clustered", "right-edge"])
def test_three_child_preview_labels_do_not_overlap_or_leave_frame(
    scenario, monkeypatch, tmp_path
):
    import mjpeg_preview

    component = _captured_component("triple")
    left = 180 if scenario == "clustered" else 319 - int(np.nonzero(component)[1].max())
    mask = _frame(component, left)
    counter = MaskCounter(**_full_width_config("--enable-touching-split"))
    result = counter.update(0, mask)
    assert len(result.tracks) == 3
    if scenario == "right-edge":
        assert max(track["detection"]["x_max"] for track in result.tracks) == 319

    draw_calls = []
    original_put_text = mjpeg_preview.cv2.putText

    def record_text(image, text, origin, font_face, font_scale, color, thickness, *args, **kwargs):
        if len(str(text)) > 1 and str(text)[0] == "T" and str(text)[1].isdigit():
            draw_calls.append(
                (str(text), tuple(origin), font_face, font_scale, thickness)
            )
        return original_put_text(
            image, text, origin, font_face, font_scale, color, thickness, *args, **kwargs
        )

    monkeypatch.setattr(mjpeg_preview.cv2, "putText", record_text)
    rendered = mjpeg_preview.render_preview_frame(
        mask, total_count=0, line_y=120, source_gray=np.zeros((240, 320), np.uint8),
        count_axis="x", line_x=160, tracks=result.tracks, roi_top=0, roi_bottom=240,
    )
    assert mjpeg_preview.cv2.imwrite(
        str(tmp_path / f"triple-{scenario}-labels.png"), rendered
    )
    assert len(draw_calls) == 3

    measured = []
    violations = []
    for text, (x, baseline_y), font_face, font_scale, thickness in draw_calls:
        (text_width, text_height), baseline = mjpeg_preview.cv2.getTextSize(
            text, font_face, font_scale, thickness
        )
        rect = (x, baseline_y - text_height, x + text_width, baseline_y + baseline)
        measured.append((text, rect))
        if rect[0] < 0 or rect[1] < 0 or rect[2] > 320 or rect[3] > 240:
            violations.append(f"{text} leaves the 320x240 frame: {rect}")

    for index, (left_text, left_rect) in enumerate(measured):
        for right_text, right_rect in measured[index + 1:]:
            # Reserve two pixels for anti-aliased glyph edges and clear visual separation.
            clearance = 2
            overlaps = not (
                left_rect[2] + clearance <= right_rect[0]
                or right_rect[2] + clearance <= left_rect[0]
                or left_rect[3] + clearance <= right_rect[1]
                or right_rect[3] + clearance <= left_rect[1]
            )
            if overlaps:
                violations.append(
                    f"{left_text} {left_rect} overlaps {right_text} {right_rect}"
                )
    assert not violations, "; ".join(violations)
