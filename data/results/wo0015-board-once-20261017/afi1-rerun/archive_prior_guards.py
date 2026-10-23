from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess


RESULT = pathlib.Path(__file__).resolve().parent
LOCAL_ARCHIVE = RESULT / "legacy-guards-afi1-20261017-1805"
BOARD = "xilinx@192.168.2.99"
BOOT_ID = "2cc2ab91-2e3f-450e-b9e7-361ec38e2815"
OLD_MANIFEST_SHA256 = "2205c49479ffb8dae66aa7e34864aaa6783186c2dd1f96d8da9caeb9a24ecad0"
REMOTE_ROOT = pathlib.PurePosixPath("/var/lib/wo0015-s2mm")
REMOTE_STAGE = pathlib.PurePosixPath(
    "/home/xilinx/wo0015/afi1-legacy-staging-20261017-1805"
)
REMOTE_ARCHIVE = REMOTE_ROOT / "archive-afi1-20261017-1805"
REMOTE_PYTHON = "/usr/local/share/pynq-venv/bin/python3"
FILES = {
    "dma-single-consumed.json": "109f2b92aff12fa2dfca75b948dce0901697d4b69221629405afe46164dc075e",
    "overlay-only-consumed.json": "10c9d0f65758bd1d7b415409147b892cfed74cc5b23e567463f6dc0be81b2136",
    "overlay-only-pass.json": "a4fb476bc97f25bdb0346f11edc31906e6a55bc4bc9663200f8649e6634ad3f1",
    "terminal-failure.json": "317f3b82fb9b1accda79f438fe07041e258297e7e819770992912cd4aaafad12",
}
SSH_BASE = [
    "ssh.exe", "-oBatchMode=yes", "-oConnectionAttempts=1", "-oConnectTimeout=10", BOARD,
]
SCP_BASE = [
    "scp.exe", "-q", "-oBatchMode=yes", "-oConnectionAttempts=1", "-oConnectTimeout=10",
]


def run_remote(source: str) -> dict:
    proc = subprocess.run(
        SSH_BASE + [f"sudo -n {REMOTE_PYTHON} -"],
        input=source.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=60,
    )
    if proc.returncode:
        raise RuntimeError(
            f"remote archive step failed ({proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace')}"
        )
    return json.loads(proc.stdout.decode("utf-8"))


def main() -> None:
    LOCAL_ARCHIVE.mkdir(parents=True, exist_ok=False)
    expected = json.dumps(FILES, sort_keys=True)
    common = f"""
import hashlib, json, os, pathlib, shutil
boot_expected = {BOOT_ID!r}
manifest_expected = {OLD_MANIFEST_SHA256!r}
expected = {expected}
root = pathlib.Path({str(REMOTE_ROOT)!r})
stage = pathlib.Path({str(REMOTE_STAGE)!r})
archive = pathlib.Path({str(REMOTE_ARCHIVE)!r})
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def validate(path, digest):
    data = json.loads(path.read_text())
    if data.get('boot_id') != boot_expected or data.get('manifest_sha256') != manifest_expected:
        raise SystemExit('guard_identity_mismatch=' + path.name)
    actual = sha(path)
    if actual != digest: raise SystemExit('guard_sha256_mismatch=' + path.name + ':' + actual)
    return actual
boot = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
if boot != boot_expected: raise SystemExit('boot_id_mismatch=' + boot)
"""
    stage_result = run_remote(common + f"""
if stage.exists(): raise SystemExit('staging_path_already_exists=' + str(stage))
stage.mkdir(mode=0o755)
copied = {{}}
for name, digest in expected.items():
    source = root / name
    copied[name] = validate(source, digest)
    target = stage / name
    shutil.copy2(source, target)
    os.chmod(target, 0o644)
print(json.dumps({{'boot_id': boot, 'staging_dir': str(stage), 'copied': copied}}, sort_keys=True))
""")

    local_hashes = {}
    for name, digest in FILES.items():
        source = f"{BOARD}:{REMOTE_STAGE.as_posix()}/{name}"
        proc = subprocess.run(
            SCP_BASE + [source, str(LOCAL_ARCHIVE / name)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        if proc.returncode:
            raise RuntimeError(f"SCP failed for {name}: {proc.stderr.decode('utf-8', 'replace')}")
        actual = hashlib.sha256((LOCAL_ARCHIVE / name).read_bytes()).hexdigest()
        if actual != digest or stage_result["copied"].get(name) != digest:
            raise RuntimeError(f"local/remote guard hash mismatch for {name}: {actual}")
        local_hashes[name] = actual

    archive_result = run_remote(common + f"""
if archive.exists(): raise SystemExit('archive_path_already_exists=' + str(archive))
if not stage.is_dir(): raise SystemExit('staging_dir_missing=' + str(stage))
for name, digest in expected.items():
    if validate(root / name, digest) != digest: raise SystemExit('source_changed=' + name)
    staged = stage / name
    if sha(staged) != digest: raise SystemExit('staging_copy_changed=' + name)
archive.mkdir(mode=0o755)
moved = {{}}
for name, digest in expected.items():
    os.rename(root / name, archive / name)
    moved[name] = sha(archive / name)
    if moved[name] != digest: raise SystemExit('archive_hash_mismatch=' + name)
active = sorted(p.name for p in root.iterdir() if p.is_file() and p.name in expected)
if active: raise SystemExit('active_guard_files_remain=' + ','.join(active))
shutil.rmtree(stage)
print(json.dumps({{'boot_id': boot, 'archive': str(archive), 'archived': moved,
                  'active_guard_files': active, 'staging_removed': not stage.exists()}}, sort_keys=True))
""")
    receipt = {
        "boot_id": BOOT_ID, "old_manifest_sha256": OLD_MANIFEST_SHA256,
        "remote_staging": stage_result, "local_archive": str(LOCAL_ARCHIVE),
        "local_sha256": local_hashes, "remote_archive": archive_result,
        "password_prompted_or_supplied": False,
    }
    (RESULT / "guard-archive-result.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
