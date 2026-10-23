"""Offline tests exercise the same injected capture core as the source template."""
import ast
import hashlib
import unittest
from pathlib import Path

import proposal_contract_v2 as fakes
import remote_template_v2 as template


ROOT = Path(__file__).resolve().parents[1]
PROPOSALS = Path(__file__).resolve().parent
FROZEN_B1_WRAPPER = """
# Frozen review wrapper. The approval marker intentionally blocks execution.
def _not_approved_b1_main():
    if EXECUTION_STATUS != "APPROVED":
        raise SystemExit(2)
    import base64 as _base64
    import json as _json
    import os as _remote_os
    if _remote_os.geteuid() == 0:
        raise SystemExit(4)
    result = capture_b1(os=_remote_os)
    if result.status != "OFFLINE_MODEL_PASS":
        raise SystemExit(3)
    payload = {
        "status": result.status,
        "samples": [[path, _base64.b64encode(raw).decode("ascii")] for path, raw in result.samples],
    }
    print(_json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))

if __name__ == "__main__":
    _not_approved_b1_main()
"""
FROZEN_B2_WRAPPER = """
# Frozen review wrapper. The approval marker intentionally blocks execution.
def _not_approved_b2_main():
    if EXECUTION_STATUS != "APPROVED":
        raise SystemExit(2)
    import base64 as _base64
    import hashlib as _hashlib
    import json as _json
    import os as _remote_os
    if _remote_os.geteuid() == 0:
        raise SystemExit(4)
    result = capture_b2(os=_remote_os)
    if result.status != "OFFLINE_MODEL_PASS":
        raise SystemExit(3)
    payload = {
        "status": result.status,
        "size": len(result.data),
        "sha256": _hashlib.sha256(result.data).hexdigest(),
        "data_b64": _base64.b64encode(result.data).decode("ascii"),
        "boot_dev_differs_from_root": result.boot_dev_differs_from_root,
    }
    print(_json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))

if __name__ == "__main__":
    _not_approved_b2_main()
"""


def _b1(os_api):
    return template.capture_b1(os=os_api)


def _b2(os_api):
    return template.capture_b2(os=os_api)


class B1TemplateTests(unittest.TestCase):
    def test_boot_mismatch_reads_only_boot_id(self):
        os_api = fakes.OfflineOSMock(boot_ids=[b"different-boot\n"])

        result = _b1(os_api)

        self.assertEqual(result.status, "BOOT_MISMATCH")
        self.assertEqual(os_api.proc_open_paths, [template.BOOT_ID])
        self.assertFalse(result.afi_authorized)

    def test_matching_boot_reads_other_five_in_fixed_order_then_rechecks(self):
        os_api = fakes.OfflineOSMock()

        result = _b1(os_api)

        self.assertEqual(result.status, "OFFLINE_MODEL_PASS")
        self.assertEqual(os_api.proc_open_paths, [template.BOOT_ID, *template.B1_OTHER_PATHS, template.BOOT_ID])
        self.assertEqual(len(result.samples), 7)
        self.assertFalse(result.afi_authorized)
        proc_opens = [event for event in os_api.events if event[0] == "open" and event[1] in template.B1_PATHS]
        self.assertTrue(all(event[2] == os_api.O_RDONLY | os_api.O_NOFOLLOW for event in proc_opens))
        self.assertEqual(os_api.events[0], ("geteuid", 1000))

    def test_root_euid_rejects_before_opening_boot_id(self):
        os_api = fakes.OfflineOSMock(euid=0)

        result = _b1(os_api)

        self.assertEqual(result.status, "ROOT_REFUSED")
        self.assertEqual(os_api.events, [("geteuid", 0)])

    def test_single_proc_file_over_limit_fails_and_closes_without_later_reads(self):
        os_api = fakes.OfflineOSMock(
            proc_payloads={template.BOOT_ID: b"x" * (template.MAX_PROC_READ_BYTES + 1)}
        )

        result = _b1(os_api)

        self.assertNotEqual(result.status, "OFFLINE_MODEL_PASS")
        self.assertEqual(os_api.proc_open_paths, [template.BOOT_ID])
        self.assertEqual(os_api.proc_read_count, 2)
        self.assertEqual(len([event for event in os_api.events if event[0] == "close"]), 1)

    def test_eacces_stops_without_retry_or_later_proc_reads(self):
        for fail_path, expected in (
            (template.BOOT_ID, [template.BOOT_ID]),
            (template.B1_OTHER_PATHS[0], [template.BOOT_ID, template.B1_OTHER_PATHS[0]]),
        ):
            with self.subTest(fail_path=fail_path):
                os_api = fakes.OfflineOSMock(fail_proc_path=fail_path)
                result = _b1(os_api)
                self.assertEqual(result.status, "EACCES")
                self.assertEqual(os_api.proc_open_paths, expected)

    def test_boot_change_after_metadata_stops(self):
        os_api = fakes.OfflineOSMock(
            boot_ids=[template.EXPECTED_BOOT_ID_BYTES, b"different-boot\n"]
        )

        result = _b1(os_api)

        self.assertEqual(result.status, "BOOT_CHANGED")
        self.assertEqual(os_api.proc_open_paths[-1], template.BOOT_ID)
        self.assertFalse(result.afi_authorized)

    def test_b1_helper_rejects_unknown_path_before_open(self):
        os_api = fakes.OfflineOSMock()

        with self.assertRaises(ValueError):
            template.read_b1_file("/dev/mem", os=os_api)
        self.assertEqual(os_api.events, [])

    def test_b1_requires_explicit_os_adapter(self):
        with self.assertRaises(TypeError):
            template.capture_b1()


