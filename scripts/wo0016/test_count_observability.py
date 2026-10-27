from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stream_counter import Detection, MaskCounter


def _detection(x: int, *, y: int = 100, width: int = 10,
               height: int = 10) -> Detection:
    return Detection(x, x + width - 1, y, y + height - 1,
                     width * height, x + (width - 1) / 2,
                     y + (height - 1) / 2)


def _moving_masks() -> list[np.ndarray]:
    masks = []
    for x in (110, 122, 134, 146, 158, 170):
        mask = np.zeros((240, 320), dtype=np.uint8)
        mask[100:110, x:x + 10] = 255
        masks.append(mask)
    return masks


def test_observability_keeps_counting_results_and_reports_stage_timings():
    masks = _moving_masks()
    plain = MaskCounter(count_axis="x", line_x=160)
    observed = MaskCounter(count_axis="x", line_x=160, observability=True)
    plain_results = [plain.update(frame_id, mask) for frame_id, mask in enumerate(masks)]
    observed_results = [observed.update(frame_id, mask)
                       for frame_id, mask in enumerate(masks)]

    assert [result.detections for result in observed_results] == [
        result.detections for result in plain_results
    ]
    assert [result.events for result in observed_results] == [
        result.events for result in plain_results
    ]
    assert observed.total_count == plain.total_count == 1
    assert set(observed_results[-1].stage_timings_ms) >= {
        "t_ccl", "t_split", "t_track", "split_calls"
    }
    assert observed_results[-1].stage_timings_ms["split_calls"] == 0
    assert all(observed_results[-1].stage_timings_ms[key] >= 0
               for key in ("t_ccl", "t_split", "t_track"))
    assert "rejected_components" in observed_results[-1].diagnostics


def test_observability_reports_small_rejected_and_roi_clipped_components():
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[100:103, 10:13] = 255  # 9 px: below min_area
    mask[100:110, 50:65] = 255  # 150 px: above max_area
    mask[30:50, 240:250] = 255  # straddles ROI top=40
    mask[215:225, 200:210] = 255  # outside ROI bottom=200
    counter = MaskCounter(observability=True, roi_mode="full", roi_top=40,
                          roi_bottom=200, min_area=12, max_area=120)

    result = counter.update(0, mask)

    rejected = result.diagnostics["rejected_components"]
    reasons = {item["reason"] for item in rejected}
    assert "below_min_area" in reasons
    assert "max_area" in reasons
    assert "partially_outside_roi" in reasons
    assert "outside_roi" in reasons
    assert all("detection" in item for item in rejected)
    partial = next(item for item in rejected
                   if item["reason"] == "partially_outside_roi")
    assert partial["rejected"] is False
    assert partial["partial_crop_only"] is True
    assert len(result.detections) == 1
    assert result.detections[0].y_min == 40


def test_observability_marks_objects_clipped_at_the_image_border():
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[100:110, 0:12] = 255
    counter = MaskCounter(observability=True, roi_mode="full")

    result = counter.update(0, mask)

    observations = result.diagnostics["boundary_observations"]
    assert len(observations) == 1
    assert observations[0]["frame_clipped_edges"] == ["left"]
    assert observations[0]["visible_in_roi"] is True
    assert result.detections[0].x_min == 0


def test_observability_records_a_failed_touching_split_without_changing_detection():
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[90:110, 120:140] = 255  # solid shape has no split evidence
    counter = MaskCounter(observability=True, roi_mode="full", count_axis="x",
                          guarded_merge=True, single_intact_area=95,
                          max_split_area=900, max_area=900)

    result = counter.update(0, mask)

    assert len(result.raw_detections) == 1
    assert len(result.detections) == 1
    attempt = result.diagnostics["split_attempts"][0]
    assert attempt["split_attempted"] is True
    assert attempt["accepted"] is False
    assert attempt["split_rejected_reason"] == "no_valid_partition"
    assert attempt["candidate_piece_count"] == 1


def test_observability_records_ambiguous_inferred_gap_associations():
    counter = MaskCounter(observability=True, count_axis="x", line_x=200,
                          max_step=40, max_missing=0,
                          prediction_max_missing=2)
    counter.update_detections(0, [_detection(100), _detection(120)])
    counter.update_detections(1, [_detection(105), _detection(125)])
    counter.update_detections(2, [])

    result = counter.update_detections(3, [_detection(130)])

    ambiguous = result.diagnostics["ambiguous_associations"]
    assert len(ambiguous) == 2
    assert {item["track_id"] for item in ambiguous} == {1, 2}
    assert all(item["detection_index"] == 0 for item in ambiguous)
    assert all(item["reason"] == "ambiguous_gap_match" for item in ambiguous)


def test_observability_explains_a_rejected_lateral_association_candidate():
    counter = MaskCounter(observability=True, count_axis="x", line_x=200,
                          max_lateral_step=5, strict_lateral_gate=True)
    counter.update_detections(0, [_detection(100)])
    counter.update_detections(1, [_detection(105)])

    result = counter.update_detections(2, [_detection(110, y=112)])

    candidate = next(item for item in result.diagnostics["association_candidates"]
                     if item["track_id"] == 1 and item["detection_index"] == 0)
    assert candidate["eligible"] is False
    assert "lateral_step_exceeded" in candidate["rejection_reasons"]
    assert candidate["lateral_px"] == 12
    assert candidate["max_lateral_px"] == 5
    assert candidate["predicted_centroid"] is not None
    assert candidate["prediction_distance_squared"] is not None


def test_observability_marks_tracks_born_past_line():
    counter = MaskCounter(observability=True, count_axis="x", line_x=160)

    result = counter.update_detections(0, [_detection(170)])

    assert result.tracks[0]["born_past_line"] is True
    association = result.diagnostics["associations"][-1]
    assert association["kind"] == "new"
    assert association["born_past_line"] is True


def test_observability_distinguishes_near_line_birth_from_past_line_birth():
    counter = MaskCounter(observability=True, count_axis="x", line_x=160)

    result = counter.update_detections(0, [_detection(154)])

    track = result.tracks[0]
    association = result.diagnostics["associations"][-1]
    assert track["born_past_line"] is False
    assert track["born_in_hysteresis_band"] is True
    assert track["born_counted_without_event"] is False
    assert track["counted"] is False
    assert association["born_past_line"] is False
    assert association["born_counted_without_event"] is False
    assert association["born_in_hysteresis_band"] is True


def test_observability_retains_lost_tracks_when_finished_tracks_are_discarded():
    counter = MaskCounter(observability=True, count_axis="x", line_x=200,
                          max_missing=0, discard_finished_tracks=True)
    counter.update_detections(0, [_detection(100)])

    result = counter.update_detections(1, [])

    assert result.tracks == ()
    lost = result.diagnostics["lost_tracks"]
    assert len(lost) == 1
    assert lost[0]["track_id"] == 1
    assert lost[0]["finished"] is True
    assert lost[0]["lost_reason"] == "missed_frame_budget_exceeded"
