from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from capture_dataset import CaptureWriter
from diagnose_count_discrepancy import (analyze_capture, _has_split_attempt,
                                        _markdown_report, main as diagnose_main)
from stream_counter import MaskCounter
from dataclasses import asdict


def _metadata(frame_id: int, *, tracks=None, events=None, associations=None,
              diagnostics=None, **extra):
    value = {
        "frame_id": frame_id, "source_sequence": frame_id,
        "capture_monotonic_ns": 1_000_000 + frame_id,
        "capture_wall_time_ns": 2_000_000 + frame_id,
        "camera_read_ms": 1.0, "capture_timestamp_kind": "host_monotonic",
        "dma_complete_monotonic_ns": 1_000_001 + frame_id,
        "dma_slot_index": frame_id % 2,
        "software_pairing": {"input_sequence_equals_dma_frame_id": True,
                              "input_and_output_use_same_slot": True,
                              "mask_echoes_frame_id": False},
        "resolution": {"width": 320, "height": 240, "dtype": "uint8"},
        "threshold": 128,
        "algorithm_config": {"line_x": 160, "count_axis": "x", "direction": 1,
                              "roi_top": 30, "roi_bottom": 210, "min_area": 15,
                              "reference_area": 111, "motion_timebase_s": None},
        "roi": {"top": 30, "bottom": 210}, "dma_status": {"frame_id": frame_id},
        "packer_status": {"frame_id": frame_id, "validation": "PASS"},
        "raw_detections": [], "detections": [],
        "associations": associations or [], "tracks": tracks or [],
        "events": events or [], "suppressed_events": [], "total_count": 0,
        "frame_result": {"ccl_executed": True},
    }
    if diagnostics is not None:
        value["diagnostics"] = diagnostics
        value["rejected_components"] = diagnostics.get("rejected_components", [])
        value["split_attempts"] = diagnostics.get("split_attempts", [])
        value["ambiguous_associations"] = diagnostics.get("ambiguous_associations", [])
    value.update(extra)
    return value


def _track(track_id: int, x: float, area: int, *, armed=False, confirmed=True,
           counted=False, finished=False, born_past_line=False):
    return {"track_id": track_id, "armed": armed, "confirmed": confirmed,
            "counted": counted, "finished": finished, "last_frame": 0,
            "born_past_line": born_past_line,
            "detection": {"x_min": int(x - 5), "x_max": int(x + 5),
                          "y_min": 80, "y_max": 90, "area": area,
                          "centroid_x": x, "centroid_y": 85}}


def _capture(path: Path, *, enriched=True, legacy_loss=False):
    writer = CaptureWriter(path, expected_frames=8,
                           config={"counter_config": {"line_x": 160, "count_axis": "x",
                                                      "direction": 1, "roi_top": 30,
                                                      "roi_bottom": 210, "min_area": 15,
                                                      "reference_area": 111,
                                                      "motion_timebase_s": None}},
                           queue_depth=16)
    for frame_id in range(8):
        gray = np.full((240, 320), 50 + frame_id, np.uint8)
        mask = np.zeros((240, 320), np.uint8)
        mask[80:90, min(315, 100 + frame_id):min(320, 110 + frame_id)] = 255
        tracks = []
        events = []
        associations = []
        diagnostics = None
        if enriched:
            diagnostics = {"rejected_components": [], "split_attempts": [],
                           "ambiguous_associations": [], "lost_tracks": []}
            if frame_id == 1:
                tracks = [_track(1, 162, 220, armed=True, counted=True)]
                events = [{"track_id": 1, "frame_id": 1,
                           "centroid_x": 162, "centroid_y": 85}]
            elif frame_id == 2:
                tracks = [_track(9, 162, 100, armed=True, counted=True)]
                events = [{"track_id": 9, "frame_id": 2,
                           "centroid_x": 162, "centroid_y": 85}]
            elif frame_id == 3:
                tracks = [_track(2, 157, 100, born_past_line=True)]
                associations = [{"track_id": 2, "detection_index": 0,
                                 "previous_frame": None, "kind": "new",
                                 "born_past_line": True}]
            elif frame_id == 4:
                tracks = [_track(2, 161, 100, born_past_line=True)]
                diagnostics = {
                    "rejected_components": [], "split_attempts": [],
                    "ambiguous_associations": [{"track_id": 3,
                                                  "detection_index": 0,
                                                  "reason": "multiple_candidates"}],
                    "lost_tracks": [{"track_id": 3, "armed": True,
                                     "confirmed": True, "counted": False,
                                     "detection": _track(3, 145, 90)["detection"]}],
                }
            elif frame_id == 5:
                diagnostics = {
                    "rejected_components": [{"detection": {"area": 14,
                                                               "centroid_x": 80,
                                                               "centroid_y": 85},
                                             "reason": "below_min_area"}],
                    "split_attempts": [], "ambiguous_associations": [],
                    "lost_tracks": [],
                }
                tracks = [_track(2, 162, 100, born_past_line=True),
                           _track(4, 130, 100)]
            elif frame_id == 6:
                tracks = [_track(2, 163, 100, born_past_line=True),
                           _track(4, 140, 170)]
            extra = {}
        else:
            if frame_id == 1:
                events = [{"track_id": 1, "frame_id": 1,
                           "centroid_x": 162, "centroid_y": 85}]
                if legacy_loss:
                    tracks = [_track(7, 145, 90, armed=True, counted=False)]
            elif legacy_loss and frame_id == 7:
                tracks = [_track(8, 145, 90, armed=True, counted=False)]
            extra = {}
        meta = _metadata(frame_id, tracks=tracks, events=events,
                         associations=associations, diagnostics=diagnostics, **extra)
        for track in meta["tracks"]:
            track["last_frame"] = frame_id
        writer.submit(frame_id, np.ascontiguousarray(gray),
                      np.ascontiguousarray(mask), meta)
    result = writer.close()
    assert result["status"] == "COMPLETE"


