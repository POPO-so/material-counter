import datetime
import hashlib
import json
import pathlib
import subprocess
import sys

RESULT = pathlib.Path(__file__).parent
PACKAGE = RESULT / "authorized-deployment.zip"
ZIP_SHA256 = "7cfc7e7ceb6d6ca9049eefca8723593dfefa08d9555916dd25478f3d22b70e12"
MANIFEST_SHA256 = "2205c49479ffb8dae66aa7e34864aaa6783186c2dd1f96d8da9caeb9a24ecad0"
BOOT_ID = "2cc2ab91-2e3f-450e-b9e7-361ec38e2815"

if hashlib.sha256(PACKAGE.read_bytes()).hexdigest() != ZIP_SHA256:
    raise SystemExit("local deployment ZIP hash mismatch")

steps = []


def run(args, label, input_bytes=None):
    proc = subprocess.run(
        args,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    steps.append(
        {
            "step": label,
            "args": args,
            "returncode": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", "replace"),
            "stderr": proc.stderr.decode("utf-8", "replace"),
        }
    )
    if proc.returncode:
        raise RuntimeError(f"{label} failed ({proc.returncode})")
    return proc


try:
    run(
        [
            "ssh.exe",
            "-oBatchMode=yes",
            "-oConnectionAttempts=1",
            "-oConnectTimeout=10",
            "xilinx@192.168.2.99",
            "mkdir /home/xilinx/wo0015",
        ],
        "create fresh deployment directory",
    )
    run(
        [
            "scp.exe",
            "-q",
            "-oBatchMode=yes",
            "-oConnectionAttempts=1",
            "-oConnectTimeout=10",
            str(PACKAGE),
            "xilinx@192.168.2.99:/home/xilinx/wo0015/authorized-deployment.zip",
        ],
        "upload authorized deployment ZIP",
    )
    remote_script = f'''import hashlib, json, pathlib, zipfile
work = pathlib.Path("/home/xilinx/wo0015")
boot = pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()
expected_boot = {BOOT_ID!r}
expected_zip = {ZIP_SHA256!r}
expected_manifest = {MANIFEST_SHA256!r}
if boot != expected_boot:
    raise SystemExit("boot_id_mismatch=" + boot)
archive = work / "authorized-deployment.zip"
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected_zip:
    raise SystemExit("uploaded_zip_sha256_mismatch")
with zipfile.ZipFile(archive) as zf:
    names = zf.namelist()
    if len(names) != len(set(names)) or any(n.startswith("/") or ".." in pathlib.PurePosixPath(n).parts for n in names):
        raise SystemExit("unsafe_or_duplicate_zip_members")
    zf.extractall(work)
sum_path = work / "SHA256SUMS.json"
expected = json.loads(sum_path.read_text())
if len(expected) != 14:
    raise SystemExit("unexpected_sha256_entry_count=" + str(len(expected)))
verified = {{}}
for name, digest in expected.items():
    path = work / name
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != digest:
        raise SystemExit("payload_hash_mismatch=" + name + ":" + actual)
    verified[name] = actual
manifest = work / "wo0015-manifest.json"
manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
if manifest_sha != expected_manifest or expected.get("wo0015-manifest.json") != expected_manifest:
    raise SystemExit("authorized_manifest_sha256_mismatch=" + manifest_sha)
manifest_data = json.loads(manifest.read_text())
if manifest_data.get("boot_id") != expected_boot:
    raise SystemExit("manifest_boot_id_mismatch=" + str(manifest_data.get("boot_id")))
if boot != pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip():
    raise SystemExit("boot_changed_during_upload_verification")
print(json.dumps({{"result":"DEPLOYMENT_PASS","boot_id":boot,"manifest_sha256":manifest_sha,"zip_sha256":expected_zip,"verified_sha256_entries":len(verified),"verified":verified}}, sort_keys=True))
'''
    run(
        [
            "ssh.exe",
            "-oBatchMode=yes",
            "-oConnectionAttempts=1",
            "-oConnectTimeout=10",
            "xilinx@192.168.2.99",
            "python3 -",
        ],
        "extract and verify deployment payload",
        remote_script.encode("utf-8"),
    )
except Exception as exc:
    overall = "FAIL"
    error = str(exc)
else:
    overall = "PASS"
    error = None

result = {
    "timestamp_local": datetime.datetime.now().astimezone().isoformat(),
    "result": overall,
    "boot_id": BOOT_ID,
    "manifest_sha256": MANIFEST_SHA256,
    "package_zip_sha256": ZIP_SHA256,
    "password_prompted_or_supplied": False,
    "error": error,
    "steps": steps,
}
(RESULT / "deployment-upload-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
for step in steps:
    if step["stdout"]:
        print(step["stdout"], end="")
    if step["stderr"]:
        print(step["stderr"], file=sys.stderr, end="")
if overall != "PASS":
    raise SystemExit(1)
