"""Package provenance, no-clobber behavior and the missing splitter binding."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


def module():
    import prepare_split_board_validation as prep
    return prep


def test_binding_patch_covers_splitter_and_is_idempotent():
    source = (b'def formal_runtime_sha256():\n'
              b'    names = ("run_stream_counter.py", "operator_gate_launcher.py", "stream_counter.py",\n'
              b'             "capture_dataset.py")\n    return names\n')
    patched = module().bind_splitter(source)
    scope = {}
    exec(patched, scope)
    assert "split_touching.py" in scope["formal_runtime_sha256"]()
    assert module().bind_splitter(patched) == patched


def test_binding_patch_rejects_unreviewed_source_layout():
    with pytest.raises(ValueError, match="reviewed"):
        module().bind_splitter(b'def formal_runtime_sha256():\n    return {}\n')


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = Path(__file__).resolve().parents[2]
    out = tmp_path_factory.mktemp("split-board-build") / "artifact"
    receipt = module().build(root, "59eea586", "ee55267e", out)
    return out, receipt


def test_bundle_preserves_main_algorithm_and_records_missing_monitor(built):
    out, receipt = built
    manifest = json.loads((out / "payload/PACKAGE.json").read_text())
    assert manifest["upstream_main_commit"].startswith("59eea586")
    assert manifest["hardware_status"] == "NOT_VERIFIED"
    assert manifest["board_contacted"] is False
    assert manifest["old_deployment_commit"].startswith("ee55267e")
    assert not (out / "payload/scripts/wo0016/run_live_monitor.py").exists()
    assert manifest["main_live_monitor_present"] is False
    changes = [path for path, row in manifest["source_files"].items()
               if row["upstream_sha256"] != row["sha256"]]
    assert changes == ["scripts/wo0016/run_stream_counter.py"]
    assert hashlib.sha256((out / "deployment.zip").read_bytes()).hexdigest() == receipt["archive_sha256"]
    plan = json.loads((out / "payload/round-plan.template.json").read_text())
    assert plan["hardware_authorized"] is False
    assert len(plan["rounds"]) == 9
    assert all(row["actual_count"] is None and row["status"] == "NOT_VERIFIED"
               and row["independent_truth"]["window_count_confirmed"] is False for row in plan["rounds"])


def test_fresh_bundle_verifies_and_tampered_splitter_is_rejected(built, tmp_path):
    import shutil
    out, receipt = built
    payload = tmp_path / "payload"
    shutil.copytree(out / "payload", payload)
    command = [sys.executable, str(payload / "tools/verify_split_package.py"),
               "--root", str(payload), "--manifest-sha256", receipt["manifest_sha256"]]
    good = subprocess.run(command, capture_output=True, text=True)
    assert good.returncode == 0, good.stderr
    splitter = payload / "scripts/wo0016/split_touching.py"
    splitter.write_bytes(splitter.read_bytes() + b"\n# changed\n")
    bad = subprocess.run(command, capture_output=True, text=True)
    assert bad.returncode != 0
    assert "split_touching.py" in bad.stderr


def test_existing_artifact_is_never_overwritten(built):
    out, _ = built
    before = (out / "deployment.zip").read_bytes()
    with pytest.raises(FileExistsError):
        module().build(Path(__file__).resolve().parents[2], "59eea586", "ee55267e", out)
    assert (out / "deployment.zip").read_bytes() == before


def test_staging_defaults_to_no_connection_and_rejects_old_deployment(built, monkeypatch):
    import stage_split_validation as staging
    out, receipt = built

    def unexpected(*args, **kwargs):
        pytest.fail("dry-run must not execute SSH")

    monkeypatch.setattr(staging.subprocess, "run", unexpected)
    args = (out / "deployment.zip", receipt["archive_sha256"], receipt["manifest_sha256"],
            "xilinx@board.example", "/home/xilinx/wo0017/split-validation-test", "/usr/bin/python3")
    assert staging.stage(*args)["network_actions"] == 0
    with pytest.raises(ValueError, match="new split-validation"):
        staging.stage(*args[:4], "/home/xilinx/wo0017/live-monitor-20261024-ee55267e", args[5], execute=True)
    with pytest.raises(ValueError, match="local archive SHA-256"):
        staging.stage(args[0], "0" * 64, *args[2:], execute=True)
    with pytest.raises(ValueError, match="local PACKAGE.json SHA-256"):
        staging.stage(args[0], args[1], "0" * 64, *args[3:], execute=True)
    with pytest.raises(ValueError, match="Python"):
        staging.stage(*args[:5], "/bin/sh", execute=True)


def test_remote_installer_rejects_existing_directory_without_touching_files(tmp_path, monkeypatch):
    import io
    from types import SimpleNamespace
    import stage_split_validation as staging
    destination = tmp_path / "split-validation-existing"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("original")
    program = staging.REMOTE.replace("'/home/xilinx/wo0017'", repr(str(tmp_path)))
    monkeypatch.setattr(sys, "argv", ["installer", str(destination), "0" * 64, "0" * 64])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"")))
    with pytest.raises(FileExistsError):
        exec(compile(program, "remote-upload-only", "exec"), {})
    assert marker.read_text() == "original"
    assert not (destination / "deployment.zip").exists()


@pytest.mark.parametrize("member", ["../keep.txt", "/tmp/escape.txt"])
def test_remote_installer_rejects_unsafe_zip_names(tmp_path, monkeypatch, member):
    import io
    from types import SimpleNamespace
    import zipfile
    import stage_split_validation as staging
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as stored:
        stored.writestr(member, "bad")
    raw = archive.getvalue()
    destination = tmp_path / "split-validation-new"
    program = staging.REMOTE.replace("'/home/xilinx/wo0017'", repr(str(tmp_path)))
    monkeypatch.setattr(sys, "argv", ["installer", str(destination), hashlib.sha256(raw).hexdigest(), "0" * 64])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    with pytest.raises(ValueError, match="unsafe"):
        exec(compile(program, "remote-upload-only", "exec"), {})
    assert not (tmp_path / "keep.txt").exists()


def test_replay_rejects_manifest_without_independent_hash_binding(built, tmp_path):
    import replay_split_board_candidate as replay
    out, _ = built
    captures = tmp_path / "captures"
    captures.mkdir()
    with pytest.raises(ValueError, match="independently pinned"):
        replay.replay(out / "payload", captures, tmp_path / "result", "0" * 64)


def test_camera_replay_uses_main_optical_strict_greater_than_128():
    import numpy as np
    import replay_split_board_candidate as replay
    gray = np.array([[0, 127, 128, 129, 255]], dtype=np.uint8)
    np.testing.assert_array_equal(replay.optical_mask(gray),
                                  np.array([[0, 0, 0, 255, 255]], dtype=np.uint8))
