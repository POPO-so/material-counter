"""Focused tests for the offline camera replay verifier."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


def verifier_module():
    assert importlib.util.find_spec("verify_split_hmi_replay") is not None, (
        "offline split-HMI replay verifier is not implemented"
    )
    return importlib.import_module("verify_split_hmi_replay")


def test_gray_threshold_is_binary8_and_preserves_frame_order():
    verifier = verifier_module()
    gray = np.zeros((2, 240, 320), dtype=np.uint8)
    gray[0, 0, :4] = [0, 127, 128, 255]
    gray[1, 0, :4] = [255, 128, 127, 0]

    actual = verifier.threshold_gray8(gray)

    np.testing.assert_array_equal(actual[:, 0, :4], np.array([
        [0, 0, 255, 255], [255, 255, 0, 0],
    ], dtype=np.uint8))
    assert actual.shape == gray.shape


def test_missing_captures_write_nonpass_receipt_and_return_nonzero(tmp_path):
    verifier = verifier_module()
    capture_root = tmp_path / "captures"
    capture_root.mkdir()
    output = tmp_path / "replay"

    exit_code = verifier.main([
        "--capture-root", str(capture_root), "--output", str(output),
    ])

    assert exit_code == 2
    receipt = json.loads((output / "replay-receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "NOT_RUN_MISSING_INPUT"
    assert len(receipt["missing_inputs"]) == 3
    assert all(item["expected_sha256"] for item in receipt["missing_inputs"])
    assert receipt["hardware_actions"] == 0


def test_archive_hash_mismatch_records_failure_without_replay(tmp_path):
    verifier = verifier_module()
    capture_root = tmp_path / "captures"
    capture_dir = capture_root / "test"
    capture_dir.mkdir(parents=True)
    archive = capture_dir / "camera_gray_only.npz"
    gray = np.zeros((2, 240, 320), dtype=np.uint8)
    np.savez(archive, input_gray320x240=gray,
             monotonic_ns=np.array([10, 20], dtype=np.int64),
             wall_ns=np.array([30, 40], dtype=np.int64),
             read_ms=np.array([1.0, 1.0], dtype=np.float32))
    (capture_dir / "manifest.json").write_text(json.dumps({
        "status": "COMPLETE", "scope": "CAMERA_ONLY_NOT_PL_BINARY8",
        "frame_count": 2, "compact": True,
        "archive": archive.name,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    spec = verifier.CaptureSpec(
        name="test", directory="test", sha256="f" * 64, frame_stop=2,
        baseline_event_frames=(), split_event_frames=(), frame_count=2,
    )

    output = tmp_path / "replay"
    receipt = verifier.verify_capture_root(capture_root, output, capture_specs=(spec,))

    assert receipt["status"] == "NOT_RUN_INPUT_INVALID"
    assert "SHA-256" in receipt["errors"][0]
    assert (output / "replay-receipt.json").is_file()


def test_wrong_event_count_fails_and_frame_metadata_reaches_report():
    verifier = verifier_module()
    spec = verifier.CaptureSpec(
        name="empty", directory="empty", sha256="0" * 64, frame_stop=3,
        baseline_event_frames=(), split_event_frames=(1,), frame_count=3,
    )
    gray = np.zeros((3, 240, 320), dtype=np.uint8)
    metadata = {
        "monotonic_ns": np.array([101, 202, 303], dtype=np.int64),
        "wall_ns": np.array([1001, 2002, 3003], dtype=np.int64),
        "read_ms": np.array([1.25, 1.5, 1.75], dtype=np.float32),
    }

    with pytest.raises(verifier.ReplayValidationError,
                       match=r"expected count 1; got 0 with event frames \[\]") as exc:
        verifier.replay_gray_frames(gray, metadata, spec)
    assert exc.value.report["split_enabled_total"] == 0
    assert exc.value.report["split_enabled_event_frames"] == []
    assert exc.value.report["frames"][0]["monotonic_ns"] == 101

    spec = verifier.CaptureSpec(
        name="empty", directory="empty", sha256="0" * 64, frame_stop=3,
        baseline_event_frames=(), split_event_frames=(), frame_count=3,
    )
    replay = verifier.replay_gray_frames(gray, metadata, spec)
    assert [frame["frame_id"] for frame in replay["frames"]] == [0, 1, 2]
    assert [frame["monotonic_ns"] for frame in replay["frames"]] == [101, 202, 303]
    assert [frame["wall_ns"] for frame in replay["frames"]] == [1001, 2002, 3003]
    assert [frame["read_ms"] for frame in replay["frames"]] == [1.25, 1.5, 1.75]
    assert all(frame["monitor"]["mode"] == "mock" for frame in replay["frames"])
    assert all(frame["monitor"]["camera"] == "disabled" for frame in replay["frames"])


def test_single_round_and_hex_controls_do_not_split_or_double_count():
    verifier = verifier_module()

    controls = verifier.run_single_nut_controls()

    assert set(controls) == {"round", "hex"}
    for control in controls.values():
        assert control["source"] == "SYNTHETIC_CONTROL_NOT_RECORDED"
        assert control["max_raw_detections_per_frame"] == 1
        assert control["max_child_detections_per_frame"] == 1
        assert control["false_split_frames"] == []
        assert control["total_count"] == 1


def test_existing_output_is_never_overwritten(tmp_path):
    verifier = verifier_module()
    capture_root = tmp_path / "captures"
    capture_root.mkdir()
    output = tmp_path / "replay"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError, match="new output directory"):
        verifier.verify_capture_root(capture_root, output)

    assert marker.read_text(encoding="utf-8") == "existing"


def test_verified_archive_succeeds_without_optional_manifest(tmp_path):
    verifier = verifier_module()
    root = tmp_path / "captures"
    directory = root / "empty"
    directory.mkdir(parents=True)
    archive = directory / "camera_gray_only.npz"
    np.savez(archive, input_gray320x240=np.zeros((2, 240, 320), dtype=np.uint8),
             monotonic_ns=np.array([10, 20], dtype=np.int64),
             wall_ns=np.array([30, 40], dtype=np.int64),
             read_ms=np.array([1, 2], dtype=np.float32))
    spec = verifier.CaptureSpec(
        name="empty", directory="empty", sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        frame_stop=2, baseline_event_frames=(), split_event_frames=(), frame_count=2)
    receipt = verifier.verify_capture_root(root, tmp_path / "result", capture_specs=(spec,))
    assert receipt["status"] == "OFFLINE_REPLAY_MATCH"
    assert receipt["verified_inputs"][0]["manifest_sha256"] is None
    assert receipt["captures"][0]["split_enabled_total"] == 0
    fps = receipt["captures"][0]["frames"][1]["monitor"]["processing_fps"]
    assert fps is None or fps > 0  # Two tiny frames may share a Windows clock tick.


def test_output_inside_capture_evidence_is_rejected_without_writes(tmp_path):
    verifier = verifier_module()
    root = tmp_path / "captures"
    root.mkdir()
    output = root / "derived"
    with pytest.raises(ValueError, match="outside the capture evidence root"):
        verifier.verify_capture_root(root, output)
    assert not output.exists()
