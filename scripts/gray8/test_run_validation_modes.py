import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_gray8_counter


def _offline_arguments(tmp_path, *extra):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    output = tmp_path / "result.json"
    return ["--manifest", str(manifest), "--manifest-sha256", "unused",
            "--mode", "offline", "--output", str(output), *extra], output


def test_offline_runtime_mode_is_explicit_and_does_not_claim_pixel_verification(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run_gray8_counter, "load_manifest",
                        lambda path, digest: {"protocol": "GRAY8_PACKET_V1"})
    arguments, output = _offline_arguments(tmp_path, "--validation", "runtime")

    assert run_gray8_counter.main(arguments) == 0

    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["validation"] == "runtime"
    assert result["verify_pixels"] is False
    assert result["pixel_verified_frames"] == 0
    assert "no frames were transferred" in result["timing_scope"]


def test_offline_validation_defaults_to_full(tmp_path, monkeypatch):
    monkeypatch.setattr(run_gray8_counter, "load_manifest",
                        lambda path, digest: {"protocol": "GRAY8_PACKET_V1"})
    arguments, output = _offline_arguments(tmp_path)

    assert run_gray8_counter.main(arguments) == 0

    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["validation"] == "full"
    assert result["verify_pixels"] is True


def test_self_test_rejects_runtime_validation(tmp_path):
    manifest = tmp_path / "manifest.json"
    arguments = ["--manifest", str(manifest), "--manifest-sha256", "unused",
                 "--mode", "self-test", "--validation", "runtime",
                 "--output", str(tmp_path / "result.json")]

    with pytest.raises(SystemExit) as exc:
        run_gray8_counter.main(arguments)

    assert exc.value.code == 2