class B2TemplateTests(unittest.TestCase):
    def test_success_uses_openat_and_fstatvfs_before_and_after_content(self):
        os_api = fakes.OfflineOSMock()

        result = _b2(os_api)

        self.assertEqual(result.status, "OFFLINE_MODEL_PASS")
        self.assertEqual(result.data, os_api.payload)
        self.assertFalse(result.boot_dev_differs_from_root)
        self.assertEqual(os_api.proc_open_paths, [template.BOOT_ID, template.BOOT_ID])
        opens = [event for event in os_api.events if event[0] == "open" and event[1] in ("/boot", "BOOT.BIN")]
        self.assertEqual(opens, [
            ("open", "/boot", os_api.O_RDONLY | os_api.O_DIRECTORY | os_api.O_NOFOLLOW, None),
            ("open", "BOOT.BIN", os_api.O_RDONLY | os_api.O_NOFOLLOW | os_api.O_NONBLOCK, "dirfd"),
        ])
        vfs_indexes = [index for index, event in enumerate(os_api.events) if event[0] == "fstatvfs"]
        stat_indexes = [index for index, event in enumerate(os_api.events) if event[0] == "fstat" and event[1] == "bootfd"]
        read_index = next(index for index, event in enumerate(os_api.events) if event[0] == "read" and event[1] == "bootfd")
        self.assertLess(vfs_indexes[0], stat_indexes[0])
        self.assertLess(stat_indexes[0], read_index)
        self.assertLess(read_index, vfs_indexes[1])
        self.assertLess(vfs_indexes[1], stat_indexes[1])
        self.assertLess(next(i for i, event in enumerate(os_api.events) if event[0] == "fstat" and event[1] == "dirfd"), read_index)
        self.assertLess(next(i for i, event in enumerate(os_api.events) if event[0] == "stat" and event[1] == "/"), read_index)
        self.assertFalse(result.afi_authorized)

    def test_nonregular_objects_open_nonblocking_then_reject_before_content_read(self):
        for file_type in ("fifo", "char", "block", "directory", "socket"):
            with self.subTest(file_type=file_type):
                os_api = fakes.OfflineOSMock(file_type=file_type)

                result = _b2(os_api)

                self.assertEqual(result.status, "OBJECT_TYPE")
                file_open = next(event for event in os_api.events if event[0] == "open" and event[1] == "BOOT.BIN")
                self.assertEqual(file_open[2], os_api.O_RDONLY | os_api.O_NOFOLLOW | os_api.O_NONBLOCK)
                self.assertEqual(os_api.boot_read_count, 0)
                self.assertIn(("close", "bootfd"), os_api.events)
                self.assertIn(("close", "dirfd"), os_api.events)

    def test_empty_or_oversized_boot_bin_rejects_before_content_read(self):
        for size in (0, template.MAX_BOOT_BIN_BYTES + 1):
            with self.subTest(size=size):
                os_api = fakes.OfflineOSMock(file_size=size)

                result = _b2(os_api)

                self.assertEqual(result.status, "SIZE_LIMIT")
                self.assertEqual(os_api.boot_read_count, 0)
                self.assertIn(("close", "bootfd"), os_api.events)
                self.assertIn(("close", "dirfd"), os_api.events)

    def test_cross_device_boot_file_rejects_before_content_read(self):
        os_api = fakes.OfflineOSMock(directory_dev=8, file_dev=9)

        result = _b2(os_api)

        self.assertEqual(result.status, "DEVICE_MISMATCH")
        self.assertEqual(os_api.boot_read_count, 0)
        self.assertIn(("close", "bootfd"), os_api.events)
        self.assertIn(("close", "dirfd"), os_api.events)

    def test_root_device_metadata_distinguishes_same_and_separate_boot_mount(self):
        for root_dev, expected in ((1, False), (9, True)):
            with self.subTest(root_dev=root_dev):
                result = _b2(fakes.OfflineOSMock(directory_dev=1, file_dev=1, root_dev=root_dev))

                self.assertEqual(result.status, "OFFLINE_MODEL_PASS")
                self.assertIs(result.boot_dev_differs_from_root, expected)

    def test_directory_symlink_stops_before_file_open_or_content_read(self):
        os_api = fakes.OfflineOSMock(directory_type="symlink")

        result = _b2(os_api)

        self.assertEqual(result.status, "SYMLINK")
        self.assertFalse(any(event[1] == "BOOT.BIN" for event in os_api.events if event[0] == "open"))
        self.assertEqual(os_api.boot_read_count, 0)

    def test_file_symlink_stops_before_content_read(self):
        os_api = fakes.OfflineOSMock(file_type="symlink")

        result = _b2(os_api)

        self.assertEqual(result.status, "SYMLINK")
        self.assertEqual(os_api.boot_read_count, 0)
        self.assertIn(("close", "dirfd"), os_api.events)

    def test_writable_filesystem_stops_before_content_read(self):
        os_api = fakes.OfflineOSMock(readonly=(False, True))

        result = _b2(os_api)

        self.assertEqual(result.status, "FILESYSTEM_WRITABLE")
        self.assertEqual(os_api.boot_read_count, 0)
        self.assertEqual(len([event for event in os_api.events if event[0] == "fstatvfs"]), 1)

    def test_filesystem_becoming_writable_after_read_stops(self):
        os_api = fakes.OfflineOSMock(readonly=(True, False))

        result = _b2(os_api)

        self.assertEqual(result.status, "FILESYSTEM_WRITABLE")
        self.assertGreater(os_api.boot_read_count, 0)

    def test_short_read_even_with_unchanged_stat_tuple_stops_and_preserves_bytes(self):
        payload = b"short fixture"
        fields = dict(zip(template.STAT_FIELDS, (1, 2, len(payload) + 1, 4)))
        os_api = fakes.OfflineOSMock(payload=payload, stat_before=fields, stat_after=fields)

        result = _b2(os_api)

        self.assertEqual(result.status, "CONTENT_SIZE_MISMATCH")
        self.assertEqual(result.data, payload)
        self.assertEqual(os_api.boot_read_count, 2)  # one data read plus EOF, with no retry

    def test_each_identity_field_change_stops(self):
        for field in template.STAT_FIELDS:
            with self.subTest(field=field):
                before = dict(zip(template.STAT_FIELDS, (1, 2, len(b"synthetic BOOT.BIN fixture"), 4)))
                after = dict(before)
                after[field] += 1
                os_api = fakes.OfflineOSMock(stat_before=before, stat_after=after)
                result = _b2(os_api)
                self.assertEqual(result.status, "STAT_CHANGED")

    def test_eacces_at_directory_open_file_open_or_content_read_stops_without_retry(self):
        for point, expected_open_count in (("open_dir", 1), ("openat", 2), ("read", 2)):
            with self.subTest(point=point):
                os_api = fakes.OfflineOSMock(fail_at=point)
                result = _b2(os_api)
                self.assertEqual(result.status, "EACCES")
                self.assertEqual(len([event for event in os_api.events if event[0] == "open" and event[1] in ("/boot", "BOOT.BIN")]), expected_open_count)

    def test_boot_mismatch_stops_before_boot_directory_open(self):
        os_api = fakes.OfflineOSMock(boot_ids=[b"different-boot\n"])

        result = _b2(os_api)

        self.assertEqual(result.status, "BOOT_MISMATCH")
        self.assertFalse(any(event[1] == "/boot" for event in os_api.events if event[0] == "open"))

    def test_boot_change_after_file_read_stops(self):
        os_api = fakes.OfflineOSMock(
            boot_ids=[template.EXPECTED_BOOT_ID_BYTES, b"new-boot\n"]
        )

        result = _b2(os_api)

        self.assertEqual(result.status, "BOOT_CHANGED")
        self.assertGreater(os_api.boot_read_count, 0)

    def test_b2_requires_explicit_os_adapter(self):
        with self.assertRaises(TypeError):
            template.capture_b2()


