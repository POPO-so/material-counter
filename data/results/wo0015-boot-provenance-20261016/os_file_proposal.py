"""Future OS-file evidence proposal. Not executed on any real board.

Only fixed OS files and (in a separately approved stage) current BOOT.BIN.
No network, MMIO, PYNQ or initialized target binary is imported/executed.
"""
import base64
import datetime
import hashlib
import json
import os
import re
import stat
import sys

VERSION = "wo0015-boot-os-proposal-v1"
EXPECTED_BOOT = "822a9c29-37c0-438c-81c4-da998d57073b"
BOOT_ID = "/proc/sys/kernel/random/boot_id"
MOUNTINFO = "/proc/self/mountinfo"
BOOT_BIN = "/boot/BOOT.BIN"
OS_PATHS = (BOOT_ID, "/proc/sys/kernel/osrelease", "/proc/version", "/proc/cmdline", "/proc/uptime", MOUNTINFO)


def collect(io, *, stage, nonce):
    """Future caller must authenticate source/session; outputs never grant AFI.

    stage=os-metadata and stage=boot-bin are separately authorized scopes. The
    first cannot automatically advance to the second, even if mountinfo says ro.
    """
    result = {"version": VERSION, "stage": stage, "nonce": nonce,
              "status": "STOPPED_PRECONDITION_UNVERIFIED", "capture_status": "STOPPED",
              "afi_authorized": False, "evidence_kind": getattr(io, "kind", "UNTRUSTED_INPUT"),
              "samples": [], "errors": [], "started_utc": _utc()}
    def sample(path, readonly=False):
        raw = io.read_regular(path, max_bytes=64 * 1024 * 1024 if path == BOOT_BIN else 1024 * 1024,
                              require_readonly_mount=readonly)
        if not isinstance(raw, bytes): raise ValueError("Original bytes required")
        result["samples"].append({"path": path, "nbytes": len(raw),
                                  "sha256": hashlib.sha256(raw).hexdigest(),
                                  "raw_base64": base64.b64encode(raw).decode("ascii"), "read_utc": _utc()})
        return raw
    try:
        if stage not in ("os-metadata", "boot-bin") or not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
            raise ValueError("Unapproved stage or malformed fresh host nonce")
        first = sample(BOOT_ID)
        if first.decode("ascii").strip() != EXPECTED_BOOT: raise ValueError("Authorized boot mismatch")
        if stage == "os-metadata":
            for path in OS_PATHS[1:]: sample(path)
        else:
            before_mount = sample(MOUNTINFO)
            _require_boot_mount(before_mount)
            sample(BOOT_BIN, readonly=True)
            if sample(MOUNTINFO) != before_mount: raise ValueError("Mount topology/options changed")
        if sample(BOOT_ID) != first: raise ValueError("Boot changed during capture")
        result["capture_status"] = "COLLECTED_OS_FILES_ONLY"
    except Exception as exc:
        result["errors"].append({"type": type(exc).__name__, "message": str(exc)})
    result["ended_utc"] = _utc()
    return result


def _utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _require_boot_mount(raw):
    matches = []
    for line in raw.decode("ascii").splitlines():
        fields = line.split()
        if len(fields) < 10 or "-" not in fields: raise ValueError("Malformed mountinfo")
        separator = fields.index("-")
        if len(fields) <= separator + 3: raise ValueError("Truncated mountinfo")
        if fields[4] == "/boot": matches.append((fields, separator))
    if len(matches) != 1: raise ValueError("Exactly one existing /boot mount required")
    fields, sep = matches[0]
    if (fields[2] != "179:1" or fields[3] != "/" or fields[sep + 1] != "vfat" or
            fields[sep + 2] != "/dev/mmcblk0p1" or "ro" not in fields[5].split(",") or
            "ro" not in fields[sep + 3].split(",")):
        raise ValueError("BOOT read requires pre-existing readonly reviewed mmcblk0p1 vfat mount")


class PinnedReadOnlyFiles:
    """Linux only; fixed allowlist, O_PATH pins inode before any readable open.

    O_PATH does not open a character/block device for driver operations. Non-
    regular objects (including final symlinks) are rejected before a readable fd
    is acquired. /proc/self/fd reopening uses only this process's pinned fd.
    This assumes a trusted kernel/proc mount, not resistance to malicious root.
    """
    kind = "CURRENT_OS_FILES_NOT_BOOT_EXECUTION_PROOF"

    def read_regular(self, path, *, max_bytes, require_readonly_mount=False):
        if path not in (*OS_PATHS, BOOT_BIN): raise ValueError("Path outside exact whitelist")
        if path == BOOT_BIN and not require_readonly_mount: raise ValueError("Readonly BOOT mount requirement cannot be disabled")
        if type(max_bytes) is not int or max_bytes <= 0 or max_bytes > 64 * 1024 * 1024:
            raise ValueError("Invalid source size limit")
        pinned = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            original = os.fstat(pinned)
            if not stat.S_ISREG(original.st_mode): raise ValueError("Only regular files allowed; no devices or symlinks")
            if require_readonly_mount and not os.fstatvfs(pinned).f_flag & os.ST_RDONLY:
                raise ValueError("Pinned BOOT file filesystem is not readonly")
            readable = os.open(f"/proc/self/fd/{pinned}", os.O_RDONLY | os.O_NOATIME | os.O_CLOEXEC | os.O_NONBLOCK)
            try:
                actual = os.fstat(readable)
                if (actual.st_dev, actual.st_ino, actual.st_mode) != (original.st_dev, original.st_ino, original.st_mode):
                    raise ValueError("Pinned/reopened inode differs")
                chunks, length = [], 0
                while True:
                    chunk = os.read(readable, min(65536, max_bytes + 1 - length))
                    if not chunk: break
                    chunks.append(chunk)
                    length += len(chunk)
                    if length > max_bytes: raise ValueError("Source exceeds bound")
                if require_readonly_mount and not os.fstatvfs(readable).f_flag & os.ST_RDONLY:
                    raise ValueError("BOOT filesystem changed readonly status")
                return b"".join(chunks)
            finally:
                os.close(readable)
        finally:
            os.close(pinned)


def cli():
    print("OFFLINE_PROPOSAL_ONLY: no board collection enabled", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(cli())