def test_analyze_capture_writes_candidate_audit_and_unoccluded_sheets(tmp_path):
    dataset = tmp_path / "capture"
    _capture(dataset)
    output = tmp_path / "diagnosis"

    report = analyze_capture(dataset, output_dir=output)

    assert report["status"] == "DIAGNOSTIC_ONLY"
    categories = {row["category"] for row in report["records"]}
    assert {"A", "B", "C", "D", "EVENT"} <= categories
    assert (output / "audit.json").is_file()
    assert (output / "audit.md").is_file()
    for row in report["records"]:
        sheet = output / row["contact_sheet"]
        assert sheet.is_file()
        image = cv2.imread(str(sheet), cv2.IMREAD_GRAYSCALE)
        assert image is not None and image.size
    assert any(row["frame_id"] == 1 and row["category"] == "A"
               for row in report["records"])
    assert any(row["frame_id"] == 3 and row["category"] == "B"
               for row in report["records"])
    assert any(row["category"] == "C" and row["frame_id"] == 4
               for row in report["records"])
    assert any(row["category"] == "D" and row["frame_id"] == 5
               for row in report["records"])
    event_frames = {row["frame_id"] for row in report["records"]
                    if row["category"] == "EVENT"}
    assert event_frames <= {int(frame_id) for frame_id in report["contact_sheets"]}
    assert report["diagnostic_field_coverage"]["lost_tracks"] == "present"
    markdown = (output / "audit.md").read_text(encoding="utf-8")
    assert "[打开联系表](contact-sheets/" in markdown


def test_contact_sheet_titles_do_not_overlap_and_pixels_start_below_headers(
        tmp_path, monkeypatch):
    import diagnose_count_discrepancy as diagnosis

    dataset = tmp_path / "capture"
    _capture(dataset)
    output = tmp_path / "diagnosis"
    positions = []
    original_put_text = cv2.putText

    def record_put_text(image, text, org, *args, **kwargs):
        positions.append((text, org))
        return original_put_text(image, text, org, *args, **kwargs)

    monkeypatch.setattr(diagnosis.cv2, "putText", record_put_text)
    report = analyze_capture(dataset, output_dir=output)

    image = cv2.imread(str(output / report["contact_sheets"]["1"]),
                       cv2.IMREAD_GRAYSCALE)
    assert image.shape == (24 + 2 * (44 + 240), 1920)
    assert image[68 + 100, 5] == 50  # frame 0 Gray8 remains unpainted
    assert image[68 + 100, 330] == 0  # adjacent Binary8 tile remains unpainted
    global_header_y = next(y for text, (_, y) in positions if text.startswith("anchor 1"))
    frame_header_y = next(y for text, (_, y) in positions if text.startswith("frame 0"))
    observations = next(text for text, _ in positions if text.startswith("current tracks: T1/A"))
    assert global_header_y == 17 and frame_header_y == 41
    assert global_header_y < frame_header_y < 68
    assert "T1/A220px" in observations


