"""Create the one current-boot Stage B-2 READY record without an attempt claim."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

BOOT_ID = "6f13f0f0-4433-445f-a023-fa403685aeee"
ROOT = Path("/home/xilinx/board-runtime-currentboot-v2/scripts/wo0015")
MANIFEST_SHA256 = "c5cb8ebd7bf50e8025d46d798ecf9aedb9cd043df5e16b34de294762dfb3b551"
BIT_SHA256 = "a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420"
HWH_SHA256 = "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6"
manifest_path = ROOT / "run-manifest.json"
manifest_raw = manifest_path.read_bytes()
manifest = json.loads(manifest_raw)
if hashlib.sha256(manifest_raw).hexdigest() != MANIFEST_SHA256:
    raise SystemExit("STOP: current manifest digest differs")
if manifest.get("boot_id") != BOOT_ID:
    raise SystemExit("STOP: manifest boot identity differs")
if hashlib.sha256((ROOT / "wo15.bit").read_bytes()).hexdigest() != BIT_SHA256:
    raise SystemExit("STOP: BIT digest differs")
if hashlib.sha256((ROOT / "wo15.hwh").read_bytes()).hexdigest() != HWH_SHA256:
    raise SystemExit("STOP: HWH digest differs")
if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != BOOT_ID:
    raise SystemExit("STOP: live boot identity differs")
if Path("/sys/class/fpga_manager/fpga0/state").read_text().strip() != "operating":
    raise SystemExit("STOP: FPGA Manager is not operating")
if Path("/sys/class/fpga_manager/fpga0/flags").read_text().strip() != "0":
    raise SystemExit("STOP: FPGA Manager flags are nonzero")
cma_free = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                    if line.startswith("CmaFree:")))
if cma_free < 32768:
    raise SystemExit(f"STOP: CmaFree below gate: {cma_free} KiB")
receipt_path = Path("/var/lib/wo0015-s2mm/overlay-only-pass.json")
receipt = json.loads(receipt_path.read_bytes())
for key, value in {
    "work_order": "WO-0015", "stage": "overlay-only", "status": "OVERLAY_LOAD_PASS",
    "load_performed": True, "boot_id": BOOT_ID,
    "manifest_sha256": MANIFEST_SHA256, "bit_sha256": BIT_SHA256,
    "hwh_sha256": HWH_SHA256, "checker_signature": 0x574F3134,
    "packer_signature": 0x574F3135,
}.items():
    if receipt.get(key) != value:
        raise SystemExit(f"STOP: current Stage 1 receipt mismatch at {key}")

guard_dir = Path("/var/lib/wo0016")
if guard_dir.is_symlink() or not guard_dir.is_dir():
    raise SystemExit("STOP: WO-0016 guard directory missing or symlinked")
dir_stat = guard_dir.stat(follow_symlinks=False)
if dir_stat.st_uid != 0:
    raise SystemExit("STOP: WO-0016 guard directory is not root-owned")
os.chmod(guard_dir, 0o700)
if stat.S_IMODE(guard_dir.stat().st_mode) != 0o700:
    raise SystemExit("STOP: could not set WO-0016 guard directory mode 0700")

identity = {
    "schema": 1,
    "work_order": "WO-0016",
    "stage": "wo0016-stageB2-nuts",
    "boot_id": BOOT_ID,
    "manifest_sha256": MANIFEST_SHA256,
    "bit_sha256": BIT_SHA256,
    "hwh_sha256": HWH_SHA256,
    "max_frames": 165,
    "expected_count": 5,
    "material_label": "M6_nut_no_washer",
    "stabilization_delay_s": 3.0,
    "threshold": 128,
    "guard_status": "READY",
    "consumed": False,
}
target = guard_dir / f"stageb2-ready-{BOOT_ID}.json"
attempt = guard_dir / f"stageb2-attempt-{BOOT_ID}-consumed.json"
if attempt.exists() or attempt.is_symlink():
    raise SystemExit("STOP: current-boot one-shot attempt marker already exists")
raw = (json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n").encode()
fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "wb") as stream:
    stream.write(raw)
    stream.flush()
    os.fsync(stream.fileno())
dir_fd = os.open(str(guard_dir), os.O_RDONLY)
try:
    os.fsync(dir_fd)
finally:
    os.close(dir_fd)
written = target.read_bytes()
if written != raw:
    raise SystemExit("STOP: READY Guard readback differs")
st = target.stat(follow_symlinks=False)
if st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o600 or not stat.S_ISREG(st.st_mode):
    raise SystemExit("STOP: READY Guard ownership/mode/type is invalid")
print(json.dumps({"ready_guard": str(target), "sha256": hashlib.sha256(raw).hexdigest(),
                  "guard": identity, "CmaFree_kib": cma_free,
                  "attempt_marker_created": False}, sort_keys=True))
