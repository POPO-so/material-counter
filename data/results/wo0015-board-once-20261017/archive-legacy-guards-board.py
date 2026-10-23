import datetime
import json
import pathlib
import subprocess
import sys

RESULT = pathlib.Path(__file__).parent
LOCAL_DIR = RESULT / "legacy-guards-822a9c29"
OLD_BOOT = "822a9c29-37c0-438c-81c4-da998d57073b"
NEW_BOOT = "2cc2ab91-2e3f-450e-b9e7-361ec38e2815"
HASHES = {
    "cma-watermark-recovery-consumed.json": "ed86d62376dfaf78673b7af9b0e014e5a3ffbb56909b70ac5f40eb97abcc4de0",
    "dma-single-consumed.json": "1585844bceb4bca82cee2f4a2be14916b2fee847a26fb7f28c207e9234fcf862",
    "dma-single-watermark-retry2-consumed.json": "1585844bceb4bca82cee2f4a2be14916b2fee847a26fb7f28c207e9234fcf862",
    "overlay-only-consumed.json": "c333975de186464bc3db9809c7762d7d7115cfc73f8b17f8e8672e3349efa37f",
    "overlay-only-pass.json": "ee3d9ba6462962657bbd43807302387af16d4fbec34ee65f909a385886802d96",
    "run-only-init-consumed.json": "ac53e6acba00404c86acd7c2e5dae1729ef0a62228538f857b8104d6b866df18",
    "terminal-failure.json": "b796fba0208adbfe2ec5df7ceaa6a3988f6602f9c1f6e18c8b74a6255817421e",
}

for name, expected in HASHES.items():
    import hashlib

    actual = hashlib.sha256((LOCAL_DIR / name).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"local archive copy hash mismatch for {name}: {actual}")

remote_script = r'''import hashlib, json, os, pathlib, shutil, sys
base = pathlib.Path("/var/lib/wo0015-s2mm")
old_boot = "822a9c29-37c0-438c-81c4-da998d57073b"
new_boot = "2cc2ab91-2e3f-450e-b9e7-361ec38e2815"
archive = base / "archive-822a9c29-20261017"
expected = {
    "cma-watermark-recovery-consumed.json": "ed86d62376dfaf78673b7af9b0e014e5a3ffbb56909b70ac5f40eb97abcc4de0",
    "dma-single-consumed.json": "1585844bceb4bca82cee2f4a2be14916b2fee847a26fb7f28c207e9234fcf862",
    "dma-single-watermark-retry2-consumed.json": "1585844bceb4bca82cee2f4a2be14916b2fee847a26fb7f28c207e9234fcf862",
    "overlay-only-consumed.json": "c333975de186464bc3db9809c7762d7d7115cfc73f8b17f8e8672e3349efa37f",
    "overlay-only-pass.json": "ee3d9ba6462962657bbd43807302387af16d4fbec34ee65f909a385886802d96",
    "run-only-init-consumed.json": "ac53e6acba00404c86acd7c2e5dae1729ef0a62228538f857b8104d6b866df18",
    "terminal-failure.json": "b796fba0208adbfe2ec5df7ceaa6a3988f6602f9c1f6e18c8b74a6255817421e",
}
def contains_boot(value, wanted):
    if isinstance(value, dict):
        return any((k == "boot_id" and v == wanted) or contains_boot(v, wanted) for k, v in value.items())
    if isinstance(value, list):
        return any(contains_boot(v, wanted) for v in value)
    return False
actual_boot = pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()
if actual_boot != new_boot:
    raise SystemExit("current_boot_id_mismatch=" + actual_boot)
files = sorted(p.name for p in base.iterdir() if p.is_file())
if files != sorted(expected):
    raise SystemExit("active_root_file_set_mismatch=" + json.dumps(files))
if archive.exists():
    raise SystemExit("archive_destination_already_exists=" + str(archive))
for name, digest in expected.items():
    path = base / name
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise SystemExit("board_hash_mismatch=" + name)
    if not contains_boot(json.loads(data), old_boot):
        raise SystemExit("old_boot_id_mismatch=" + name)
archive.mkdir(mode=0o700)
moved = []
try:
    for name in expected:
        os.rename(base / name, archive / name)
        moved.append(name)
    dest_files = sorted(p.name for p in archive.iterdir() if p.is_file())
    root_files = sorted(p.name for p in base.iterdir() if p.is_file())
    if dest_files != sorted(expected) or root_files:
        raise RuntimeError("post_move_verification_failed=" + json.dumps({"archive": dest_files, "root": root_files}))
    for name, digest in expected.items():
        if hashlib.sha256((archive / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("archive_hash_mismatch=" + name)
except Exception:
    for name in reversed(moved):
        os.rename(archive / name, base / name)
    try:
        archive.rmdir()
    except OSError:
        pass
    raise
print(json.dumps({"result":"ARCHIVE_PASS","boot_id":actual_boot,"archive":str(archive),"archived_files":dest_files,"active_root_files":root_files}, sort_keys=True))
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
    input=remote_script.encode("utf-8"),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
result = {
    "timestamp_local": datetime.datetime.now().astimezone().isoformat(),
    "command": "ssh BatchMode + sudo -n python3 - (archive exactly 7 old boot guards)",
    "returncode": proc.returncode,
    "stdout": proc.stdout.decode("utf-8", "replace"),
    "stderr": proc.stderr.decode("utf-8", "replace"),
    "password_prompted_or_supplied": False,
}
(RESULT / "legacy-archive-board-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(result["stdout"], end="")
if result["stderr"]:
    print(result["stderr"], file=sys.stderr, end="")
if proc.returncode:
    raise SystemExit(proc.returncode)
