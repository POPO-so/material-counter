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
def test_existing_formal_contracts_reject_bounded_before_hardware(field, value):
    args = options()
    setattr(args, field, value)
    with pytest.raises(ValueError, match="bounded_stream"):
        runner.resolve_capture_write_mode(args)


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
                           write_mode="bounded_stream", ring_depth=4)
    try:
        evidence = runner.capture_storage_evidence(writer, write_mode="bounded_stream")
        assert evidence["write_mode"] == "bounded_stream"
        assert evidence["gray_bytes"] == evidence["binary_bytes"] == 4 * 240 * 320
        assert evidence["per_frame_files_written_during_sampling"] is True
        assert evidence["ring_depth"] == 4
    finally:
        writer.close()


def test_calibration_flag_cannot_silently_override_explicit_bounded():
    with pytest.raises(ValueError, match="bounded_stream"):
        runner.resolve_capture_write_mode(options(), calibration_mode=True)


def test_wo0016_formal_stage_rejects_bounded_even_with_operator_opt_in(monkeypatch):
    args = runner._parser().parse_args([
        "--mode", "board", "--capture-write-mode", "bounded_stream",
        "--authorize-stage", runner.WO16_STAGE, "--execute", "--approve-once"])
    with pytest.raises(ValueError, match="bounded_stream.*not authorized.*wo0016-stream"):
        runner.resolve_capture_write_mode(args)

    def forbidden(*args, **kwargs):
        pytest.fail("permit/hardware gate reached before stage rejection")

    monkeypatch.setattr(runner, "validate_operator_gate_launcher_sha256", forbidden)
    with pytest.raises(ValueError, match="bounded_stream.*not authorized"):
        runner.execute_board_stream(args)


@pytest.mark.parametrize("mode,expected", [("auto", "streaming_queue"),
                                           ("deferred", "deferred_memory")])
def test_wo0016_legacy_stage_resolution_unchanged(mode, expected):
    args = options(authorize_stage=runner.WO16_STAGE)
    args.capture_write_mode = mode
    assert runner.resolve_capture_write_mode(args) == expected


def test_board_entry_without_stage_still_rejects_bounded(monkeypatch):
    args = runner._parser().parse_args(["--mode", "board", "--capture-write-mode", "bounded_stream"])
    def forbidden(*args, **kwargs):
        pytest.fail("permit gate reached before rejection")
    monkeypatch.setattr(runner, "validate_operator_gate_launcher_sha256", forbidden)
    with pytest.raises(ValueError, match="bounded_stream.*not authorized"):
        runner.execute_board_stream(args)


@pytest.mark.parametrize("field,value", [("wo0017_profile", "b1"),
                                       ("wo0017_profile", "b2"),
                                       ("diagnostic_probe", True),
                                       ("physical_calibration", True)])
def test_board_entry_rejects_contract_conflict_before_gate_or_hardware(monkeypatch, field, value):
    args = runner._parser().parse_args(["--mode", "board", "--capture-write-mode", "bounded_stream"])
    setattr(args, field, value)

    def forbidden(*args, **kwargs):
        pytest.fail("board/permit gate reached before bounded mode contract rejection")

    monkeypatch.setattr(runner, "validate_operator_gate_launcher_sha256", forbidden)
    monkeypatch.setattr(runner, "require_board_opt_in", forbidden)
    with pytest.raises(ValueError, match="bounded_stream"):
        runner.execute_board_stream(args)
