"""Tests for source-pinned overlay RTL replacement in the joint staging flow."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("joint_prepare.py")
SPEC = importlib.util.spec_from_file_location("joint_prepare_overlay_tests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
joint_prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(joint_prepare)

PROJECT_TARGET_REL = Path("data/results/standalone-20261101-055148-g2-integration/rtl/video_binary_overlay.v")
PINNED_REFERENCE_REL = Path("fpga/experimental/standalone/video_binary_overlay.v")
TARGET_REL = PROJECT_TARGET_REL


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class JointPrepareOverlayTests(unittest.TestCase):
    def test_overlay_override_is_an_optional_cli_argument(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--overlay-rtl", result.stdout)

    def test_candidate_targets_the_fixed_xpr_source_and_uses_standalone_pin_as_reference(self):
        resolver = getattr(joint_prepare, "resolve_overlay_override", None)
        self.assertTrue(callable(resolver), "joint_prepare must expose override validation")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            pinned_path = baseline / PINNED_REFERENCE_REL
            project_path = baseline / PROJECT_TARGET_REL
            pinned_path.parent.mkdir(parents=True)
            project_path.parent.mkdir(parents=True)
            original = "module video_binary_overlay; endmodule\n"
            pinned_path.write_text(original, encoding="utf-8")
            project_path.write_text(original, encoding="utf-8")
            candidate = root / "candidate.v"
            candidate.write_text("module video_binary_overlay; wire fixed; endmodule\n", encoding="utf-8")

            try:
                binding = resolver(candidate, baseline,
                                   [joint_prepare.entry(pinned_path)],
                                   [joint_prepare.entry(project_path)])
            except ValueError as error:
                self.fail(f"fixed XPR source should bind to the standalone pinned copy: {error}")

            self.assertEqual(binding["source"]["path"], project_path.resolve().as_posix())
            self.assertEqual(binding["pinned_reference"]["path"], pinned_path.resolve().as_posix())
            self.assertEqual(binding["target_relative_path"], TARGET_REL.as_posix())

    def test_legacy_manifest_without_overlay_field_still_verifies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            immutable = root / "input.v"
            snapshot = root / "snapshot.v"
            immutable.write_text("module input; endmodule\n", encoding="utf-8")
            snapshot.write_text("module snapshot; endmodule\n", encoding="utf-8")
            manifest = {
                "immutable_inputs": [{"path": immutable.as_posix(), "sha256": digest(immutable)}],
                "snapshot_sources": [{"path": snapshot.as_posix(), "sha256": digest(snapshot)}],
            }
            manifest_path = root / "joint_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                joint_prepare.verify_manifest(manifest_path)

            self.assertIn("JOINT_SOURCE_HASHES_OK", output.getvalue())

    def test_candidate_is_bound_to_the_unique_pinned_project_source(self):
        resolver = getattr(joint_prepare, "resolve_overlay_override", None)
        self.assertTrue(callable(resolver), "joint_prepare must expose override validation")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            pinned = baseline / PINNED_REFERENCE_REL
            source = baseline / TARGET_REL
            pinned.parent.mkdir(parents=True)
            source.parent.mkdir(parents=True)
            original = "module video_binary_overlay; endmodule\n"
            pinned.write_text(original, encoding="utf-8")
            source.write_text(original, encoding="utf-8")
            candidate = root / "candidate.v"
            candidate.write_text("module video_binary_overlay; wire fixed; endmodule\n", encoding="utf-8")
            source_entry = joint_prepare.entry(source)
            pinned_entry = joint_prepare.entry(pinned)

            binding = resolver(candidate, baseline, [pinned_entry], [source_entry])

            self.assertEqual(binding["source"]["sha256"], digest(source))
            self.assertEqual(binding["pinned_reference"]["sha256"], digest(pinned))
            self.assertEqual(binding["candidate"]["sha256"], digest(candidate))
            self.assertEqual(binding["target_relative_path"], TARGET_REL.as_posix())

    def test_candidate_module_must_match_the_fixed_overlay_name(self):
        resolver = getattr(joint_prepare, "resolve_overlay_override", None)
        self.assertTrue(callable(resolver), "joint_prepare must expose override validation")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            pinned = baseline / PINNED_REFERENCE_REL
            source = baseline / TARGET_REL
            pinned.parent.mkdir(parents=True)
            source.parent.mkdir(parents=True)
            original = "module video_binary_overlay; endmodule\n"
            pinned.write_text(original, encoding="utf-8")
            source.write_text(original, encoding="utf-8")
            candidate = root / "candidate.v"
            candidate.write_text("module unrelated; endmodule\n", encoding="utf-8")
            source_entry = joint_prepare.entry(source)
            pinned_entry = joint_prepare.entry(pinned)

            with self.assertRaisesRegex(ValueError, "module video_binary_overlay"):
                resolver(candidate, baseline, [pinned_entry], [source_entry])

    def test_candidate_must_replace_a_present_pinned_project_source(self):
        resolver = getattr(joint_prepare, "resolve_overlay_override", None)
        self.assertTrue(callable(resolver), "joint_prepare must expose override validation")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            baseline.mkdir()
            candidate = root / "candidate.v"
            candidate.write_text("module video_binary_overlay; endmodule\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "uniquely pinned"):
                resolver(candidate, baseline, [], [])

    def test_manifest_rejects_candidate_and_staged_source_hash_disagreement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            pinned_path = baseline / PINNED_REFERENCE_REL
            target = baseline / TARGET_REL
            pinned_path.parent.mkdir(parents=True)
            target.parent.mkdir(parents=True)
            original = "module video_binary_overlay; endmodule\n"
            pinned_path.write_text(original, encoding="utf-8")
            target.write_text(original, encoding="utf-8")
            stage_target = root / "snapshot" / TARGET_REL
            stage_target.parent.mkdir(parents=True)
            stage_target.write_text("module video_binary_overlay; endmodule\n", encoding="utf-8")
            candidate = root / "candidate.v"
            candidate.write_text("module video_binary_overlay; wire changed; endmodule\n", encoding="utf-8")
            manifest = {
                "preflight": {
                    "snapshot": baseline.as_posix(),
                    "baseline_pinned_sources": [joint_prepare.entry(pinned_path)],
                    "project_sources": [joint_prepare.entry(target)],
                },
                "immutable_inputs": [joint_prepare.entry(candidate)],
                "snapshot_sources": [joint_prepare.entry(stage_target)],
                "required_live_sources": [stage_target.as_posix()],
                "overlay_replacements": [{
                    "source": joint_prepare.entry(target),
                    "pinned_reference": joint_prepare.entry(pinned_path),
                    "replacement": joint_prepare.entry(stage_target),
                    "candidate": joint_prepare.entry(candidate),
                    "target_relative_path": TARGET_REL.as_posix(),
                }],
            }
            manifest_path = root / "joint_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "overlay replacement hash"):
                joint_prepare.verify_manifest(manifest_path)

    def test_manifest_accepts_matching_immutable_candidate_and_staged_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            pinned_path = baseline / PINNED_REFERENCE_REL
            target = baseline / TARGET_REL
            pinned_path.parent.mkdir(parents=True)
            target.parent.mkdir(parents=True)
            original = "module video_binary_overlay; endmodule\n"
            pinned_path.write_text(original, encoding="utf-8")
            target.write_text(original, encoding="utf-8")
            stage_target = root / "snapshot" / TARGET_REL
            stage_target.parent.mkdir(parents=True)
            candidate = root / "candidate.v"
            text = "module video_binary_overlay; wire fixed; endmodule\n"
            candidate.write_text(text, encoding="utf-8")
            stage_target.write_text(text, encoding="utf-8")
            source_entry = joint_prepare.entry(target)
            candidate_entry = joint_prepare.entry(candidate)
            stage_entry = joint_prepare.entry(stage_target)
            manifest = {
                "preflight": {
                    "snapshot": baseline.as_posix(),
                    "baseline_pinned_sources": [joint_prepare.entry(pinned_path)],
                    "project_sources": [source_entry],
                },
                "immutable_inputs": [candidate_entry],
                "snapshot_sources": [stage_entry],
                "required_live_sources": [stage_target.as_posix()],
                "overlay_replacements": [{
                    "source": source_entry,
                    "pinned_reference": joint_prepare.entry(pinned_path),
                    "replacement": stage_entry,
                    "candidate": candidate_entry,
                    "target_relative_path": TARGET_REL.as_posix(),
                }],
            }
            manifest_path = root / "joint_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                joint_prepare.verify_manifest(manifest_path)

            self.assertIn("JOINT_SOURCE_HASHES_OK", output.getvalue())


if __name__ == "__main__":
    unittest.main()
