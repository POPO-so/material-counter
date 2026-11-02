import json

from scripts.experiments import run_jpeg_counter
from scripts.rle import run_counter


def test_explicit_jpeg_pl_flag_dispatches_to_raw_jpeg_runner(monkeypatch):
    captured = {}

    def fake_main(argv):
        captured["argv"] = argv
        return 23

    monkeypatch.setattr(run_jpeg_counter, "main", fake_main)
    argv = [
        "--jpeg-pl", "--execute", "--bit", "joint.bit", "--bit-sha256", "abc",
        "--hwh", "joint.hwh", "--hwh-sha256", "def", "--build-status",
        "ROUTED_NOT_BOARD_VERIFIED", "--boot-id", "boot", "--output", "run.json",
    ]

    result = run_counter.main(argv)

    assert result == 23
    assert captured["argv"] == argv[1:]


def test_camera_hardware_entry_defaults_to_raw_jpeg_runner(monkeypatch):
    captured = {}

    def fake_main(argv):
        captured["argv"] = argv
        return 29

    monkeypatch.setattr(run_jpeg_counter, "main", fake_main)
    argv = [
        "--mode", "camera", "--execute", "--bit", "joint.bit",
        "--bit-sha256", "abc", "--hwh", "joint.hwh", "--hwh-sha256", "def",
        "--build-status", "ROUTED_NOT_BOARD_VERIFIED", "--boot-id", "boot",
        "--output", "run.json",
    ]

    result = run_counter.main(argv)

    assert result == 29
    assert captured["argv"] == [
        "--execute", "--bit", "joint.bit", "--bit-sha256", "abc",
        "--hwh", "joint.hwh", "--hwh-sha256", "def", "--build-status",
        "ROUTED_NOT_BOARD_VERIFIED", "--boot-id", "boot", "--output", "run.json",
    ]


def test_software_decode_flag_keeps_camera_request_on_legacy_path(monkeypatch, tmp_path):
    called = False

    def forbidden_jpeg_runner(_argv):
        nonlocal called
        called = True
        raise AssertionError("explicit software decode must not dispatch to JPEG PL")

    monkeypatch.setattr(run_jpeg_counter, "main", forbidden_jpeg_runner)
    output = tmp_path / "software-fallback.json"

    result = run_counter.main([
        "--software-decode", "--mode", "camera", "--output", str(output),
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert result == 1
    assert not called
    assert report["status"] == "FAILED"
    assert "hardware requires execute" in report["error"]


def test_hardware_self_test_execute_does_not_dispatch_to_jpeg_camera_runner(monkeypatch, tmp_path):
    called = False

    def forbidden_jpeg_runner(_argv):
        nonlocal called
        called = True
        raise AssertionError("RLE self-test must retain its runner")

    monkeypatch.setattr(run_jpeg_counter, "main", forbidden_jpeg_runner)
    output = tmp_path / "self-test.json"

    result = run_counter.main([
        "--mode", "self-test", "--execute", "--output", str(output),
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert result == 1
    assert not called
    assert "hardware requires execute, boot-id, manifest" in report["error"]
