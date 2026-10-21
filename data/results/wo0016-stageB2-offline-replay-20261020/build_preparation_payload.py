"""Build exact Attempt4 permit and reviewed command offline (no execution)."""
import hashlib
import json
from pathlib import Path
import sys

repo = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo/"scripts/wo0016"))
import run_stream_counter as runner

out = Path(__file__).resolve().parent
boot = "6f13f0f0-4433-445f-a023-fa403685aeee"
runtime = "/home/xilinx/board-runtime-currentboot-v2"
original = repo/"data/results/wo0016-stageB2-attempt3-20261020-6f13f0f0"
guard = original/"var/lib/wo0016"/("stageb2-ready-"+boot+".json")
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
ready = json.loads(guard.read_text())
identity = {k:v for k,v in ready.items() if k not in ("guard_status", "consumed")}
flags = ["--mode","board","--execute","--authorize-stage","wo0016-stream","--approve-once",
         "--no-consume-guard","--attempt-id","attempt-4","--operator-prearm-delay-s","15",
         "--stabilization-delay-s","3","--max-frames","165","--expected-count","5",
         "--material-label","M6_nut_no_washer","--camera","/dev/video0",
         "--manifest",runtime+"/scripts/wo0015/run-manifest.json",
         "--manifest-sha256",ready["manifest_sha256"],
         "--evidence-json",runtime+"/stageb2-attempt-4-result.json",
         "--count-axis","x","--line-x","160","--line-y","120","--direction","1",
         "--max-step","60","--max-lateral-step","10","--min-area","12"]
args = runner._parser().parse_args(flags)
source_hashes = {"scripts/wo0016/"+name:sha(repo/"scripts/wo0016"/name)
                 for name in ("run_stream_counter.py","stream_counter.py","mjpeg_preview.py")}
permit = runner.stageb2_attempt_permit_identity(ready_identity=identity,
    ready_guard_sha256=sha(guard), attempt_id="attempt-4",operator_prearm_delay_s=15,
    runner_sha256=source_hashes["scripts/wo0016/run_stream_counter.py"],
    counter_sha256=source_hashes["scripts/wo0016/stream_counter.py"],
    counting_config=runner.counting_configuration(args))
payload = {"boot_id":boot, "ready_guard_sha256":sha(guard),
    "attempt2_archive_sha256":"6e7bd38d6fc9dc57a0d88500a77d754ef593e7c667e7fb735ad09aaef263a068",
    "attempt3_result_sha256":"7e4e24afb36fb78716e1bed12e8273c807fa33ad42feef1b8c066a2c773f3471",
    "frame_records_sha256":"bcacadae888ec5166bea40b00bcd39db6857a0dc61e1143cdee1bfa0821b77dd",
    "new_source_hashes":source_hashes,
    "permit":{**permit,"permit_status":"AUTHORIZED","consumed":False},
    "prepared_command":["/usr/local/share/pynq-venv/bin/python","-u",
        runtime+"/scripts/wo0016/operator_gate_launcher.py","--attempt-dir",
        "/var/lib/wo0016/attempts/"+boot+"/attempt-4","--control-port","8081",
        "--handoff-timeout-s","300","--",*flags]}
(out/"preparation-payload.json").write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
print(json.dumps(source_hashes))
