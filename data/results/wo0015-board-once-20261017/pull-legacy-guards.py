import hashlib
import io
import json
import pathlib
import subprocess
import sys
import tarfile

NAMES = [
    "cma-watermark-recovery-consumed.json",
    "dma-single-consumed.json",
    "dma-single-watermark-retry2-consumed.json",
    "overlay-only-consumed.json",
    "overlay-only-pass.json",
    "run-only-init-consumed.json",
    "terminal-failure.json",
]
OLD_BOOT = "822a9c29-37c0-438c-81c4-da998d57073b"
dest = pathlib.Path(sys.argv[1])
if any(dest.iterdir()):
    raise SystemExit("destination is not empty; refusing to overwrite")

remote_cmd = "sudo -n tar -C /var/lib/wo0015-s2mm -cf - -- " + " ".join(NAMES)
proc = subprocess.run(
    [
        "ssh.exe",
        "-oBatchMode=yes",
        "-oConnectionAttempts=1",
        "-oConnectTimeout=10",
        "xilinx@192.168.2.99",
        remote_cmd,
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
if proc.returncode:
    sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
    raise SystemExit("remote tar failed; no local files written")

with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as archive:
    members = archive.getmembers()
    regular_files = sorted(m.name.removeprefix("./") for m in members if m.isfile())
    if regular_files != sorted(NAMES) or len(members) != 7 or any(not m.isfile() for m in members):
        raise SystemExit(f"unexpected tar members: {[(m.name, m.isfile()) for m in members]}")
    payloads = {}
    for name in NAMES:
        stream = archive.extractfile(name)
        if stream is None:
            raise SystemExit(f"missing tar payload: {name}")
        payloads[name] = stream.read()


def contains_old_boot(value):
    if isinstance(value, dict):
        return any(
            (key == "boot_id" and item == OLD_BOOT) or contains_old_boot(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_old_boot(item) for item in value)
    return False


for name, data in payloads.items():
    if not contains_old_boot(json.loads(data)):
        raise SystemExit(f"{name}: old boot_id not found; refusing archive")

for name, data in payloads.items():
    (dest / name).write_bytes(data)
    print(f"{name}\t{len(data)}\t{hashlib.sha256(data).hexdigest()}\tboot_id={OLD_BOOT}")
print("LOCAL_PULL_AND_OLD_BOOT_VALIDATION=PASS")
