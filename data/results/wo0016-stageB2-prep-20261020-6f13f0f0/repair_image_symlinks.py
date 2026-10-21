"""Repair only the two verified dangling WO-0015 image links."""
from pathlib import Path
import hashlib
import json
import os

pairs = [
    (Path("/home/xilinx/wo0016-phase110-58e6af6c-6660-4d44-b0c5-e18f4a564202/wo0015/wo15.bit"),
     Path("/home/xilinx/wo0015/wo15.bit"),
     "/tmp/wo0016-phase16-20261019-130149/board-runtime-final/wo0015/wo15.bit",
     "a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420"),
    (Path("/home/xilinx/wo0016-phase110-58e6af6c-6660-4d44-b0c5-e18f4a564202/wo0015/wo15.hwh"),
     Path("/home/xilinx/wo0015/wo15.hwh"),
     "/tmp/wo0016-phase16-20261019-130149/board-runtime-final/wo0015/wo15.hwh",
     "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6"),
]
result = []
for link, target, old_target, expected_sha256 in pairs:
    if not link.is_symlink() or str(link.readlink()) != old_target:
        raise SystemExit(f"STOP: unexpected link state: {link}")
    if not target.is_file():
        raise SystemExit(f"STOP: persistent target is missing: {target}")
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise SystemExit(f"STOP: persistent target SHA-256 differs: {target}: {actual}")
    link.unlink()
    link.symlink_to(target)
    result.append({"link": str(link), "target": str(target), "sha256": actual,
                   "is_file": link.is_file(), "is_symlink": link.is_symlink()})
for parent in {link.parent for link, *_ in pairs}:
    fd = os.open(str(parent), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
print(json.dumps(result, indent=2))
