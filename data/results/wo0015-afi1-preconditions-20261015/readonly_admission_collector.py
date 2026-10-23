"""OS-files-only evidence collector, not authorized for board execution yet.

No register reader, PYNQ, transport or writable interface exists in this module.
No CLI option can enable board collection or accept an external PASS boolean.
"""
import base64
import datetime
import hashlib
import json
import math
import os
import platform
import re
import stat
import sys
import time

VERSION = "wo0015-readonly-admission-v2-offline-reviewed"
EXPECTED_BOOT = "822a9c29-37c0-438c-81c4-da998d57073b"
EXPECTED_KERNEL = "6.6.10-xilinx-v2024.1-g3c0eca68c652"
BOOT = "/proc/sys/kernel/random/boot_id"
UPTIME = "/proc/uptime"
KERNEL = "/proc/sys/kernel/osrelease"
MODEL = "/proc/device-tree/model"
COMPATIBLE = "/proc/device-tree/compatible"
MEMINFO = "/proc/meminfo"
MANAGER = "/sys/class/fpga_manager/fpga0/state"
FLAGS = "/sys/class/fpga_manager/fpga0/flags"
BIT = "/home/xilinx/wo0015_first_board_retry3_20261015/wo15.bit"
HWH = "/home/xilinx/wo0015_first_board_retry3_20261015/wo15.hwh"
GLOBAL = "/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq/pl_server/global_pl_state_.json"
ARTIFACT_LOCKS = {BIT: "a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420",
                  HWH: "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6"}
GUARD_LOCKS = {
    "/var/lib/wo0015-s2mm/overlay-only-pass.json": "ee3d9ba6462962657bbd43807302387af16d4fbec34ee65f909a385886802d96",
    "/var/lib/wo0015-s2mm/terminal-failure.json": "b796fba0208adbfe2ec5df7ceaa6a3988f6602f9c1f6e18c8b74a6255817421e",
    "/var/lib/wo0015-s2mm/cma-watermark-recovery-consumed.json": "ed86d62376dfaf78673b7af9b0e014e5a3ffbb56909b70ac5f40eb97abcc4de0",
    "/var/lib/wo0015-s2mm/overlay-only-consumed.json": "c333975de186464bc3db9809c7762d7d7115cfc73f8b17f8e8672e3349efa37f",
    "/var/lib/wo0015-s2mm/dma-single-consumed.json": "1585844bceb4bca82cee2f4a2be14916b2fee847a26fb7f28c207e9234fcf862",
    "/var/lib/wo0015-s2mm/dma-single-watermark-retry2-consumed.json": "1585844bceb4bca82cee2f4a2be14916b2fee847a26fb7f28c207e9234fcf862",
    "/var/lib/wo0015-s2mm/run-only-init-consumed.json": "ac53e6acba00404c86acd7c2e5dae1729ef0a62228538f857b8104d6b866df18",
}
FIXED_FILES = frozenset((BOOT, UPTIME, KERNEL, MODEL, COMPATIBLE, MEMINFO,
                         MANAGER, FLAGS, GLOBAL, *ARTIFACT_LOCKS, *GUARD_LOCKS))
MAX_BYTES = 16 * 1024 * 1024
MAX_WINDOW_SECONDS = 5.0


