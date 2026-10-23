import datetime
import json
import pathlib
import subprocess
import sys

RESULT = pathlib.Path(__file__).parent
BOOT_ID = "2cc2ab91-2e3f-450e-b9e7-361ec38e2815"
MANIFEST_SHA256 = "2205c49479ffb8dae66aa7e34864aaa6783186c2dd1f96d8da9caeb9a24ecad0"
REMOTE_RESULT = "/home/xilinx/wo0015/overlay-only-result.json"
LOCAL_RESULT = RESULT / "overlay-only-result.json"
COMMAND = (
    "sudo -n env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u /home/xilinx/wo0015/board_once.py "
    "--mode overlay-only --manifest /home/xilinx/wo0015/wo0015-manifest.json "
    f"--manifest-sha256 {MANIFEST_SHA256} --execute --authorize-stage overlay-only "
    f"--approve-once --timeout-s 5 --evidence-json {REMOTE_RESULT}"
)
ssh_args = [
    "ssh.exe",
    "-oBatchMode=yes",
    "-oConnectionAttempts=1",
    "-oConnectTimeout=10",
    "xilinx@192.168.2.99",
    COMMAND,
]
proc = subprocess.run(ssh_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
(RESULT / "overlay-only-stdout.txt").write_bytes(proc.stdout)
(RESULT / "overlay-only-stderr.txt").write_bytes(proc.stderr)
steps = [
    {
        "step": "execute overlay-only once",
        "args": ssh_args,
        "returncode": proc.returncode,
        "stdout_file": "overlay-only-stdout.txt",
        "stderr_file": "overlay-only-stderr.txt",
        "password_prompted_or_supplied": False,
    }
]
copy = None
try:
    scp_args = [
        "scp.exe",
        "-q",
        "-oBatchMode=yes",
        "-oConnectionAttempts=1",
        "-oConnectTimeout=10",
        f"xilinx@192.168.2.99:{REMOTE_RESULT}",
        str(LOCAL_RESULT),
    ]
    copy = subprocess.run(scp_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    steps.append(
        {
            "step": "retrieve overlay-only evidence",
            "args": scp_args,
            "returncode": copy.returncode,
            "stdout": copy.stdout.decode("utf-8", "replace"),
            "stderr": copy.stderr.decode("utf-8", "replace"),
        }
    )
except Exception as exc:
    steps.append({"step": "retrieve overlay-only evidence", "error": str(exc)})

result = {
    "timestamp_local": datetime.datetime.now().astimezone().isoformat(),
    "boot_id": BOOT_ID,
    "manifest_sha256": MANIFEST_SHA256,
    "command": COMMAND,
    "steps": steps,
    "evidence_retrieved": LOCAL_RESULT.exists(),
}
(RESULT / "overlay-only-execution-controller.json").write_text(
    json.dumps(result, indent=2) + "\n", encoding="utf-8"
)
print(f"OVERLAY_ONLY_SSH_EXIT={proc.returncode}")
print(f"OVERLAY_ONLY_EVIDENCE_RETRIEVED={LOCAL_RESULT.exists()}")
if proc.stdout:
    print(proc.stdout.decode("utf-8", "replace"), end="")
if proc.stderr:
    print(proc.stderr.decode("utf-8", "replace"), file=sys.stderr, end="")
if proc.returncode:
    raise SystemExit(proc.returncode)
if copy is not None and copy.returncode:
    raise SystemExit(copy.returncode)
if not LOCAL_RESULT.is_file():
    raise SystemExit("overlay-only result JSON missing")
