from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile


SCRIPT = Path(__file__).with_name("joint_prepare.py")
SPEC = importlib.util.spec_from_file_location("joint_prepare_frame_monitor_tests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
joint_prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(joint_prepare)

SOURCE_REL = Path("fpga/experimental/standalone/video_frame_monitor.v")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_binding(root: Path) -> tuple[dict, Path]:
    baseline = root / "baseline"
    source = baseline / SOURCE_REL
    source.parent.mkdir(parents=True)
    source.write_text("module video_frame_monitor; endmodule\n", encoding="utf-8")
    candidate = root / "candidate.v"
    candidate.write_text("module video_frame_monitor; wire staged; endmodule\n", encoding="utf-8")
    staged = root / "build" / "snapshot" / SOURCE_REL
    staged.parent.mkdir(parents=True)
    staged.write_bytes(candidate.read_bytes())

    source_entry = joint_prepare.entry(source)
    candidate_entry = joint_prepare.entry(candidate)
    staged_entry = joint_prepare.entry(staged)
    binding = joint_prepare.resolve_frame_monitor_override(
        candidate, baseline, [source_entry], [source_entry])
    assert binding is not None
    manifest = {
        "preflight": {
            "snapshot": baseline.as_posix(),
            "baseline_pinned_sources": [source_entry],
            "project_sources": [source_entry],
        },
        "immutable_inputs": [candidate_entry],
        "snapshot_sources": [staged_entry],
        "required_live_sources": [staged.as_posix()],
        "frame_monitor_replacements": [{
            "source": binding["source"],
            "pinned_reference": binding["pinned_reference"],
            "candidate": binding["candidate"],
            "replacement": staged_entry,
            "target_relative_path": binding["target_relative_path"],
        }],
    }
    manifest_path = root / "build" / "joint_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest, manifest_path


def test_frame_monitor_override_binds_candidate_to_pinned_project_source():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest, manifest_path = make_binding(root)

        joint_prepare.verify_manifest(manifest_path)

        replacement = manifest["frame_monitor_replacements"][0]
        assert replacement["target_relative_path"] == SOURCE_REL.as_posix()
        assert replacement["candidate"]["sha256"] == replacement["replacement"]["sha256"]


def test_frame_monitor_binding_rejects_staged_hash_drift():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest, manifest_path = make_binding(root)
        replacement = manifest["frame_monitor_replacements"][0]
        replacement["replacement"] = dict(replacement["replacement"])
        replacement["replacement"]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        try:
            joint_prepare.verify_manifest(manifest_path)
        except ValueError as exc:
            assert "hash" in str(exc)
        else:
            raise AssertionError("frame monitor staged source hash drift was accepted")
