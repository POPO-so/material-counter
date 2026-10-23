from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

IMAGE = Path(r"C:\Users\POP\Desktop\FPGA赛\pynq_z2_v3.1.1\pynq_z2_v3.1.1.img")
EXPECTED_SIZE = 8_337_309_696
EXPECTED_SHA256 = "4a6ac1cdbb413d768fcb059b7182e1f53aa7f728b898b7f5061cbb0b7cc93aab"
OUTPUT = Path(__file__).with_name("image-identity.json")

before = IMAGE.stat()
digest = hashlib.sha256()
with IMAGE.open("rb") as stream:
    while chunk := stream.read(8 * 1024 * 1024):
        digest.update(chunk)
after = IMAGE.stat()
stable = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
)
actual = digest.hexdigest()
result = {
    "classification": "VERIFIED_LOCAL_IMAGE_IDENTITY",
    "path": str(IMAGE),
    "size_bytes": after.st_size,
    "expected_size_bytes": EXPECTED_SIZE,
    "sha256": actual,
    "expected_sha256": EXPECTED_SHA256,
    "size_matches": after.st_size == EXPECTED_SIZE,
    "sha256_matches": actual == EXPECTED_SHA256,
    "source_metadata_stable_during_read": stable,
    "read_mode": "regular host file opened rb; opaque sequential SHA-256 only; no mount; no extraction; no image code execution",
    "content_scope": "The digest covers the complete image byte stream. No credential-named image member was parsed, displayed, or extracted.",
}
OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, indent=2))
if not result["size_matches"] or not result["sha256_matches"] or not stable:
    raise SystemExit(1)