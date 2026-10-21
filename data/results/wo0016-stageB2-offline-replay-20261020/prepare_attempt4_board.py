"""Explicitly authorized archive/deploy/permit preparation; NEVER runs sampling.

Run under sudo python3 with one staged directory argument. Standard library only.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)

stage = Path(sys.argv[1]).resolve()
payload = json.loads((stage/"preparation-payload.json").read_text())
boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
assert boot == payload["boot_id"], "boot changed"
root = Path("/home/xilinx/board-runtime-currentboot-v2")
state = Path("/var/lib/wo0016")
ready = state/("stageb2-ready-"+boot+".json")
assert sha(ready) == payload["ready_guard_sha256"]
assert json.loads(ready.read_text())["consumed"] is False
assert subprocess.run(["fuser", "/dev/video0", "/dev/video1", "/dev/mem"],
                      capture_output=True).returncode == 1, "device occupied"
meminfo = {line.split(":")[0]: int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("Cma")}
assert meminfo["CmaFree"] >= 32768
listeners = subprocess.check_output(["ss", "-ltnp"], text=True)
assert ":8080 " not in listeners and ":8081 " not in listeners
history = state/"history"/("boot-"+boot)
archive2 = history/"attempt-2"
assert sha(archive2/"archive-manifest.json") == payload["attempt2_archive_sha256"]
manifest2 = json.loads((archive2/"archive-manifest.json").read_text())
for name, digest in manifest2["files"].items():
    assert sha(archive2/name) == digest, "attempt2 archival hash mismatch"
attempt3 = state/"attempts"/boot/"attempt-3"
assert json.loads((attempt3/"consumed.json").read_text())["consumed"] is True
assert sha(root/"stageb2-attempt-3-result.json") == payload["attempt3_result_sha256"]
assert sha(attempt3/"frame-results.json") == payload["frame_records_sha256"]
package_path = root/"PACKAGE_SHA256.json"
package = json.loads(package_path.read_text())
for name, digest in package["files"].items():
    assert sha(root/name) == digest, "deployed package drift: "+name
for name, digest in payload["new_source_hashes"].items():
    assert sha(stage/Path(name).name) == digest
archive3 = history/"attempt-3"
attempt4 = state/"attempts"/boot/"attempt-4"
assert not archive3.exists(), "archive3 exists; inspect, do not overwrite"
assert not attempt4.exists(), "attempt4 exists; inspect, do not overwrite"

# Preserve every original result, spent marker, log and source before deployment.
archive3.mkdir(mode=0o700)
shutil.copytree(attempt3, archive3/"attempt-records")
for name in ("stageb2-attempt-3-result.json", "stageb2-attempt-3-result.milestones.jsonl", "PACKAGE_SHA256.json", "stage1-currentboot.json"):
    shutil.copy2(root/name, archive3/name)
for name in package["files"]:
    if name.startswith("scripts/wo0016/"):
        target = archive3/name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(root/name, target)
shutil.copy2(ready, archive3/ready.name)
archive_manifest = {"schema":1, "attempt_id":"attempt-3", "boot_id":boot,
    "status":"FAILED_COUNT_SOFTWARE_AXIS_ASSOCIATION_GROUPED_FEED",
    "operator_report":"Five nuts fed together; individual timing/identity unverified",
    "raw_total_count":0, "offline_replayed_total_count":5,
    "offline_replay_note":"Numerical replay only; no one-to-one physical identity proof",
    "previous_archive_manifest_sha256":sha(archive2/"archive-manifest.json"),
    "files":{str(path.relative_to(archive3)):sha(path) for path in sorted(archive3.rglob("*")) if path.is_file()}}
write_new(archive3/"archive-manifest.json", archive_manifest)
for name, digest in archive_manifest["files"].items():
    assert sha(archive3/name) == digest

for name, digest in payload["new_source_hashes"].items():
    target = root/name
    temporary = target.with_suffix(target.suffix+".attempt4-new")
    assert not temporary.exists()
    shutil.copyfile(stage/Path(name).name, temporary)
    temporary.chmod(target.stat().st_mode & 0o777)
    os.replace(temporary, target)
    assert sha(target) == digest
    package["files"][name] = digest
package_new = root/"PACKAGE_SHA256.attempt4-new.json"
write_new(package_new, package)
os.replace(package_new, package_path)
attempt4.mkdir(mode=0o700)
write_new(attempt4/"attempt-permit.json", payload["permit"])
write_new(attempt4/"preparation.json", {
    "status":"PREPARED_WAITING_EXPLICIT_USER_START", "sampling_started":False,
    "boot_id":boot, "monotonic":time.monotonic(),
    "archive2_manifest_sha256":sha(archive2/"archive-manifest.json"),
    "archive3_manifest_sha256":sha(archive3/"archive-manifest.json"),
    "ready_guard_sha256":sha(ready), "permit_sha256":sha(attempt4/"attempt-permit.json"),
    "package_sha256":sha(package_path), "source_hashes":payload["new_source_hashes"],
    "counting_config":payload["permit"]["counting_config"],
    "cma_free_kib":meminfo["CmaFree"], "device_handles":"NONE",
    "offline_tests":"92 passed", "offline_replay_total":5,
    "physical_identity_gate":"UNVERIFIED_FROM_CCL_ONLY",
    "prepared_command":payload["prepared_command"]})
assert sha(ready) == payload["ready_guard_sha256"]
assert not (attempt4/"consumed.json").exists()
print((attempt4/"preparation.json").read_text())
