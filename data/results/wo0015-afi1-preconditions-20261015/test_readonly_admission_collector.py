"""All collector inputs are offline byte fixtures; never use the real backend."""
import base64
import copy
import hashlib
import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

MODULE = Path(__file__).with_name("readonly_admission_collector.py")


@pytest.fixture
def mod():
    spec = importlib.util.spec_from_file_location("readonly_collector", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeFS:
    kind = "OFFLINE_FIXTURE_NOT_BOARD"
    def __init__(self, m):
        self.calls = []
        self.pid = 999
        self.uid = 0
        self.files = {
            m.BOOT: (m.EXPECTED_BOOT + "\n").encode(),
            m.UPTIME: b"100.00 90.00\n",
            m.KERNEL: (m.EXPECTED_KERNEL + "\n").encode(),
            m.MODEL: b"PYNQ-Z2\x00",
            m.COMPATIBLE: b"xlnx,zynq-7000\x00",
            m.MEMINFO: b"CmaTotal: 131072 kB\nCmaFree: 52852 kB\n",
            m.MANAGER: b"operating\n", m.FLAGS: b"0\n",
            m.BIT: b"offline-bit-fixture\x00\n",
            m.HWH: b"offline-hwh-fixture\r\n",
            m.GLOBAL: json.dumps({"bitfile_name": m.BIT, "bitfile_hash": hashlib.sha1(b"offline-bit-fixture\x00\n").hexdigest(), "shutdown_ips": {}, "ip_dict": {}}).encode() + b"\n",
            "/proc/1/cmdline": b"systemd\x00", "/proc/1/maps": b"",
        }
        for p in m.GUARD_LOCKS:
            self.files[p] = b"{\"offline_guard\": true}\n"
        self.links = {"/proc/1/fd/0": "/dev/null"}
        self.after = {}
        self.counts = {}
    def read(self, path):
        self.calls.append(("read", path))
        n = self.counts.get(path, 0)
        self.counts[path] = n + 1
        if n and path in self.after: return self.after[path]
        value = self.files[path]
        if isinstance(value, Exception): raise value
        return value
    def identity(self):
        self.calls.append(("identity",))
        return self.uid, self.pid, "armv7l"
    def list_pids(self):
        self.calls.append(("list_pids",))
        return [1, self.pid]
    def list_fds(self, pid): return [0]
    def readlink(self, path): return self.links[path]


@pytest.fixture
def scene(mod, monkeypatch):
    fs = FakeFS(mod)
    monkeypatch.setattr(mod, "ARTIFACT_LOCKS", {p: hashlib.sha256(fs.files[p]).hexdigest() for p in (mod.BIT, mod.HWH)})
    monkeypatch.setattr(mod, "GUARD_LOCKS", {p: hashlib.sha256(fs.files[p]).hexdigest() for p in mod.GUARD_LOCKS})
    return mod, fs


def run(scene, challenge="0123456789abcdef0123456789abcdef"):
    m, fs = scene
    return m.collect_os_evidence(fs, challenge=challenge)


def test_os_snapshot_does_not_grant_mmio(scene):
    result = run(scene)
    assert result.get("os_precheck") == "VERIFIED"
    assert result["status"] == "STOPPED_PRECONDITION_UNVERIFIED"
    assert result["checks"]["current_reset_state"]["status"] == "UNKNOWN"
    assert result["checks"]["reset_definition"]["status"] == "CONFLICT"
    assert result["checks"]["loaded_hardware_identity"]["status"] == "INFERRED"
    assert result["afi_read_authorized"] is False
    assert result["register_reads"] == result["register_writes"] == 0
    assert result["evidence_kind"] == "OFFLINE_FIXTURE_NOT_BOARD"


def test_raw_bytes_hash_before_text_normalization(scene):
    m, fs = scene
    result = run(scene)
    for p in (m.BOOT, m.HWH, *m.GUARD_LOCKS):
        item = next(v for v in result["samples"] if v["path"] == p)
        assert base64.b64decode(item["raw_base64"]) == fs.files[p]
        assert item["sha256"] == hashlib.sha256(fs.files[p]).hexdigest()
        assert item["sha256"] != hashlib.sha256(fs.files[p].strip()).hexdigest()


@pytest.mark.parametrize("bad", ["", "abc", "0" * 31, "g" * 32, True, None])
def test_fresh_challenge_required_before_any_file_read(scene, bad):
    result = run(scene, bad)
    assert result["os_precheck"] == "UNKNOWN"
    assert scene[1].calls == []


@pytest.mark.parametrize("target", ["boot", "bit", "hwh", "guard", "kernel", "manager", "flags", "global", "missing", "uid", "arch"])
def test_fail_closed_on_identity_environment_or_hash(scene, target):
    m, fs = scene
    if target == "boot": fs.files[m.BOOT] = b"unexpected-boot\n"
    elif target == "bit": fs.files[m.BIT] += b"x"
    elif target == "hwh": fs.files[m.HWH] += b"x"
    elif target == "guard": fs.files[next(iter(m.GUARD_LOCKS))] += b"x"
    elif target == "kernel": fs.files[m.KERNEL] = b"other-kernel\n"
    elif target == "manager": fs.files[m.MANAGER] = b"unknown\n"
    elif target == "flags": fs.files[m.FLAGS] = b"1\n"
    elif target == "global": fs.files[m.GLOBAL] = b"{}\n"
    elif target == "missing": fs.files[m.BOOT] = PermissionError("denied")
    elif target == "uid": fs.uid = 1000
    elif target == "arch": fs.identity = lambda: (0, 999, "x86_64")
    result = run(scene)
    assert result["os_precheck"] == "UNKNOWN"
    assert result["status"] == "STOPPED_PRECONDITION_UNVERIFIED"
    assert result["errors"]
    assert result["register_reads"] == 0


@pytest.mark.parametrize("change", ["boot", "global", "uptime_reverse", "uptime_slow", "uptime_nan", "uptime_inf"])
def test_window_continuity_and_metadata_race_rejected(scene, change):
    m, fs = scene
    p, raw = {"boot": (m.BOOT, b"changed\n"), "global": (m.GLOBAL, b"{}\n"), "uptime_reverse": (m.UPTIME, b"99 0\n"), "uptime_slow": (m.UPTIME, b"111 0\n"), "uptime_nan": (m.UPTIME, b"nan 0\n"), "uptime_inf": (m.UPTIME, b"inf 0\n")}[change]
    fs.after[p] = raw
    assert run(scene)["os_precheck"] == "UNKNOWN"


@pytest.mark.parametrize("owner", ["mem_fd", "uio_map", "dri_fd", "zocl_fd", "owner_command", "denied", "vanished"])
def test_process_ownership_and_visibility_gate(scene, owner):
    _, fs = scene
    if owner == "mem_fd": fs.links["/proc/1/fd/0"] = "/dev/mem"
    elif owner == "uio_map": fs.files["/proc/1/maps"] = b"1000-2000 rw-s 0000 /dev/uio0\n"
    elif owner == "dri_fd": fs.links["/proc/1/fd/0"] = "/dev/dri/renderD128"
    elif owner == "zocl_fd": fs.links["/proc/1/fd/0"] = "/dev/zocl"
    elif owner == "owner_command": fs.files["/proc/1/cmdline"] = b"python\x00ipykernel_launcher\x00"
    elif owner == "denied": fs.files["/proc/1/maps"] = PermissionError("denied")
    elif owner == "vanished": fs.files["/proc/1/maps"] = FileNotFoundError("gone")
    result = run(scene)
    assert result["os_precheck"] == "UNKNOWN"
    assert result["ownership"]["findings"] or result["ownership"]["unreadable"]


@pytest.mark.parametrize("field,value", [("bitfile_name", "/other/wo15.bit"), ("bitfile_hash", "0" * 40), ("shutdown_ips", []), ("ip_dict", [])])
def test_global_state_schema_or_file_identity_rejected(scene, field, value):
    m, fs = scene
    obj = json.loads(fs.files[m.GLOBAL])
    obj[field] = value
    fs.files[m.GLOBAL] = json.dumps(obj).encode()
    assert run(scene)["os_precheck"] == "UNKNOWN"


def test_nonempty_shutdown_metadata_is_only_observed_never_invoked(scene):
    m, fs = scene
    obj = json.loads(fs.files[m.GLOBAL])
    obj["shutdown_ips"] = {"shutdown": {"address": 0x1234}}
    fs.files[m.GLOBAL] = json.dumps(obj).encode()
    assert run(scene)["os_precheck"] == "VERIFIED"
    assert all(not (len(c) > 1 and c[1].startswith("/dev")) for c in fs.calls)


def test_old_true_boolean_dictionary_cannot_be_passed_as_verifier(mod):
    result = mod.collect_os_evidence(dict.fromkeys(["current_reset_deasserted", "safe_access_confirmed"], True), challenge="a" * 32)
    assert result["os_precheck"] == "UNKNOWN"
    assert result["afi_read_authorized"] is False


@pytest.mark.parametrize("path", ["/dev/mem", "/dev/uio0", "/sys/class/fpga_manager/fpga0/firmware", "/etc/shadow", "/proc/../dev/mem"])
def test_real_backend_rejects_nonwhitelisted_paths_without_open(mod, monkeypatch, path):
    def forbidden(*args, **kwargs): raise AssertionError("OS open forbidden in offline test")
    monkeypatch.setattr(mod.os, "open", forbidden)
    with pytest.raises(ValueError): mod.ReadOnlyOSFiles().read(path)


def test_import_and_cli_have_no_collector_side_effects(mod):
    assert not hasattr(mod, "read_afi1")
    assert not hasattr(mod, "mmap")
    assert mod.cli() == 2


@pytest.mark.parametrize("raw", [b"{\"shutdown_ips\":{},\"ip_dict\":{},\"bitfile_name\":\"bad\",\"bitfile_name\":\"bad2\"}", b"[]", b"not-json", b"\xff"])
def test_ambiguous_or_invalid_metadata_never_passes(scene, raw):
    m, fs = scene
    fs.files[m.GLOBAL] = raw
    assert run(scene)["os_precheck"] == "UNKNOWN"


def test_duplicate_key_cannot_be_hidden_by_final_valid_value(scene):
    m, fs = scene
    original = fs.files[m.GLOBAL].decode()
    fs.files[m.GLOBAL] = ('{"bitfile_name":"/bad/bit",' + original[1:]).encode()
    assert run(scene)["os_precheck"] == "UNKNOWN"


def test_raw_guard_newline_conversion_is_detected(scene):
    m, fs = scene
    path = next(iter(m.GUARD_LOCKS))
    fs.files[path] = fs.files[path].replace(b"\n", b"\r\n")
    assert run(scene)["os_precheck"] == "UNKNOWN"


def fake_lowlevel_backend(mod, monkeypatch, data, *, mode=stat.S_IFREG, read_error=False):
    events = []
    remaining = [data]
    monkeypatch.setattr(mod.os, "O_CLOEXEC", 0x100, raising=False)
    monkeypatch.setattr(mod.os, "O_NOFOLLOW", 0x200, raising=False)
    monkeypatch.setattr(mod.os, "O_NONBLOCK", 0x400, raising=False)
    def opened(path, flags): events.append(("open", path, flags)); return 42
    def fstat(fd):
        events.append(("fstat", fd))
        class Info: st_mode = mode
        return Info()
    def read(fd, count):
        events.append(("read", fd, count))
        if read_error: raise OSError("offline-fake")
        if not remaining: return b""
        chunk = remaining.pop(0)
        return chunk
    monkeypatch.setattr(mod.os, "open", opened)
    monkeypatch.setattr(mod.os, "fstat", fstat)
    monkeypatch.setattr(mod.os, "read", read)
    monkeypatch.setattr(mod.os, "close", lambda fd: events.append(("close", fd)))
    return events


def test_backend_opens_readonly_and_preserves_bytes_then_closes(mod, monkeypatch):
    events = fake_lowlevel_backend(mod, monkeypatch, b"original\r\n\x00")
    assert mod.ReadOnlyOSFiles().read(mod.GLOBAL) == b"original\r\n\x00"
    assert events[0] == ("open", mod.GLOBAL, mod.os.O_RDONLY | mod.os.O_CLOEXEC | mod.os.O_NOFOLLOW | mod.os.O_NONBLOCK)
    assert events[-1] == ("close", 42)
    assert not events[0][2] & (mod.os.O_WRONLY | mod.os.O_RDWR)


@pytest.mark.parametrize("mode", [stat.S_IFCHR, stat.S_IFBLK, stat.S_IFDIR, stat.S_IFIFO])
def test_backend_rejects_device_or_nonregular_file_before_read(mod, monkeypatch, mode):
    events = fake_lowlevel_backend(mod, monkeypatch, b"never", mode=mode)
    with pytest.raises(ValueError): mod.ReadOnlyOSFiles().read(mod.BOOT)
    assert not any(e[0] == "read" for e in events)
    assert events[-1] == ("close", 42)


def test_backend_read_error_closes_descriptor_no_retry(mod, monkeypatch):
    events = fake_lowlevel_backend(mod, monkeypatch, b"", read_error=True)
    with pytest.raises(OSError): mod.ReadOnlyOSFiles().read(mod.BOOT)
    assert len([e for e in events if e[0] == "open"]) == 1
    assert len([e for e in events if e[0] == "read"]) == 1
    assert events[-1] == ("close", 42)


def test_backend_bounded_read_limit(mod, monkeypatch):
    events = fake_lowlevel_backend(mod, monkeypatch, b"x" * 101)
    monkeypatch.setattr(mod, "MAX_BYTES", 100)
    with pytest.raises(ValueError): mod.ReadOnlyOSFiles().read(mod.BOOT)
    assert events[-1] == ("close", 42)


@pytest.mark.parametrize("pid", [0, -1, "1/../../dev", True])
def test_backend_dynamic_proc_paths_are_typed_and_scoped(mod, monkeypatch, pid):
    monkeypatch.setattr(mod.os, "listdir", lambda path: pytest.fail("Unexpected OS listing"))
    with pytest.raises(ValueError): mod.ReadOnlyOSFiles().list_fds(pid)


def test_backend_observes_link_target_without_opening_device(mod, monkeypatch):
    monkeypatch.setattr(mod.os, "open", lambda *a: pytest.fail("Unexpected open"))
    monkeypatch.setattr(mod.os, "readlink", lambda path: "/dev/mem")
    assert mod.ReadOnlyOSFiles().readlink("/proc/12/fd/3") == "/dev/mem"
    with pytest.raises(ValueError): mod.ReadOnlyOSFiles().readlink("/dev/mem")
