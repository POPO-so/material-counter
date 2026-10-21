"""Safely archive only verified consumed records from the prior boots."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

CURRENT_BOOT = "6f13f0f0-4433-445f-a023-fa403685aeee"
STAGE1_BOOT = "f8b24aee-ed7e-430a-b3f3-d26056746bfd"
STAGEB1_BOOT = "58e6af6c-6660-4d44-b0c5-e18f4a564202"
records = [
    (Path("/var/lib/wo0015-s2mm/overlay-only-consumed.json"), "WO-0015", STAGE1_BOOT, "consumed"),
    (Path("/var/lib/wo0015-s2mm/overlay-only-pass.json"), "WO-0015", STAGE1_BOOT, "receipt"),
]
records += [
    (p, "WO-0016", STAGEB1_BOOT, "consumed")
    for p in sorted(Path("/var/lib/wo0016").glob("*.json"))
]
if len(records) != 7:
    raise SystemExit(f"STOP: expected exactly 7 prior records, found {len(records)}")
for path, work_order, old_boot, kind in records:
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"STOP: expected a regular source file: {path}")
    st = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0:
        raise SystemExit(f"STOP: source is not a root-owned regular file: {path}")
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get("work_order") != work_order or data.get("boot_id") != old_boot:
        raise SystemExit(f"STOP: unexpected work order or boot identity: {path}")
    if data.get("boot_id") == CURRENT_BOOT:
        raise SystemExit(f"STOP: refusing to archive a current-boot record: {path}")
    if kind == "consumed" and data.get("consumed") is not True:
        raise SystemExit(f"STOP: record is not consumed: {path}")
    if kind == "receipt" and data.get("status") != "OVERLAY_LOAD_PASS":
        raise SystemExit(f"STOP: Stage 1 receipt is not a PASS: {path}")

    history = path.parent / "history" / f"boot-{old_boot}"
    history.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(history, 0o700)
    target = history / path.name
    fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(raw).digest():
            raise SystemExit(f"STOP: archived copy hash mismatch: {target}")
        os.unlink(path)
        for directory in (path.parent, history):
            dirfd = os.open(str(directory), os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
    except BaseException:
        # Keep any successfully written archive for audit; do not remove it.
        raise
    print(json.dumps({"source": str(path), "archived": str(target),
                      "boot_id": old_boot, "sha256": hashlib.sha256(raw).hexdigest()},
                     sort_keys=True))