def collect_os_evidence(io, *, challenge):
    """Collect new OS bytes, never import/run the previous register candidate.

    A future authenticated fixed transport must generate a fresh 128-bit nonce,
    pin this source hash and preserve raw stdout. This function is not a remote
    attestation protocol: injected backends and returned JSON are not trusted by
    an AFI reader. No such reader is connected to it.
    """
    result = {"version": VERSION, "status": "STOPPED_PRECONDITION_UNVERIFIED",
              "os_precheck": "UNKNOWN", "afi_read_authorized": False,
              "register_reads": 0, "register_writes": 0,
              "started_utc": _utc(), "challenge": challenge,
              "evidence_kind": getattr(io, "kind", "UNTRUSTED_INPUT"),
              "samples": [], "errors": [], "ownership": {"findings": [], "unreadable": []},
              "checks": {
                  "reset_definition": {"status": "CONFLICT", "reason": "UG585 AFI prerequisite vs generated reserved AXDS1 definition unresolved"},
                  "current_reset_state": {"status": "UNKNOWN", "reason": "No hardware read performed or accepted"},
                  "loaded_hardware_identity": {"status": "UNKNOWN", "reason": "Software metadata does not attest current PL bits"}}}
    begin = time.monotonic()

    def sample(path, *, retain_raw=True):
        raw = io.read(path)
        if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
            raise ValueError("Source must return bounded original bytes: " + path)
        digest = hashlib.sha256(raw).hexdigest()
        item = {"path": path, "sha256": digest, "nbytes": len(raw), "read_utc": _utc(),
                "raw_base64": base64.b64encode(raw).decode("ascii") if retain_raw else None}
        result["samples"].append(item)
        return raw

    try:
        if not isinstance(challenge, str) or re.fullmatch(r"[0-9a-f]{32}", challenge) is None:
            raise ValueError("A fresh lowercase 128-bit host challenge is required")
        uid, pid, arch = io.identity()
        if uid != 0 or arch != "armv7l" or type(pid) is not int or pid <= 0:
            raise ValueError("Root armv7l OS identity is required for complete process visibility")
        result["process_identity"] = {"uid": uid, "pid": pid, "architecture": arch}
        boot_before = sample(BOOT)
        if boot_before.decode("ascii").strip() != EXPECTED_BOOT:
            raise ValueError("Authorized boot_id mismatch")
        uptime_before = _uptime(sample(UPTIME))
        if sample(KERNEL).decode("ascii").strip() != EXPECTED_KERNEL:
            raise ValueError("Kernel differs from reviewed session")
        model = sample(MODEL).rstrip(b"\x00").decode("ascii")
        compatible = sample(COMPATIBLE).split(b"\x00")
        if "PYNQ-Z2" not in model or b"xlnx,zynq-7000" not in compatible:
            raise ValueError("Device-tree platform identity mismatch")
        result["checks"]["os_session_identity"] = {"status": "VERIFIED", "meaning": "OS-reported identity only", "boot_id": EXPECTED_BOOT, "model": model}
        state_before, flags_before = sample(MANAGER), sample(FLAGS)
        if state_before.strip() != b"operating" or flags_before.strip() != b"0":
            raise ValueError("FPGA manager must report operating/flags=0")
        mem = sample(MEMINFO).decode("ascii")
        values = {}
        for key in ("CmaTotal", "CmaFree"):
            hits = re.findall(r"^" + key + r":\s+(\d+)\s+kB\s*$", mem, re.MULTILINE)
            if len(hits) != 1: raise ValueError("Ambiguous/missing CMA telemetry")
            values[key] = int(hits[0])
        if not 0 <= values["CmaFree"] <= values["CmaTotal"]:
            raise ValueError("Invalid CMA telemetry")
        result["cma_telemetry_kib"] = values  # No allocation or difference gate.
        bits = {}
        for path, expected in ARTIFACT_LOCKS.items():
            raw = sample(path)
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError("Locked artifact SHA-256 mismatch: " + path)
            bits[path] = raw
        for path, expected in GUARD_LOCKS.items():
            if hashlib.sha256(sample(path)).hexdigest() != expected:
                raise ValueError("Original-byte Guard SHA-256 mismatch: " + path)
        result["checks"]["artifact_and_guard_bytes"] = {"status": "VERIFIED", "meaning": "Fixed files match historical locks; guards remain in place"}
        global_before = sample(GLOBAL)
        state = json.loads(global_before, object_pairs_hook=_unique_object)
        if not isinstance(state, dict) or not isinstance(state.get("shutdown_ips"), dict) or not isinstance(state.get("ip_dict"), dict):
            raise ValueError("PYNQ global-state schema mismatch")
        if state.get("bitfile_name") != BIT or state.get("bitfile_hash") != hashlib.sha1(bits[BIT]).hexdigest():
            raise ValueError("PYNQ cached loaded-file identity mismatch")
        result["checks"]["cached_loaded_file_identity"] = {"status": "VERIFIED", "source": GLOBAL, "meaning": "Match to software cache; no hardware attestation"}
        result["checks"]["loaded_hardware_identity"] = {"status": "INFERRED", "reason": "Cache/artifacts/manager support correspondence but cannot prove current configuration or reset"}
        _scan_ownership(io, pid, sample, result["ownership"])
        if result["ownership"]["findings"] or result["ownership"]["unreadable"]:
            raise ValueError("Conflicting process or incomplete ownership visibility")
        result["checks"]["process_ownership_snapshot"] = {"status": "VERIFIED", "meaning": "No owner found in one proc snapshot; not an exclusive lock against later races"}
        if sample(GLOBAL) != global_before or sample(MANAGER) != state_before or sample(FLAGS) != flags_before:
            raise ValueError("Loaded-state metadata changed during snapshot")
        uptime_after = _uptime(sample(UPTIME))
        if sample(BOOT) != boot_before:
            raise ValueError("Boot changed during snapshot")
        elapsed = time.monotonic() - begin
        if not (0 <= uptime_after - uptime_before <= MAX_WINDOW_SECONDS and 0 <= elapsed <= MAX_WINDOW_SECONDS):
            raise ValueError("Snapshot window stale, reversed or too slow")
        result["window"] = {"uptime_before": uptime_before, "uptime_after": uptime_after, "host_elapsed_seconds": elapsed}
        result["os_precheck"] = "VERIFIED"
    except Exception as exc:
        result["errors"].append({"type": type(exc).__name__, "message": str(exc)})
    result["ended_utc"] = _utc()
    return result


