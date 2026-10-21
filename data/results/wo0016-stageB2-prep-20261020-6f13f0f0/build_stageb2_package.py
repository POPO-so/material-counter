"""Build the current-boot Stage 1 / Stage B-2 package from baseline 5926820."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import zipfile

BASELINE = "59268209148888c135ff7ff5c0aa7dba8ddbaf66"
BOOT_ID = "6f13f0f0-4433-445f-a023-fa403685aeee"
WORKTREE = Path(r"C:\Users\POP\.codex\worktrees\wo0016-stageb2-no-consume\material-counter")
PROJECT = Path(r"C:\fpga\material-counter")
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "board-runtime-currentboot-v2"
ARCHIVE = HERE / "board-runtime-currentboot-v2.zip"
if OUTPUT.exists() or ARCHIVE.exists():
    raise SystemExit("STOP: package output already exists; refusing to overwrite")

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def git_bytes(path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{BASELINE}:{path}"], cwd=PROJECT)

expected_images = {
    "wo15.bit": ("data/results/wo0015-hw-50mhz-20261015/export/wo15.bit",
                 "a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420"),
    "wo15.hwh": ("data/results/wo0015-hw-50mhz-20261015/export/wo15.hwh",
                 "bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6"),
}
for name, (path, expected) in expected_images.items():
    if sha256(git_bytes(path)) != expected:
        raise SystemExit(f"STOP: baseline commit {BASELINE} has unexpected {name} hash")

stage1 = OUTPUT / "scripts" / "wo0015"
stageb = OUTPUT / "scripts" / "wo0016"
stage1.mkdir(parents=True)
stageb.mkdir(parents=True)
for name in ("board_once.py", "board_preflight.py"):
    data = (WORKTREE / "scripts" / "wo0015" / name).read_bytes()
    if name == "board_preflight.py":
        old = b'AUTHORIZED_BOOT_ID = "f8b24aee-ed7e-430a-b3f3-d26056746bfd"'
        new = f'AUTHORIZED_BOOT_ID = "{BOOT_ID}"'.encode()
        if data.count(old) != 1:
            raise SystemExit("STOP: unexpected baseline authorization constant")
        data = data.replace(old, new, 1)
    (stage1 / name).write_bytes(data)
for name in ("camera_dma_oneshot.py", "camera_adapter.py", "prepare_vectors.py"):
    shutil.copy2(WORKTREE / "scripts" / "wo0015" / name, stage1 / name)

for name, (path, _expected) in expected_images.items():
    (stage1 / name).write_bytes(git_bytes(path))
original_manifest = json.loads((HERE / "original-wo15-run-manifest.json").read_text(encoding="utf-8"))
manifest = dict(original_manifest)
manifest["boot_id"] = BOOT_ID
manifest["executor_sha256"] = {
    name: sha256((stage1 / name).read_bytes())
    for name in ("board_once.py", "board_preflight.py")
}
for frame in manifest["frames"]:
    for key in ("packet_path", "binary_path"):
        source = HERE / frame[key]
        target = stage1 / frame[key]
        if not source.is_file():
            raise SystemExit(f"STOP: missing frame artifact {source}")
        shutil.copy2(source, target)
manifest_path = stage1 / "run-manifest.json"
manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

for name in ("run_stream_counter.py", "packet_encoder.py",
             "mjpeg_preview.py", "stream_counter.py"):
    shutil.copy2(WORKTREE / "scripts" / "wo0016" / name, stageb / name)

digest = sha256(manifest_path.read_bytes())
check = subprocess.run(
    [r"C:\fpga\material-counter\.venv\Scripts\python.exe", "-c",
     "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
     "import board_preflight as p; plan=p.validate_manifest(sys.argv[2],sys.argv[3]); "
     "print(plan.boot_id,plan.manifest_sha256,plan.bit_sha256,plan.hwh_sha256)",
     str(stage1), str(manifest_path), digest],
    capture_output=True, text=True, cwd=WORKTREE,
    env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
)
if check.returncode != 0 or BOOT_ID not in check.stdout:
    raise SystemExit(f"STOP: generated current-boot manifest failed validation: {check.stderr}")

files = {}
for path in sorted(OUTPUT.rglob("*")):
    if path.is_file():
        files[path.relative_to(OUTPUT).as_posix()] = sha256(path.read_bytes())
(OUTPUT / "PACKAGE_SHA256.json").write_text(
    json.dumps({"baseline_commit": BASELINE, "authorized_boot_id": BOOT_ID,
                "manifest_sha256": digest, "files": files}, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(OUTPUT.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(HERE).as_posix())
print(json.dumps({"package": str(OUTPUT), "archive": str(ARCHIVE),
                  "baseline_commit": BASELINE, "boot_id": BOOT_ID,
                  "manifest_sha256": digest, "manifest_validation": check.stdout.strip(),
                  "files": len(files)}, indent=2))
