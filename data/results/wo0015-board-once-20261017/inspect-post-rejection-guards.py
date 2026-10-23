import datetime
import json
import pathlib
import subprocess

RESULT = pathlib.Path(__file__).parent / "post-rejection-guard-inspection.json"
REMOTE = r'''import hashlib, json, pathlib
base = pathlib.Path("/var/lib/wo0015-s2mm")
entries = []
for p in sorted(base.iterdir(), key=lambda x:x.name):
    item = {"name":p.name,"is_dir":p.is_dir(),"is_file":p.is_file()}
    if p.is_file():
        data = p.read_bytes()
        item["sha256"] = hashlib.sha256(data).hexdigest()
        try:
            item["json"] = json.loads(data)
        except Exception:
            item["text"] = data.decode("utf-8","replace")
    entries.append(item)
print(json.dumps({"boot_id":pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip(),"entries":entries},sort_keys=True))
'''
proc = subprocess.run(
    [
        "ssh.exe",
        "-oBatchMode=yes",
        "-oConnectionAttempts=1",
        "-oConnectTimeout=10",
        "xilinx@192.168.2.99",
        "sudo -n python3 -",
    ],
    input=REMOTE.encode(),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
record = {
    "timestamp_local": datetime.datetime.now().astimezone().isoformat(),
    "returncode": proc.returncode,
    "stdout": proc.stdout.decode("utf-8", "replace"),
    "stderr": proc.stderr.decode("utf-8", "replace"),
    "password_prompted_or_supplied": False,
}
RESULT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(record["stdout"], end="")
if record["stderr"]:
    print(record["stderr"])
raise SystemExit(proc.returncode)
