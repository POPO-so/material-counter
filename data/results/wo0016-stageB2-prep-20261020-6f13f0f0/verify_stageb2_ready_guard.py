"""Exercise the board runner's read-only READY Guard validator only."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BOOT_ID = "6f13f0f0-4433-445f-a023-fa403685aeee"
MANIFEST_SHA256 = "c5cb8ebd7bf50e8025d46d798ecf9aedb9cd043df5e16b34de294762dfb3b551"
BIT_SHA256 = "a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420"
HWH_SHA256 = "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6"
ROOT = Path("/home/xilinx/board-runtime-currentboot-v2/scripts")
sys.path.insert(0, str(ROOT / "wo0015"))
sys.path.insert(0, str(ROOT / "wo0016"))
import run_stream_counter as runner

guard_path = Path(f"/var/lib/wo0016/stageb2-ready-{BOOT_ID}.json")
attempt_path = Path(f"/var/lib/wo0016/stageb2-attempt-{BOOT_ID}-consumed.json")
raw_before = guard_path.read_bytes()
before_cma = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                      if line.startswith("CmaFree:")))
identity = runner.stageb2_guard_identity(
    boot_id=BOOT_ID,
    manifest_sha256=MANIFEST_SHA256,
    bit_sha256=BIT_SHA256,
    hwh_sha256=HWH_SHA256,
    max_frames=165,
    expected_count=5,
    material_label="M6_nut_no_washer",
    stabilization_delay_s=3.0,
)
validated = runner.validate_non_consuming_guard(guard_path, identity)
raw_after = guard_path.read_bytes()
after_cma = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                     if line.startswith("CmaFree:")))
if raw_before != raw_after or validated["sha256"] != hashlib.sha256(raw_before).hexdigest():
    raise SystemExit("STOP: read-only Guard validation changed its file")
if validated["guard"]["consumed"] is not False:
    raise SystemExit("STOP: READY Guard must remain unconsumed")
if attempt_path.exists() or attempt_path.is_symlink():
    raise SystemExit("STOP: attempt marker unexpectedly exists before physical-ready")
if min(before_cma, after_cma) < 32768:
    raise SystemExit(f"STOP: CmaFree dipped below gate: {before_cma}->{after_cma} KiB")
print(json.dumps({"status": "READY_GUARD_READ_ONLY_VALIDATED",
                  "guard_path": str(guard_path), "sha256": validated["sha256"],
                  "consumed": False, "attempt_marker_exists": False,
                  "CmaFree_before_kib": before_cma,
                  "CmaFree_after_kib": after_cma}, sort_keys=True))
