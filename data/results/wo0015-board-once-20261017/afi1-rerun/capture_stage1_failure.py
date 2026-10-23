from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess


RESULT = pathlib.Path(__file__).resolve().parent
BOARD = "xilinx@192.168.2.99"
BOOT_ID = "2cc2ab91-2e3f-450e-b9e7-361ec38e2815"
SSH = ["ssh.exe", "-oBatchMode=yes", "-oConnectionAttempts=1", "-oConnectTimeout=10", BOARD]
REMOTE_PYTHON = "/usr/local/share/pynq-venv/bin/python3"


def main() -> None:
    source = f"""
import hashlib, json, pathlib, sys
sys.path.insert(0, '/home/xilinx/wo0015')
import board_once
root = pathlib.Path('/var/lib/wo0015-s2mm')
boot = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
expected = {BOOT_ID!r}
if boot != expected: raise SystemExit('boot_id_mismatch=' + boot)
files = {{}}
for name in ('overlay-only-consumed.json','terminal-failure.json'):
    path = root / name
    if path.is_file():
        raw = path.read_bytes()
        files[name] = {{'sha256':hashlib.sha256(raw).hexdigest(),'hex':raw.hex()}}
regs = {{name:board_once.read_phys32_readonly(address)
        for name,address in board_once.SNAPSHOT_REGISTERS.items()}}
print(json.dumps({{'boot_id':boot,'registers':regs,'files':files,
                  'active_guard_files':sorted(p.name for p in root.iterdir() if p.is_file())}},sort_keys=True))
"""
    proc = subprocess.run(
        SSH + [f"sudo -n {REMOTE_PYTHON} -"], input=source.encode(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode:
        raise SystemExit(f"read-only board evidence capture failed: {proc.stderr.decode('utf-8','replace')}")
    evidence = json.loads(proc.stdout.decode("utf-8"))
    if evidence.get("boot_id") != BOOT_ID:
        raise SystemExit("read-only capture returned wrong boot id")
    for name, item in evidence["files"].items():
        raw = bytes.fromhex(item.pop("hex"))
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise SystemExit(f"guard file SHA-256 mismatch: {name}")
        (RESULT / name).write_bytes(raw)
    evidence["password_prompted_or_supplied"] = False
    (RESULT / "post-stage1-readonly-evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
