"""Persist a full host-only image hash; the Worker hash had no saved log."""
import datetime
import hashlib
import json
from pathlib import Path

image = Path("C:/Users/POP/Desktop/FPGA赛/pynq_z2_v3.1.1/pynq_z2_v3.1.1.img")
before = image.stat()
with image.open("rb") as src: digest = hashlib.file_digest(src, "sha256").hexdigest()
after = image.stat()
assert (before.st_size, before.st_mtime_ns, before.st_ino) == (after.st_size, after.st_mtime_ns, after.st_ino)
assert digest == "4a6ac1cdbb413d768fcb059b7182e1f53aa7f728b898b7f5061cbb0b7cc93aab"
result = {"source": str(image), "sha256": digest, "nbytes": before.st_size, "creation_utc": datetime.datetime.fromtimestamp(before.st_birthtime, datetime.timezone.utc).isoformat(), "mtime_utc": datetime.datetime.fromtimestamp(before.st_mtime, datetime.timezone.utc).isoformat(), "recorded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "read_mode": "rb regular host image; no mount", "verification": "Manager complete hash persisted to verify Worker-reported hash where no raw Worker log survived", "source_metadata_unchanged": True, "vendor_checksum_available": False, "flash_or_boot_binding": "UNKNOWN"}
with Path(__file__).with_name("image-sha256-verified.json").open("x", encoding="utf-8") as f: json.dump(result, f, ensure_ascii=False, indent=2); f.write("\n")
print(json.dumps({"sha256": digest, "bytes": before.st_size, "matched_worker_hash": True}))
