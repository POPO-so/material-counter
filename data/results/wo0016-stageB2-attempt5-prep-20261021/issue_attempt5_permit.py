#!/usr/bin/env python3
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys

attempt_id = sys.argv[1]
run_dir = Path(sys.argv[2])
boot_id = "d9f65950-d59b-4ea4-bee4-b28656cb176f"
wo = Path("/var/lib/wo0016")
ready_path = wo / f"stageb2-ready-{boot_id}.json"
stage1_path = run_dir / f"stage1-overlay-only-result-{boot_id[:8]}.json"
manifest_path = run_dir / "wo0015" / "wo0015-manifest.json"
runner_path = run_dir / "run_stream_counter.py"
counter_path = run_dir / "stream_counter.py"
bit_path = Path("/home/xilinx/wo0015/wo15.bit")
hwh_path = Path("/home/xilinx/wo0015/wo15.hwh")
source_dir = wo / "attempts" / boot_id / "attempt-5-recovery-2"
source_permit_path = source_dir / "attempt-permit.json"
source_marker_path = source_dir / "consumed.json"
target_dir = wo / "attempts" / boot_id / attempt_id
target_permit_path = target_dir / "attempt-permit.json"

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

if attempt_id not in {"attempt-5-trial-2", "attempt-5-trial-3"}:
    raise SystemExit("refusing unapproved attempt id")
if target_dir.exists():
    raise SystemExit(f"attempt directory already exists: {target_dir}")
if not source_marker_path.is_file():
    raise SystemExit("recovery-2 consumed marker is missing; refusing to clone permit")
if not all(path.is_file() for path in (ready_path, stage1_path, manifest_path, runner_path,
                                        counter_path, bit_path, hwh_path, source_permit_path)):
    raise SystemExit("a required boot-bound source record or deployment file is missing")

sys.path.insert(0, str(run_dir))
import run_stream_counter as runner

stage1 = json.loads(stage1_path.read_text(encoding="utf-8"))
if (stage1.get("status") != "OVERLAY_LOAD_PASS"
        or stage1.get("boot_id") != boot_id
        or stage1.get("load_performed") is not True
        or stage1.get("checker_signature") != 0x574F3134
        or stage1.get("packer_signature") != 0x574F3135
        or stage1.get("post_stage_audit", {}).get("fpga_manager") != {
            "flags": "0", "state": "operating"}):
    raise SystemExit("Stage 1 receipt does not pass the current-boot/signature/manager gate")
if (Path("/sys/class/fpga_manager/fpga0/state").read_text().strip() != "operating"
        or Path("/sys/class/fpga_manager/fpga0/flags").read_text().strip() != "0"):
    raise SystemExit("live FPGA Manager is not operating with flags=0")
if (sha(bit_path) != "a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420"
        or sha(hwh_path) != "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6"):
    raise SystemExit("persistent BIT/HWH hash mismatch")

manifest_sha = sha(manifest_path)
runner_sha = sha(runner_path)
counter_sha = sha(counter_path)
identity = runner.stageb2_guard_identity(
    boot_id=boot_id,
    manifest_sha256=manifest_sha,
    bit_sha256=sha(bit_path),
    hwh_sha256=sha(hwh_path),
    max_frames=runner.STAGEB2_VALID_FRAMES,
    expected_count=runner.STAGEB2_EXPECTED_COUNT,
    material_label=runner.STAGEB2_MATERIAL_LABEL,
    stabilization_delay_s=runner.STAGEB2_STABILIZATION_DELAY_S,
)
ready = runner.validate_non_consuming_guard(ready_path, identity, expected_uid=0)
source, _ = runner._read_private_json_record(
    source_permit_path, label="recovery-2 permit source", expected_uid=0
)
if (source.get("attempt_id") != "attempt-5-recovery-2"
        or source.get("permit_status") != "AUTHORIZED"
        or source.get("consumed") is not False
        or source.get("ready_guard_sha256") != ready["sha256"]
        or source.get("runner_sha256") != runner_sha
        or source.get("counter_sha256") != counter_sha
        or source.get("manifest_sha256") != manifest_sha
        or source.get("max_frames") != 165
        or source.get("expected_count") != 5
        or source.get("threshold") != 128
        or source.get("stabilization_delay_s") != 3.0):
    raise SystemExit("recovery-2 source permit does not bind the current runner/configuration")
permit_identity = runner.stageb2_attempt_permit_identity(
    ready_identity=identity,
    ready_guard_sha256=ready["sha256"],
    attempt_id=attempt_id,
    operator_prearm_delay_s=runner.STAGEB2_OPERATOR_PREARM_DELAY_S,
    runner_sha256=runner_sha,
    counting_config=source["counting_config"],
    counter_sha256=counter_sha,
)
permit = {**permit_identity, "permit_status": "AUTHORIZED", "consumed": False}
target_dir.mkdir(mode=0o700)
fd = os.open(target_permit_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
    json.dump(permit, stream, ensure_ascii=True, indent=2, sort_keys=True)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
os.chmod(target_permit_path, 0o600)
verified = runner.validate_stageb2_attempt_permit(
    target_permit_path, permit_identity, expected_uid=0
)
print(json.dumps({
    "attempt_id": attempt_id,
    "permit_path": str(target_permit_path),
    "permit_sha256": verified["sha256"],
    "ready_guard_sha256": ready["sha256"],
    "runner_sha256": runner_sha,
    "counter_sha256": counter_sha,
    "manifest_sha256": manifest_sha,
    "cma_gate": "not assessed by permit issuer",
}, sort_keys=True))