class StaticGuardTests(unittest.TestCase):
    def test_template_contains_syscall_core_but_no_executor_or_entry_point(self):
        source = (PROPOSALS / "remote_template_v2.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_imports = {"subprocess", "socket", "paramiko", "fabric", "pexpect", "pathlib"}
        os_api_calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertFalse({alias.name.split(".")[0] for alias in node.names} & forbidden_imports)
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0], forbidden_imports)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    os_api_calls.add(node.func.attr)
                if isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"open", "exec", "eval", "compile", "__import__"})
        self.assertTrue({"open", "fstatvfs", "fstat", "fstat", "stat", "read", "close", "geteuid"} <= os_api_calls)
        self.assertNotIn("__main__", source)
        self.assertNotIn("subprocess", source)
        self.assertIn('os.open("/boot", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)', source)
        self.assertIn('os.open("BOOT.BIN", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)', source)
        self.assertIn("MAX_BOOT_BIN_BYTES = 32 * 1024 * 1024", source)
        self.assertIn("MAX_PROC_READ_BYTES = 65536", source)
        self.assertIn('os.stat("/", follow_symlinks=False)', source)
        self.assertIn("dir_st.st_dev != st_before.st_dev", source)
        self.assertIn("bool(before_vfs.f_flag & os.ST_RDONLY)", source)
        self.assertIn("bool(after_vfs.f_flag & os.ST_RDONLY)", source)
        self.assertEqual(template.STAT_FIELDS, ("st_dev", "st_ino", "st_size", "st_mtime_ns"))
        self.assertIn("_identity_tuple(st_before) != _identity_tuple(after)", source)

    def test_proposal_documents_are_blocked_and_transport_is_fixed(self):
        self.assertEqual(template.REMOTE_PYTHON_EXECUTABLE, "/usr/bin/python3")
        for option in ("-o ControlPath=none", "-o ForwardAgent=no", "-o ForwardX11=no"):
            self.assertIn(option, template.SSH_OPTIONS)
        command = (ROOT / "command-audit-and-whitelist-v2.md").read_text(encoding="utf-8")
        approval = (ROOT / "next-step-approval-request-v2.md").read_text(encoding="utf-8")
        for text in (command, approval):
            self.assertIn("NOT_APPROVED_DO_NOT_EXECUTE", text)
            self.assertIn("-T", text)
            self.assertIn("ControlMaster=no", text)
            self.assertIn("ClearAllForwardings=yes", text)
            self.assertIn("StrictHostKeyChecking=yes", text)
            self.assertIn(template.KNOWN_HOSTS_OPTION, text)
            self.assertIn("-I -S -B", text)
            self.assertIn("/usr/bin/python3", text)
            self.assertIn("ControlPath=none", text)
            self.assertIn("ForwardAgent=no", text)
            self.assertIn("ForwardX11=no", text)
            self.assertIn("EACCES", text)
            self.assertIn("ST_RDONLY", text)
            for field in template.STAT_FIELDS:
                self.assertIn(field, text)
            self.assertIn("目标文件/挂载点只读", text)
            self.assertIn("整张 SD 卡零写入", text)
            self.assertIn("SSH", text)
            self.assertIn("log", text.lower())
            self.assertNotIn("sudo -n", text)

    def test_template_hash_manifest_matches_exact_source_bytes(self):
        source = (PROPOSALS / "remote_template_v2.py").read_bytes()
        manifest = (PROPOSALS / "remote-template-sha256.txt").read_text(encoding="ascii").strip()
        self.assertEqual(manifest, hashlib.sha256(source).hexdigest())

    def test_frozen_texts_embed_tested_core_and_fail_closed_entrypoints(self):
        source = (PROPOSALS / "remote_template_v2.py").read_text(encoding="utf-8")
        frozen_expectations = (
            ("frozen-remote-b1.NOT_APPROVED.txt", FROZEN_B1_WRAPPER),
            ("frozen-remote-b2.NOT_APPROVED.txt", FROZEN_B2_WRAPPER),
        )
        manifest_lines = (PROPOSALS / "remote-frozen-sha256.txt").read_text(encoding="ascii").splitlines()
        manifest = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in manifest_lines}
        for filename, wrapper in frozen_expectations:
            with self.subTest(filename=filename):
                raw = (PROPOSALS / filename).read_bytes()
                text = raw.decode("utf-8")
                self.assertEqual(text, source + wrapper)
                ast.parse(text)
                self.assertEqual(manifest[filename], hashlib.sha256(raw).hexdigest())
                self.assertIn('if EXECUTION_STATUS != "APPROVED":', text)
                self.assertIn("raise SystemExit(2)", text)
                self.assertIn("raise SystemExit(3)", text)
                self.assertIn("_remote_os.geteuid() == 0", text)
                self.assertIn("raise SystemExit(4)", text)
                if filename.endswith("b2.NOT_APPROVED.txt"):
                    self.assertIn('"boot_dev_differs_from_root": result.boot_dev_differs_from_root', text)
                self.assertIn("print(_json.dumps(payload", text)
                forbidden = {"subprocess", "socket", "paramiko", "fabric", "pexpect"}
                for node in ast.walk(ast.parse(text)):
                    if isinstance(node, ast.Import):
                        self.assertFalse({alias.name.split(".")[0] for alias in node.names} & forbidden)
                    elif isinstance(node, ast.ImportFrom):
                        self.assertNotIn((node.module or "").split(".")[0], forbidden)


if __name__ == "__main__":
    unittest.main()
