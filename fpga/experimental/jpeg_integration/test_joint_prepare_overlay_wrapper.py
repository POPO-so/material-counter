from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile


SCRIPT = Path(__file__).with_name("joint_prepare.py")
SPEC = importlib.util.spec_from_file_location("joint_prepare_overlay_wrapper_tests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
joint_prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(joint_prepare)

PROJECT_TARGET_REL = Path("data/results/standalone-20261101-055148-g2-integration/rtl/video_binary_overlay_bd.v")
PINNED_REFERENCE_REL = Path("fpga/experimental/standalone/video_binary_overlay_bd.v")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_wrapper_override_binds_only_the_fixed_project_and_pinned_reference():
    resolver = getattr(joint_prepare, "resolve_overlay_wrapper_override", None)
    assert callable(resolver), "joint_prepare must expose wrapper override validation"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        baseline = root / "baseline"
        pinned = baseline / PINNED_REFERENCE_REL
        project = baseline / PROJECT_TARGET_REL
        pinned.parent.mkdir(parents=True)
        project.parent.mkdir(parents=True)
        original = "module video_binary_overlay_bd; endmodule\n"
        pinned.write_text(original, encoding="utf-8")
        project.write_text(original, encoding="utf-8")
        candidate = root / "candidate.v"
        candidate.write_text("module video_binary_overlay_bd; wire total_count; endmodule\n", encoding="utf-8")

        binding = resolver(candidate, baseline, [joint_prepare.entry(pinned)], [joint_prepare.entry(project)])

        assert binding["source"]["sha256"] == sha(project)
        assert binding["pinned_reference"]["sha256"] == sha(pinned)
        assert binding["candidate"]["sha256"] == sha(candidate)
        assert binding["target_relative_path"] == PROJECT_TARGET_REL.as_posix()


def test_wrapper_binding_rejects_manifest_staging_hash_drift():
    verify = getattr(joint_prepare, "verify_overlay_wrapper_binding", None)
    assert callable(verify), "joint_prepare must verify staged wrapper identity"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        baseline = root / "baseline"
        pinned = baseline / PINNED_REFERENCE_REL
        project = baseline / PROJECT_TARGET_REL
        staged = root / "snapshot" / PROJECT_TARGET_REL
        for path in (pinned, project, staged):
            path.parent.mkdir(parents=True, exist_ok=True)
        original = "module video_binary_overlay_bd; endmodule\n"
        pinned.write_text(original, encoding="utf-8")
        project.write_text(original, encoding="utf-8")
        candidate = root / "candidate.v"
        candidate.write_text("module video_binary_overlay_bd; wire count_reg; endmodule\n", encoding="utf-8")
        staged.write_text("module video_binary_overlay_bd; wire wrong; endmodule\n", encoding="utf-8")
        source = joint_prepare.entry(project)
        reference = joint_prepare.entry(pinned)
        staged_entry = joint_prepare.entry(staged)
        candidate_entry = joint_prepare.entry(candidate)
        manifest = {
            "preflight": {
                "snapshot": baseline.as_posix(),
                "baseline_pinned_sources": [reference],
                "project_sources": [source],
            },
            "immutable_inputs": [candidate_entry],
            "snapshot_sources": [staged_entry],
            "required_live_sources": [staged.as_posix()],
            "overlay_wrapper_replacements": [{
                "source": source,
                "pinned_reference": reference,
                "replacement": staged_entry,
                "candidate": candidate_entry,
                "target_relative_path": PROJECT_TARGET_REL.as_posix(),
            }],
        }
        manifest_path = root / "joint_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        try:
            verify(manifest_path, manifest)
        except ValueError as exc:
            assert "wrapper replacement hash" in str(exc)
        else:
            raise AssertionError("wrapper staged source hash drift was accepted")
