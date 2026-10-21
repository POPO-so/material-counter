"""Archive the consumed Phase 1.10 software-abort guard without resetting it."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


BOOT = "58e6af6c-6660-4d44-b0c5-e18f4a564202"
ROOT = Path("/var/lib/wo0016")
SOURCE = ROOT / "stream-counter-consumed.json"
ARCHIVE = ROOT / "phase110-prehardware-sw-abort-20261019"
DESTINATION = ARCHIVE / SOURCE.name
MARKER = ARCHIVE / "classification.json"
EXPECTED_SHA256 = "a9b8d46efd69e341ba915a4e3352211413e284985605795c975e5c0ee0d753b8"
MANIFEST_SHA256 = "e07316b9398166c6fc7a459faaca271eaf32170c18b4deaa4c16208bcaa933f2"


def main() -> None:
    assert os.geteuid() == 0
    assert Path("/proc/sys/kernel/random/boot_id").read_text().strip() == BOOT
    assert ROOT.is_dir() and not ROOT.is_symlink()
    assert SOURCE.is_file() and not SOURCE.is_symlink()
    assert not ARCHIVE.exists() and not ARCHIVE.is_symlink()
    raw = SOURCE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == EXPECTED_SHA256
    guard = json.loads(raw)
    assert guard["boot_id"] == BOOT
    assert guard["manifest_sha256"] == MANIFEST_SHA256
    assert guard["max_frames"] == 3 and guard["consumed"] is True
    assert guard["stage"] == "wo0016-stream"
    ARCHIVE.mkdir(mode=0o700)
    os.rename(SOURCE, DESTINATION)
    marker = {
        "classification": "PRE_HARDWARE_SW_ABORT",
        "reason": "Prior Python lazy-load timer wrapper AttributeError before hardware action; operator-authorized classification",
        "boot_id": BOOT,
        "guard_sha256": digest,
        "guard_path": str(DESTINATION),
    }
    fd = os.open(MARKER, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(marker, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    assert hashlib.sha256(DESTINATION.read_bytes()).hexdigest() == digest
    print(json.dumps(marker, sort_keys=True))


if __name__ == "__main__":
    main()
