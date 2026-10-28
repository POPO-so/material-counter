"""Offline mode selection contracts; these tests never access board hardware."""
import argparse

import pytest

import run_stream_counter as runner


def options(**kwargs):
    return argparse.Namespace(capture_write_mode="bounded_stream", capture_dir="capture",
                              wo0017_profile=None, diagnostic_probe=False,
                              physical_calibration=False, **kwargs)


def test_bounded_cli_is_explicit_and_defaults_stay_auto():
    assert runner._parser().parse_args([]).capture_write_mode == "auto"
    args = runner._parser().parse_args(["--capture-write-mode", "bounded_stream"])
    assert runner.resolve_capture_write_mode(args) == "bounded_stream"


@pytest.mark.parametrize("field,value", [("wo0017_profile", "b1"),
                                       ("wo0017_profile", "b2"),
                                       ("diagnostic_probe", True),
                                       ("physical_calibration", True)])
def test_formal_capture_profiles_allow_explicit_bounded_mode(field, value):
    args = options()
    setattr(args, field, value)
    assert runner.resolve_capture_write_mode(args) == "bounded_stream"


def test_legacy_resolution_preserves_formal_deferred_and_auto_streaming():
    args = options()
    args.capture_write_mode = "auto"
    assert runner.resolve_capture_write_mode(args) == "streaming_queue"
    args.wo0017_profile = "b2"
    assert runner.resolve_capture_write_mode(args) == "deferred_memory"
    args.wo0017_profile = None
    assert runner.resolve_capture_write_mode(args, calibration_mode=True) == "deferred_memory"
    args.capture_write_mode = "deferred"
    assert runner.resolve_capture_write_mode(args) == "deferred_memory"


def test_bounded_storage_evidence_does_not_read_full_run_arrays(tmp_path):
    from capture_dataset import CaptureWriter
    writer = CaptureWriter(tmp_path / "capture", expected_frames=1, config={},
                           source_frame_ids=(0,), write_mode="bounded_stream", ring_depth=4)
    try:
        evidence = runner.capture_storage_evidence(writer, write_mode="bounded_stream")
        assert evidence["write_mode"] == "bounded_stream"
        assert evidence["gray_bytes"] == evidence["binary_bytes"] == 4 * 240 * 320
        assert evidence["per_frame_files_written_during_sampling"] is True
        assert evidence["ring_depth"] == 4
        assert evidence["selected_source_frame_storage_bytes"] == 400 * 640
        assert evidence["writer_owned_image_array_bytes"] == 4 * 240 * 320 * 2 + 400 * 640
    finally:
        writer.close()


def test_explicit_bounded_mode_is_available_for_calibration_evidence():
    assert runner.resolve_capture_write_mode(
        options(), calibration_mode=True) == "bounded_stream"


def test_wo0016_formal_board_stage_allows_bounded_mode_without_touching_hardware():
    args = runner._parser().parse_args([
        "--mode", "board", "--capture-write-mode", "bounded_stream",
        "--authorize-stage", runner.WO16_STAGE, "--execute", "--approve-once"])
    assert runner.resolve_capture_write_mode(args) == "bounded_stream"


@pytest.mark.parametrize("mode,expected", [("auto", "streaming_queue"),
                                           ("deferred", "deferred_memory")])
def test_wo0016_legacy_stage_resolution_unchanged(mode, expected):
    args = options(authorize_stage=runner.WO16_STAGE)
    args.capture_write_mode = mode
    assert runner.resolve_capture_write_mode(args) == expected


def test_bounded_mode_does_not_bypass_board_stage_authorization(monkeypatch):
    args = runner._parser().parse_args(["--mode", "board", "--capture-write-mode", "bounded_stream"])
    assert runner.resolve_capture_write_mode(args) == "bounded_stream"

    def forbidden(*args, **kwargs):
        pytest.fail("board admission must stop before manifest or hardware checks")

    monkeypatch.setattr(runner.preflight, "validate_manifest", forbidden)
    with pytest.raises(ValueError, match="requires --execute --authorize-stage"):
        runner.execute_board_stream(args)


@pytest.mark.parametrize("field,value", [("wo0017_profile", "b1"),
                                       ("wo0017_profile", "b2"),
                                       ("diagnostic_probe", True),
                                       ("physical_calibration", True)])
def test_explicit_bounded_mode_resolves_for_formal_board_contexts(field, value):
    args = runner._parser().parse_args(["--mode", "board", "--capture-write-mode", "bounded_stream"])
    setattr(args, field, value)
    assert runner.resolve_capture_write_mode(args) == "bounded_stream"