def test_legacy_capture_marks_missing_diagnostics_unknown_and_reports_window_edges(tmp_path):
    dataset = tmp_path / "legacy"
    _capture(dataset, enriched=False)
    output = tmp_path / "diagnosis"

    report = analyze_capture(dataset, output_dir=output)

    assert report["diagnostic_field_coverage"]["rejected_components"] == "missing"
    assert report["diagnostic_field_coverage"]["split_attempts"] == "missing"
    assert report["diagnostic_field_coverage"]["ambiguous_associations"] == "missing"
    assert report["diagnostic_field_coverage"]["lost_tracks"] == "missing"
    assert any("unknown" in text.lower() for text in report["limitations"])
    edge = next(row for row in report["records"] if row["category"] == "EVENT"
                and row["frame_id"] == 1)
    assert edge["window_missing_before"] == 3
    assert edge["window_missing_after"] == 0
    assert edge["window_missing_before"] == 3
    assert edge["window_missing_after"] == 0


def test_cli_output_json_references_all_contact_sheets(tmp_path):
    dataset = tmp_path / "capture"
    _capture(dataset)
    output = tmp_path / "diagnosis"

    report = analyze_capture(dataset, output_dir=output)
    stored = json.loads((output / "audit.json").read_text(encoding="utf-8"))

    assert stored["records"] == report["records"]
    assert all((output / row["contact_sheet"]).is_file() for row in stored["records"])


def test_existing_diagnosis_output_is_never_overwritten(tmp_path):
    dataset = tmp_path / "capture"
    _capture(dataset)
    output = tmp_path / "diagnosis"
    analyze_capture(dataset, output_dir=output)

    with pytest.raises(FileExistsError, match="already contains evidence"):
        analyze_capture(dataset, output_dir=output)


def test_nonfinite_threshold_is_rejected_and_markdown_escapes_table_pipes():
    with pytest.raises(ValueError, match="finite"):
        analyze_capture("unused", large_area_ratio=float("nan"))

    report = {
        "capture_dir": "capture", "frame_count": 1, "final_total_count": 0,
        "source_data": "gray", "diagnostic_field_coverage": {},
        "limitations": [],
        "records": [{"category": "D", "frame_id": 0, "track_id": None,
                     "evidence": {"reason": "one|two"}, "contact_sheet": "contact-sheets/frame.png"}],
    }
    markdown = _markdown_report(report)
    assert "one\\|two" in markdown
    assert "[打开联系表](contact-sheets/frame.png)" in markdown


def test_cli_rejects_nonfinite_threshold_and_existing_output(tmp_path, capsys):
    dataset = tmp_path / "capture"
    _capture(dataset)
    output = tmp_path / "diagnosis"

    with pytest.raises(SystemExit) as invalid:
        diagnose_main([str(dataset), "--output", str(output),
                       "--large-area-ratio", "nan"])
    assert invalid.value.code == 2
    assert "thresholds must be finite" in capsys.readouterr().err

    assert diagnose_main([str(dataset), "--output", str(output)]) == 0
    with pytest.raises(SystemExit) as duplicate:
        diagnose_main([str(dataset), "--output", str(output)])
    assert duplicate.value.code == 2
    assert "already contains evidence" in capsys.readouterr().err


def test_split_attempt_requires_parent_or_child_geometry_not_matching_area():
    parent = {"x_min": 120, "x_max": 140, "y_min": 80, "y_max": 100,
              "area": 220, "centroid_x": 130, "centroid_y": 90}
    child = {"x_min": 120, "x_max": 130, "y_min": 80, "y_max": 100,
             "area": 110, "centroid_x": 125, "centroid_y": 90}
    unrelated_same_area = {"x_min": 10, "x_max": 20, "y_min": 80, "y_max": 90,
                           "area": 110, "centroid_x": 15, "centroid_y": 85}
    unrelated = {"diagnostics": {"split_attempts": [{
        "area": 110, "detection": unrelated_same_area, "split_attempted": True,
        "accepted": True, "children": []}]}}
    matching_nested = {"diagnostics": {"split_attempts": [{
        "detection": parent, "split_attempted": True, "accepted": True,
        "children": [child]}]}}

    assert _has_split_attempt(unrelated, child) == (True, False)
    assert _has_split_attempt(matching_nested, child) == (True, True)


