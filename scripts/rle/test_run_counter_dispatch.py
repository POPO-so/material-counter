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
