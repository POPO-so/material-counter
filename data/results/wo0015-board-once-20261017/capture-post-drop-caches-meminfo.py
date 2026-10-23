import datetime
import json
import pathlib
import re
import subprocess
import sys

RESULT = pathlib.Path(__file__).parent
proc = subprocess.run(
    [
        "ssh.exe",
        "-oBatchMode=yes",
        "-oConnectionAttempts=1",
        "-oConnectTimeout=10",
        "xilinx@192.168.2.99",
        "cat /proc/sys/kernel/random/boot_id && cat /proc/meminfo",
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
stdout = proc.stdout.decode("utf-8", "replace")
(RESULT / "post-drop-caches-meminfo.txt").write_text(stdout, encoding="utf-8")
lines = stdout.splitlines()
boot_id = lines[0].strip() if lines else None
values = {}
for line in lines[1:]:
    match = re.match(r"^(CmaTotal|CmaFree):\s+(\d+)\s+kB$", line)
    if match:
        values[match.group(1)] = int(match.group(2))
if proc.returncode == 0 and boot_id == "2cc2ab91-2e3f-450e-b9e7-361ec38e2815" and set(values) == {"CmaTotal", "CmaFree"}:
    branch = "A" if values["CmaTotal"] == 131072 and values["CmaFree"] >= 32768 else "B"
else:
    branch = "UNKNOWN_OR_BOOT_MISMATCH"
record = {
    "timestamp_local": datetime.datetime.now().astimezone().isoformat(),
    "boot_id": boot_id,
    "meminfo": values,
    "branch": branch,
    "returncode": proc.returncode,
    "stderr": proc.stderr.decode("utf-8", "replace"),
    "full_meminfo_file": "post-drop-caches-meminfo.txt",
    "password_prompted_or_supplied": False,
}
(RESULT / "post-drop-caches-check.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record, indent=2))
if proc.returncode:
    raise SystemExit(proc.returncode)