def _utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _uptime(raw):
    value = float(raw.decode("ascii").split()[0])
    if not math.isfinite(value) or value < 0:
        raise ValueError("Invalid uptime sample")
    return value


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value: raise ValueError("Duplicate metadata JSON key: " + key)
        value[key] = item
    return value


def _scan_ownership(io, current_pid, sample, output):
    device_markers = ("/dev/mem", "/dev/xdevcfg", "/dev/fpga", "/dev/uio", "/dev/dri/", "/dev/zocl")
    commands = ("ipykernel_launcher", "fpgautil", "openocd", "xsdb", "xvcserver", "board_once", "dma_replay_verify")
    pids = io.list_pids()
    if not isinstance(pids, list) or not pids or any(type(p) is not int or p <= 0 for p in pids) or len(pids) != len(set(pids)) or current_pid not in pids:
        raise ValueError("Incomplete or malformed proc listing")
    for pid in sorted(pids):
        if pid == current_pid: continue
        try:
            # Hash cmdlines without returning possibly sensitive command arguments.
            command = sample(f"/proc/{pid}/cmdline", retain_raw=False).replace(b"\x00", b" ").decode("utf-8", "replace")
            maps = sample(f"/proc/{pid}/maps", retain_raw=False).decode("utf-8", "replace")
            reasons = []
            if any(name in command for name in commands): reasons.append("known_hardware_owner_command")
            if any(name in maps for name in device_markers): reasons.append("device_mapping")
            for fd in io.list_fds(pid):
                target = io.readlink(f"/proc/{pid}/fd/{fd}")
                if any(target.startswith(name) for name in device_markers): reasons.append("device_fd")
            if reasons: output["findings"].append({"pid": pid, "reasons": sorted(set(reasons))})
        except OSError as exc:
            # No retry, and no silent success if a process disappears mid-scan.
            output["unreadable"].append({"pid": pid, "error": type(exc).__name__})


class ReadOnlyOSFiles:
    """Fixed OS-file backend. Never invoked on a real board in this delivery.

    Opening is O_RDONLY|O_NOFOLLOW; fstat rejects device objects before reading.
    O_NOFOLLOW protects only the final path component. Parent paths/mounts must
    be trusted; this is NOT containment against a hostile root or path races.
    This remaining backend constraint must be reviewed before live deployment.
    Only whitelisted sysfs state files, not arbitrary sysfs/debugfs nodes, exist.
    """
    kind = "OS_FILE_SNAPSHOT_NOT_REGISTER_PROOF"

    def identity(self):
        return os.geteuid(), os.getpid(), platform.machine()

    def read(self, path):
        if path not in FIXED_FILES and re.fullmatch(r"/proc/[1-9][0-9]*/(cmdline|maps)", path) is None:
            raise ValueError("OS read path not in fixed allowlist")
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("Only regular proc/sysfs/artifact files may be read")
            chunks, length = [], 0
            while True:
                chunk = os.read(fd, min(65536, MAX_BYTES + 1 - length))
                if not chunk: break
                chunks.append(chunk)
                length += len(chunk)
                if length > MAX_BYTES: raise ValueError("OS source exceeds read limit")
            return b"".join(chunks)
        finally:
            os.close(fd)

    def list_pids(self):
        return sorted(int(p) for p in os.listdir("/proc") if re.fullmatch(r"[1-9][0-9]*", p))

    def list_fds(self, pid):
        if type(pid) is not int or pid <= 0: raise ValueError("Invalid PID")
        return sorted(int(p) for p in os.listdir(f"/proc/{pid}/fd") if re.fullmatch(r"[0-9]+", p))

    def readlink(self, path):
        if re.fullmatch(r"/proc/[1-9][0-9]*/fd/[0-9]+", path) is None:
            raise ValueError("Only proc fd symlink targets may be observed")
        return os.readlink(path)


def cli():
    print("OFFLINE_REVIEW_ONLY: no live collector or register operation is enabled", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(cli())
