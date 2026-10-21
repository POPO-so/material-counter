"""Validate HUD HTML and one MJPEG part without changing board state."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path

guard = Path("/var/lib/wo0016/stageb2-ready-6f13f0f0-4433-445f-a023-fa403685aeee.json")
guard_before = guard.read_bytes()
cma_before = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                      if line.startswith("CmaFree:")))
with urllib.request.urlopen("http://127.0.0.1:8080/", timeout=4) as response:
    root_status = response.status
    root_type = response.headers.get("Content-Type", "")
    root_body = response.read(4096)
if root_status != 200 or b"<html" not in root_body.lower():
    raise SystemExit("STOP: HUD root endpoint did not return an HTML page")

request = urllib.request.Request("http://127.0.0.1:8080/stream.mjpg",
                                 headers={"Connection": "close"})
response = urllib.request.urlopen(request, timeout=4)
stream_type = response.headers.get("Content-Type", "")
if response.status != 200 or "multipart/x-mixed-replace" not in stream_type:
    response.close()
    raise SystemExit("STOP: HUD stream endpoint did not return multipart MJPEG")
data = bytearray()
deadline = time.monotonic() + 4.0
markers_found = False
try:
    while len(data) < 1_000_000 and time.monotonic() < deadline:
        chunk = response.read(8192)
        if not chunk:
            break
        data.extend(chunk)
        start = data.find(b"\xff\xd8")
        if start >= 0 and data.find(b"\xff\xd9", start + 2) >= 0:
            markers_found = True
            break
finally:
    response.close()
if not markers_found:
    raise SystemExit("STOP: no complete JPEG part arrived from HUD")

cma_after = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                     if line.startswith("CmaFree:")))
guard_after = guard.read_bytes()
if guard_after != guard_before:
    raise SystemExit("STOP: HUD probe changed READY Guard")
if cma_after < 32768:
    raise SystemExit(f"STOP: HUD operation left CmaFree below gate: {cma_after} KiB")
print(json.dumps({"http_root_status": root_status, "http_root_type": root_type,
                  "root_has_html": True, "mjpeg_status": response.status,
                  "mjpeg_content_type": stream_type,
                  "jpeg_part_complete": markers_found, "jpeg_bytes_observed": len(data),
                  "CmaFree_before_kib": cma_before, "CmaFree_after_kib": cma_after,
                  "ready_guard_sha256": hashlib.sha256(guard_after).hexdigest(),
                  "ready_guard_unchanged": True}, sort_keys=True))