def test_born_past_line_is_reported_only_at_first_observation(tmp_path):
    dataset = tmp_path / "capture"
    _capture(dataset)

    report = analyze_capture(dataset, output_dir=tmp_path / "diagnosis")

    born = [row for row in report["records"] if row["category"] == "B"
            and row["track_id"] == 2]
    assert len(born) == 1
    assert born[0]["frame_id"] == 3


def test_legacy_track_disappearance_is_inferred_with_scope(tmp_path):
    dataset = tmp_path / "capture"
    _capture(dataset, enriched=False, legacy_loss=True)

    report = analyze_capture(dataset, output_dir=tmp_path / "diagnosis")

    lost = [row for row in report["records"] if row["category"] == "C"
            and row["track_id"] == 7]
    assert len(lost) == 1
    assert lost[0]["evidence"]["evidence_scope"] == "inferred_track_snapshot_disappearance"
    assert not any(row["category"] == "C" and row["track_id"] == 8
                   for row in report["records"])


def test_armed_track_crossing_line_without_event_is_candidate_a(tmp_path):
    dataset = tmp_path / "capture"
    _capture(dataset)
    from capture_dataset import load_dataset
    from diagnose_count_discrepancy import _detect_records
    manifest, captured = load_dataset(dataset)
    frames = list(captured)
    for frame_id, x in ((0, 150), (1, 166)):
        tracks = [_track(8, x, 100, armed=True, counted=False)]
        frames[frame_id] = type(frames[frame_id])(
            frame_id, frames[frame_id].gray, frames[frame_id].mask,
            {**frames[frame_id].metadata, "tracks": tracks, "events": []})

    records_out, _, _ = _detect_records(
        tuple(frames), manifest["config"]["counter_config"], min_area_margin=5,
        large_area_px=210, large_area_ratio=1.5, late_birth_x=150,
        area_jump_ratio=0.5)
    crossing = [row for row in records_out if row["category"] == "A"
                and row["track_id"] == 8]
    assert len(crossing) == 1
    assert crossing[0]["evidence"]["pattern"] == "armed_crossing_without_event"


def test_analyze_real_counter_diagnostics_shape(tmp_path):
    config = {"line_x": 160, "count_axis": "x", "direction": 1,
              "roi_mode": "full", "roi_top": 30, "roi_bottom": 210,
              "min_area": 15, "max_area": 400, "max_bbox_width": 85,
              "max_bbox_height": 85, "hysteresis": 2, "confirm_hits": 2,
              "max_missing": 1, "max_step": 48, "max_lateral_step": 10,
              "strict_lateral_gate": True, "motion_timebase_s": None,
              "observability": True}
    counter = MaskCounter(**config)
    writer = CaptureWriter(tmp_path / "counter-capture", expected_frames=7,
                           config={"counter_config": config}, queue_depth=16)
    for frame_id in range(7):
        mask = np.zeros((240, 320), np.uint8)
        x = 130 + frame_id * 5
        mask[80:90, x:x + 10] = 255
        result = counter.update(frame_id, np.ascontiguousarray(mask))
        metadata = _metadata(frame_id, tracks=list(result.tracks),
                             events=[asdict(event) for event in result.events],
                             associations=list(counter.last_associations),
                             diagnostics=result.diagnostics)
        metadata["algorithm_config"] = config
        metadata["raw_detections"] = [asdict(item) for item in result.raw_detections]
        metadata["detections"] = [asdict(item) for item in result.detections]
        metadata["total_count"] = result.total_count
        metadata["roi"] = {"top": result.roi_top, "bottom": result.roi_bottom}
        writer.submit(frame_id, np.full((240, 320), 100, np.uint8), mask, metadata)
    assert writer.close()["status"] == "COMPLETE"

    report = analyze_capture(tmp_path / "counter-capture",
                             output_dir=tmp_path / "counter-diagnosis")

    assert report["diagnostic_field_coverage"] == {
        "rejected_components": "present", "split_attempts": "present",
        "ambiguous_associations": "present", "lost_tracks": "present"}
    assert any(row["category"] == "EVENT" for row in report["records"])
