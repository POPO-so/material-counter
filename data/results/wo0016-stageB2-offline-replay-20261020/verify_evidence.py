"""Offline integrity and replay verification; no board access."""
import hashlib
import json
from pathlib import Path
import sys

here = Path(__file__).resolve().parent
repo = here.parents[2]
sys.path.insert(0,str(repo/"scripts/wo0016"))
import replay_recorded_components as replay_module
import run_stream_counter as runner
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
boot = "6f13f0f0-4433-445f-a023-fa403685aeee"
board = here/"board-evidence"
history = board/"var/lib/wo0016/history"/("boot-"+boot)
archive_hashes = {}
for number in (2,3):
    archive = history/f"attempt-{number}"
    manifest = json.loads((archive/"archive-manifest.json").read_text())
    for relative, digest in manifest["files"].items():
        assert sha(archive/relative) == digest, relative
    archive_hashes[str(number)] = sha(archive/"archive-manifest.json")
payload = json.loads((here/"preparation-payload.json").read_text())
for relative,digest in payload["new_source_hashes"].items():
    assert sha(repo/relative) == digest
ready = board/"var/lib/wo0016"/("stageb2-ready-"+boot+".json")
assert sha(ready) == payload["ready_guard_sha256"]
assert json.loads(ready.read_text())["consumed"] is False
attempt4 = board/"var/lib/wo0016/attempts"/boot/"attempt-4"
permit = json.loads((attempt4/"attempt-permit.json").read_text())
assert permit == payload["permit"]
assert not (attempt4/"consumed.json").exists()
args = runner._parser().parse_args(payload["prepared_command"][payload["prepared_command"].index("--")+1:])
ready_identity = {k:v for k,v in json.loads(ready.read_text()).items() if k not in ("guard_status","consumed")}
expected = runner.stageb2_attempt_permit_identity(ready_identity=ready_identity,
    ready_guard_sha256=sha(ready), attempt_id=args.attempt_id,
    operator_prearm_delay_s=args.operator_prearm_delay_s,
    runner_sha256=sha(repo/"scripts/wo0016/run_stream_counter.py"),
    counter_sha256=sha(repo/"scripts/wo0016/stream_counter.py"),
    counting_config=runner.counting_configuration(args))
assert permit == {**expected,"permit_status":"AUTHORIZED","consumed":False}
raw = history/"attempt-3/attempt-records/frame-results.json"
assert sha(raw) == payload["frame_records_sha256"]
saved = json.loads((here/"replay.json").read_text())
recomputed = replay_module.replay(json.loads(raw.read_text()),saved["configuration"])
for key,value in recomputed.items():
    assert saved[key] == value, key
assert recomputed["replayed_total_count"] == 5
summary = {"status":"OFFLINE_INTEGRITY_AND_REPLAY_PASS", "board_accessed":False,
           "verified_archive_hashes":archive_hashes,
           "permit_sha256":sha(attempt4/"attempt-permit.json"),
           "ready_guard_sha256":sha(ready),"replayed_total_count":5,
           "physical_identity":"UNVERIFIED_FROM_CCL_ONLY", "attempt4_sampling":"NOT_RUN"}
(here/"verification.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
print(json.dumps(summary))
