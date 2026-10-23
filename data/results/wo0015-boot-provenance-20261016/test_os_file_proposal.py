"""Offline fakes only: proposed future OS files are never read by these tests."""
import base64
import hashlib
import importlib.util
import stat
from pathlib import Path

import pytest


@pytest.fixture
def mod():
    path = Path(__file__).with_name("os_file_proposal.py")
    spec = importlib.util.spec_from_file_location("boot_os_proposal", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Fake:
    kind = "OFFLINE_SYNTHETIC_NOT_BOARD"
    def __init__(self, m):
        self.calls = []
        self.files = {p: b"fixture\r\n" for p in m.OS_PATHS}
        self.files[m.BOOT_ID] = (m.EXPECTED_BOOT + "\n").encode()
        self.files[m.MOUNTINFO] = b"12 1 179:1 / /boot ro,relatime - vfat /dev/mmcblk0p1 ro\n"
        self.files[m.BOOT_BIN] = b"synthetic-not-actual-BOOT\x00\n"
        self.after_boot = None
        self.nboot = 0
    def read_regular(self, path, *, max_bytes, require_readonly_mount=False):
        self.calls.append((path, require_readonly_mount))
        if path.endswith("boot_id"):
            self.nboot += 1
            if self.nboot > 1 and self.after_boot: return self.after_boot
        raw = self.files[path]
        if isinstance(raw, Exception): raise raw
        if len(raw) > max_bytes: raise ValueError("oversized")
        return raw


def test_default_cli_does_not_collect(mod):
    assert mod.cli() == 2


@pytest.mark.parametrize("stage", ["os-metadata", "boot-bin"])
def test_fixed_whitelist_stage_and_raw_bytes(mod, stage):
    fs = Fake(mod)
    result = mod.collect(fs, stage=stage, nonce="a" * 32)
    assert result.get("status") == "STOPPED_PRECONDITION_UNVERIFIED"
    assert result["capture_status"] == "COLLECTED_OS_FILES_ONLY"
    assert result["afi_authorized"] is False
    assert result["evidence_kind"] == "OFFLINE_SYNTHETIC_NOT_BOARD"
    for item in result["samples"]:
        assert base64.b64decode(item["raw_base64"]) == fs.files[item["path"]]
        assert item["sha256"] == hashlib.sha256(fs.files[item["path"]]).hexdigest()
    if stage == "os-metadata": assert not any(p == mod.BOOT_BIN for p, _ in fs.calls)
    else: assert (mod.BOOT_BIN, True) in fs.calls


@pytest.mark.parametrize("stage,nonce", [("dma", "a" * 32), ("os-metadata", "bad"), ("boot-bin", True)])
def test_invalid_request_never_reads(mod, stage, nonce):
    fs = Fake(mod)
    result = mod.collect(fs, stage=stage, nonce=nonce)
    assert result["capture_status"] == "STOPPED"
    assert fs.calls == []


def test_boot_mismatch_stops_before_other_files(mod):
    fs = Fake(mod)
    fs.files[mod.BOOT_ID] = b"other\n"
    result = mod.collect(fs, stage="boot-bin", nonce="b" * 32)
    assert result["capture_status"] == "STOPPED"
    assert fs.calls == [(mod.BOOT_ID, False)]


def test_boot_changes_after_collection_keeps_evidence_and_stops(mod):
    fs = Fake(mod)
    fs.after_boot = b"other\n"
    result = mod.collect(fs, stage="os-metadata", nonce="b" * 32)
    assert result["capture_status"] == "STOPPED"
    assert result["samples"]


@pytest.mark.parametrize("mount", [b"1 0 179:1 / /boot rw - vfat /dev/mmcblk0p1 rw\n", b"1 0 8:1 / /boot ro - vfat /dev/sda1 ro\n", b"1 0 179:1 / / ro - ext4 /dev/mmcblk0p1 ro\n", b"bad\n", b"1 0 179:1 / /boot ro - autofs auto ro\n"])
def test_boot_file_requires_existing_expected_readonly_mount(mod, mount):
    fs = Fake(mod)
    fs.files[mod.MOUNTINFO] = mount
    result = mod.collect(fs, stage="boot-bin", nonce="b" * 32)
    assert result["capture_status"] == "STOPPED"
    assert not any(p == mod.BOOT_BIN for p, _ in fs.calls)


def test_read_failure_keeps_partial_evidence_no_retry(mod):
    fs = Fake(mod)
    fs.files[mod.OS_PATHS[1]] = PermissionError("denied")
    result = mod.collect(fs, stage="os-metadata", nonce="b" * 32)
    assert result["capture_status"] == "STOPPED"
    assert len([p for p, _ in fs.calls if p == mod.OS_PATHS[1]]) == 1
    assert result["samples"]


@pytest.mark.parametrize("path", ["/dev/mem", "/dev/mmcblk0", "/boot/boot.scr", "/etc/shadow", "/proc/../dev/mem"])
def test_backend_rejects_unknown_paths_before_os_open(mod, monkeypatch, path):
    monkeypatch.setattr(mod.os, "open", lambda *a, **k: pytest.fail("unexpected open"))
    with pytest.raises(ValueError): mod.PinnedReadOnlyFiles().read_regular(path, max_bytes=100)


def fake_backend_os(mod, monkeypatch, *, mode=stat.S_IFREG, ro=True, raw=b"a\r\n"):
    events = []
    for name, val in [("O_PATH", 0x200000), ("O_NOFOLLOW", 0x20000), ("O_CLOEXEC", 0x80000), ("O_NOATIME", 0x40000), ("O_NONBLOCK", 0x800), ("ST_RDONLY", 1)]:
        monkeypatch.setattr(mod.os, name, val, raising=False)
    def opened(path, flags):
        events.append(("open", path, flags))
        return 10 if path != "/proc/self/fd/10" else 11
    def info(fd):
        class S:
            st_mode = mode; st_dev = 1; st_ino = 2
        return S()
    def vfs(fd):
        class V: f_flag = 1 if ro else 0
        return V()
    chunks = [raw, b""]
    monkeypatch.setattr(mod.os, "open", opened)
    monkeypatch.setattr(mod.os, "fstat", info)
    monkeypatch.setattr(mod.os, "fstatvfs", vfs, raising=False)
    monkeypatch.setattr(mod.os, "read", lambda fd, n: events.append(("read", fd)) or chunks.pop(0))
    monkeypatch.setattr(mod.os, "close", lambda fd: events.append(("close", fd)))
    return events


@pytest.mark.parametrize("mode", [stat.S_IFCHR, stat.S_IFBLK, stat.S_IFLNK, stat.S_IFIFO, stat.S_IFDIR])
def test_opath_rejects_nonregular_before_readable_open(mod, monkeypatch, mode):
    events = fake_backend_os(mod, monkeypatch, mode=mode)
    with pytest.raises(ValueError): mod.PinnedReadOnlyFiles().read_regular(mod.BOOT_ID, max_bytes=100)
    assert len([e for e in events if e[0] == "open"]) == 1
    assert not any(e[0] == "read" for e in events)
    assert events[-1] == ("close", 10)


def test_pinned_regular_reopens_same_fd_readonly_noatime(mod, monkeypatch):
    events = fake_backend_os(mod, monkeypatch)
    assert mod.PinnedReadOnlyFiles().read_regular(mod.BOOT_ID, max_bytes=100) == b"a\r\n"
    opens = [e for e in events if e[0] == "open"]
    assert opens[0][2] & mod.os.O_PATH
    assert opens[1][1] == "/proc/self/fd/10"
    assert opens[1][2] & mod.os.O_NOATIME
    assert not opens[1][2] & (mod.os.O_WRONLY | mod.os.O_RDWR)
    assert events[-2:] == [("close", 11), ("close", 10)]


def test_boot_mount_rw_stops_before_readable_open_even_if_mountinfo_ro(mod, monkeypatch):
    events = fake_backend_os(mod, monkeypatch, ro=False)
    with pytest.raises(ValueError): mod.PinnedReadOnlyFiles().read_regular(mod.BOOT_BIN, max_bytes=100, require_readonly_mount=True)
    assert len([e for e in events if e[0] == "open"]) == 1
    assert not any(e[0] == "read" for e in events)


def test_boot_readonly_requirement_cannot_be_disabled(mod, monkeypatch):
    monkeypatch.setattr(mod.os, "open", lambda *a: pytest.fail("unexpected open"))
    with pytest.raises(ValueError): mod.PinnedReadOnlyFiles().read_regular(mod.BOOT_BIN, max_bytes=100)
