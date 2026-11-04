import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit.py"


class ArtifactAuditCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pynq-audit-")
        self.base = Path(self.temp.name)
        self.root = self.base / "bundle"
        self.root.mkdir()
        self.manifest_path = self.base / "manifest.json"
        self.payloads = {
            "design.bit": b"SYNTHETIC TEST FIXTURE: not a bitstream\n",
            "design.hwh": b"SYNTHETIC TEST FIXTURE: not hardware metadata\n",
            "source.v": b"module synthetic_fixture; endmodule\n",
            "config.json": b'{"fixture": true}\n',
        }
        for name, payload in self.payloads.items():
            (self.root / name).write_bytes(payload)
        self.files = [
            {"role": role, "path": name, "sha256": hashlib.sha256(self.payloads[name]).hexdigest()}
            for role, name in (("bit", "design.bit"), ("hwh", "design.hwh"),
                               ("source", "source.v"), ("config", "config.json"))
        ]
        self.write_manifest({"schema": 1, "files": self.files})

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self, document):
        self.manifest_bytes = json.dumps(document, sort_keys=True).encode("utf-8") + b"\n"
        self.manifest_path.write_bytes(self.manifest_bytes)

    def manifest_sha256(self):
        return hashlib.sha256(self.manifest_bytes).hexdigest()

    def run_audit(self, expected_sha=None, *, root=None, manifest=None):
        command = [sys.executable, str(SCRIPT), "--manifest", str(manifest or self.manifest_path),
                   "--manifest-sha256", expected_sha or self.manifest_sha256()]
        if root is not None:
            command.extend(["--root", str(root)])
        process = subprocess.run(command, text=True, capture_output=True, check=False)
        try:
            output = json.loads(process.stdout)
        except json.JSONDecodeError as error:
            self.fail(f"CLI did not emit JSON on stdout (exit {process.returncode}): {process.stdout!r}; {error}")
        return process, output

    def assert_failure(self, *, expected_sha=None, root=None):
        process, output = self.run_audit(expected_sha, root=root)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(output["status"], "ARTIFACT_HASH_FAIL")
        return output

    def test_valid_synthetic_bundle_passes_and_reports_each_file(self):
        process, output = self.run_audit(root=self.root)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(output["status"], "ARTIFACT_HASH_PASS")
        self.assertEqual(len(output["files"]), 4)

    def test_changed_file_fails_its_declared_hash(self):
        (self.root / "design.bit").write_bytes(b"changed synthetic fixture\n")
        output = self.assert_failure(root=self.root)
        self.assertIn("design.bit", json.dumps(output))

    def test_missing_file_fails(self):
        (self.root / "source.v").unlink()
        self.assert_failure(root=self.root)

    def test_manifest_digest_mismatch_fails_before_trusting_entries(self):
        expected = self.manifest_sha256()
        self.manifest_path.write_bytes(self.manifest_bytes + b" ")
        self.assert_failure(expected_sha=expected, root=self.root)

    def test_parent_path_escape_fails(self):
        outside = self.base / "outside.bin"
        outside.write_bytes(b"outside\n")
        escaped = dict(self.files[2], path="../outside.bin")
        self.write_manifest({"schema": 1, "files": [self.files[0], self.files[1], escaped]})
        self.assert_failure(root=self.root)

    def test_absolute_path_fails(self):
        outside = self.base / "outside.bin"
        outside.write_bytes(b"outside\n")
        escaped = dict(self.files[2], path=str(outside.resolve()))
        self.write_manifest({"schema": 1, "files": [self.files[0], self.files[1], escaped]})
        self.assert_failure(root=self.root)

    def test_duplicate_paths_fail(self):
        duplicate = dict(self.files[2], path="design.bit")
        self.write_manifest({"schema": 1, "files": [self.files[0], self.files[1], duplicate]})
        self.assert_failure(root=self.root)

    def test_manifest_without_hwh_fails(self):
        self.write_manifest({"schema": 1, "files": [self.files[0], self.files[2]]})
        self.assert_failure(root=self.root)

    def test_unsupported_schema_fails(self):
        self.write_manifest({"schema": 2, "files": self.files})
        self.assert_failure(root=self.root)

    def test_invalid_sha256_syntax_fails(self):
        invalid = [dict(item) for item in self.files]
        invalid[0]["sha256"] = "not-a-sha256"
        self.write_manifest({"schema": 1, "files": invalid})
        self.assert_failure(root=self.root)

    def test_symlink_resolving_outside_root_fails(self):
        outside_dir = self.base / "outside-link-target"
        outside_dir.mkdir()
        outside = outside_dir / "outside.bin"
        outside.write_bytes(b"outside\n")
        link = self.root / "outside-link"
        try:
            os.symlink(outside_dir, link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            if os.name != "nt":
                self.skipTest(f"symlink creation unavailable: {error}")
            junction = subprocess.run(
                ["cmd.exe", "/c", "mklink", "/J", str(link), str(outside_dir)],
                text=True, capture_output=True, check=False,
            )
            if junction.returncode != 0:
                self.skipTest(f"symlink/junction creation unavailable: {junction.stderr or junction.stdout}")
        escaped = {"role": "source", "path": "outside-link/outside.bin",
                   "sha256": hashlib.sha256(outside.read_bytes()).hexdigest()}
        self.write_manifest({"schema": 1, "files": [self.files[0], self.files[1], escaped]})
        self.assert_failure(root=self.root)

    def test_root_defaults_to_manifest_directory(self):
        local_manifest = self.root / "manifest.json"
        local_bytes = json.dumps({"schema": 1, "files": self.files}, sort_keys=True).encode() + b"\n"
        local_manifest.write_bytes(local_bytes)
        process, output = self.run_audit(hashlib.sha256(local_bytes).hexdigest(), manifest=local_manifest)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(output["status"], "ARTIFACT_HASH_PASS")


if __name__ == "__main__":
    unittest.main()
