from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys


RESULT = pathlib.Path(__file__).resolve().parent
PACKAGE = RESULT.parent / "afi1-authorized-deployment-v2.zip"
ZIP_SHA256 = "07bffc3acbad924f4b48df8746e528d26164e08df3f5230875abfa1c3b1286cb"
MANIFEST_SHA256 = "c18a0396a6723df171e41a87390dc9cb20886e7cd74347f0c2330573fbe75767"
BOOT_ID = "2cc2ab91-2e3f-450e-b9e7-361ec38e2815"
BOARD = "xilinx@192.168.2.99"
REMOTE_ROOT = "/home/xilinx/wo0015"
REMOTE_RUN = f"{REMOTE_ROOT}/afi1-rerun"
REMOTE_PACKAGE = f"{REMOTE_ROOT}/afi1-authorized-deployment-v2.zip"
REMOTE_PYTHON = "/usr/local/share/pynq-venv/bin/python3"
SSH = ["ssh.exe", "-oBatchMode=yes", "-oConnectionAttempts=1", "-oConnectTimeout=10", BOARD]
SCP = ["scp.exe", "-q", "-oBatchMode=yes", "-oConnectionAttempts=1", "-oConnectTimeout=10"]
records: list[dict] = []


def save_controller() -> None:
    (RESULT / "controller-result.json").write_text(
        json.dumps({
            "boot_id": BOOT_ID,
            "manifest_sha256": MANIFEST_SHA256,
            "package_zip_sha256": ZIP_SHA256,
            "remote_python": REMOTE_PYTHON,
            "steps": records,
            "password_prompted_or_supplied": False,
        }, indent=2) + "\n",
        encoding="utf-8",
    )


def run_ssh(label: str, command: str, *, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        SSH + [command], input=input_bytes, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    records.append({
        "step": label, "args": SSH + [command], "returncode": proc.returncode,
        "stdout_file": f"{label.replace(' ', '-')}-stdout.txt",
        "stderr_file": f"{label.replace(' ', '-')}-stderr.txt",
        "password_prompted_or_supplied": False,
    })
    (RESULT / f"{label.replace(' ', '-')}-stdout.txt").write_bytes(proc.stdout)
    (RESULT / f"{label.replace(' ', '-')}-stderr.txt").write_bytes(proc.stderr)
    save_controller()
    return proc


def remote_json(label: str, source: str) -> dict:
    proc = run_ssh(label, f"sudo -n {REMOTE_PYTHON} -", input_bytes=source.encode("utf-8"))
    if proc.returncode:
        raise RuntimeError(f"{label} failed: {proc.stderr.decode('utf-8', 'replace')}")
    return json.loads(proc.stdout.decode("utf-8"))


def scp_from(label: str, remote_path: str, local_path: pathlib.Path) -> str:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    args = SCP + [f"{BOARD}:{remote_path}", str(local_path)]
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    records.append({
        "step": label, "args": args, "returncode": proc.returncode,
        "stdout": proc.stdout.decode("utf-8", "replace"),
        "stderr": proc.stderr.decode("utf-8", "replace"),
    })
    save_controller()
    if proc.returncode:
        raise RuntimeError(f"{label} failed: {proc.stderr.decode('utf-8', 'replace')}")
    return hashlib.sha256(local_path.read_bytes()).hexdigest()


def run_stage(stage: str) -> tuple[subprocess.CompletedProcess, dict]:
    remote_evidence = f"{REMOTE_RUN}/{stage}-result.json"
    command = (
        f"sudo -n env XILINX_XRT=/usr {REMOTE_PYTHON} -u {REMOTE_ROOT}/board_once.py "
        f"--mode {stage} --manifest {REMOTE_ROOT}/wo0015-manifest.json "
        f"--manifest-sha256 {MANIFEST_SHA256} --execute --authorize-stage {stage} "
        f"--approve-once --timeout-s 5 --evidence-json {remote_evidence}"
    )
    proc = run_ssh(f"execute-{stage}", command)
    local = RESULT / f"{stage}-result.json"
    scp_from(f"retrieve-{stage}-result", remote_evidence, local)
    return proc, json.loads(local.read_text(encoding="utf-8"))


def main() -> None:
    if hashlib.sha256(PACKAGE.read_bytes()).hexdigest() != ZIP_SHA256:
        raise SystemExit("local deployment ZIP SHA-256 mismatch")
    identity = remote_json("pre-upload-boot-python-check", f"""
import json, pathlib, sys
boot = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
expected = {BOOT_ID!r}
if boot != expected: raise SystemExit('boot_id_mismatch=' + boot)
if pathlib.Path(sys.prefix).resolve() != pathlib.Path('/usr/local/share/pynq-venv').resolve():
    raise SystemExit('wrong_python_prefix=' + sys.prefix)
import pynq
print(json.dumps({{'boot_id': boot, 'python': sys.executable,
                  'expected_invocation': {REMOTE_PYTHON!r},
                  'python_prefix': sys.prefix,
                  'pynq_path': str(pathlib.Path(pynq.__file__).resolve())}}))
""")
    records.append({"step": "pynq-interpreter-check", "result": identity})
    save_controller()

    upload_args = SCP + [str(PACKAGE), f"{BOARD}:{REMOTE_PACKAGE}"]
    upload = subprocess.run(upload_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    records.append({
        "step": "upload-afi1-deployment-zip", "args": upload_args,
        "returncode": upload.returncode,
        "stdout": upload.stdout.decode("utf-8", "replace"),
        "stderr": upload.stderr.decode("utf-8", "replace"),
    })
    save_controller()
    if upload.returncode:
        raise RuntimeError(f"deployment upload failed: {upload.stderr.decode('utf-8', 'replace')}")

    deploy = remote_json("extract-and-verify-deployment", f"""
import hashlib, json, pathlib, sys, zipfile
root = pathlib.Path({REMOTE_ROOT!r})
run_dir = pathlib.Path({REMOTE_RUN!r})
archive = pathlib.Path({REMOTE_PACKAGE!r})
expected_boot = {BOOT_ID!r}
expected_zip = {ZIP_SHA256!r}
expected_manifest = {MANIFEST_SHA256!r}
boot = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
if boot != expected_boot: raise SystemExit('boot_id_mismatch=' + boot)
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected_zip:
    raise SystemExit('uploaded_zip_sha256_mismatch')
if run_dir.exists(): raise SystemExit('run_directory_already_exists=' + str(run_dir))
run_dir.mkdir(mode=0o755)
with zipfile.ZipFile(archive) as zf:
    names = zf.namelist()
    if len(names) != 15 or len(names) != len(set(names)) or any(n.startswith('/') or '..' in pathlib.PurePosixPath(n).parts for n in names):
        raise SystemExit('unsafe_or_duplicate_zip_members')
    zf.extractall(root)
sum_path = root / 'SHA256SUMS.json'
expected = json.loads(sum_path.read_text())
if len(expected) != 14: raise SystemExit('unexpected_sha256_entry_count=' + str(len(expected)))
verified = {{}}
for name, digest in expected.items():
    actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
    if actual != digest: raise SystemExit('payload_hash_mismatch=' + name + ':' + actual)
    verified[name] = actual
manifest = root / 'wo0015-manifest.json'
manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
data = json.loads(manifest.read_text())
if manifest_sha != expected_manifest or expected.get('wo0015-manifest.json') != expected_manifest:
    raise SystemExit('authorized_manifest_sha256_mismatch=' + manifest_sha)
if data.get('boot_id') != expected_boot: raise SystemExit('manifest_boot_id_mismatch')
if data.get('executor_sha256', {{}}).get('board_once.py') != verified['board_once.py']:
    raise SystemExit('board_once_manifest_executor_hash_mismatch')
if data.get('executor_sha256', {{}}).get('board_preflight.py') != verified['board_preflight.py']:
    raise SystemExit('board_preflight_manifest_executor_hash_mismatch')
if pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip() != expected_boot:
    raise SystemExit('boot_changed_during_upload_verification')
print(json.dumps({{'result':'DEPLOYMENT_PASS','boot_id':boot,
                  'manifest_sha256':manifest_sha,'zip_sha256':expected_zip,
                  'verified_sha256_entries':len(verified),'verified':verified}}, sort_keys=True))
""")
    records.append({"step": "deployment-verification", "result": deploy})
    save_controller()

    overlay_proc, overlay = run_stage("overlay-only")
    if overlay_proc.returncode or overlay.get("status") != "OVERLAY_LOAD_PASS":
        raise RuntimeError("Stage 1 did not return OVERLAY_LOAD_PASS; Stage 2 was not started")
    if overlay.get("boot_id") != BOOT_ID or overlay.get("manifest_sha256") != MANIFEST_SHA256:
        raise RuntimeError("Stage 1 receipt identity mismatch")
    post_overlay = overlay["afi1_snapshots"][-1]["registers"]
    if post_overlay["AFI1_RDCHAN_CTRL"].get("width_bit0") != 1 or \
            post_overlay["AFI1_WRCHAN_CTRL"].get("width_bit0") != 1:
        raise RuntimeError("Stage 1 AFI1 bit 0 readback is not set; Stage 2 was not started")

    dma_proc, dma = run_stage("dma-single")
    diag = dma.get("rx_diagnostics")
    if diag is None:
        frames = dma.get("frames", [])
        diag = next((f.get("raw_capture") for f in frames if f.get("raw_capture")), None)
    if diag:
        for local_name, key in (
            ("rx_raw.bin", "rx_path"), ("tx_raw.bin", "tx_path"),
            ("expected_golden.bin", "expected_golden_path"),
            ("step2-diagnostics.json", "diagnostics_path"),
        ):
            remote_path = diag.get(key)
            if remote_path:
                digest = scp_from(f"retrieve-{local_name}", remote_path, RESULT / local_name)
                expected = diag.get({
                    "rx_raw.bin": "rx_sha256", "tx_raw.bin": "tx_sha256",
                    "expected_golden.bin": "expected_golden_sha256",
                }.get(local_name, ""))
                if expected and digest != expected:
                    raise RuntimeError(f"downloaded {local_name} SHA-256 mismatch")

    guards = remote_json("stage-guard-evidence", f"""
import hashlib, json, os, pathlib, shutil
boot_expected = {BOOT_ID!r}
manifest_expected = {MANIFEST_SHA256!r}
root = pathlib.Path('/var/lib/wo0015-s2mm')
run_dir = pathlib.Path({REMOTE_RUN!r})
stage = run_dir / 'guard-evidence-transfer'
if stage.exists(): raise SystemExit('guard_transfer_dir_already_exists')
stage.mkdir(mode=0o755)
names = ('overlay-only-consumed.json','overlay-only-pass.json',
         'dma-single-consumed.json','dma-single-pass.json','terminal-failure.json')
copied = {{}}
for name in names:
    source = root / name
    if not source.is_file(): continue
    data = json.loads(source.read_text())
    if data.get('boot_id') != boot_expected or data.get('manifest_sha256') != manifest_expected:
        raise SystemExit('guard_identity_mismatch=' + name)
    target = stage / name
    shutil.copy2(source, target)
    os.chmod(target, 0o644)
    copied[name] = hashlib.sha256(target.read_bytes()).hexdigest()
print(json.dumps({{'boot_id':boot_expected,'staged':str(stage),'files':copied}}, sort_keys=True))
""")
    records.append({"step": "guard-evidence-staging", "result": guards})
    save_controller()
    for name, digest in guards["files"].items():
        local_path = RESULT / "guard-evidence" / name
        actual = scp_from(f"retrieve-guard-{name}", f"{REMOTE_RUN}/guard-evidence-transfer/{name}", local_path)
        if actual != digest:
            raise RuntimeError(f"guard evidence SHA-256 mismatch for {name}")
    clean = remote_json("remove-guard-transfer-stage", f"""
import json, pathlib, shutil
stage = pathlib.Path({(REMOTE_RUN + '/guard-evidence-transfer')!r})
if not stage.is_dir(): raise SystemExit('guard_transfer_dir_missing')
shutil.rmtree(stage)
print(json.dumps({{'removed':not stage.exists()}}))
""")
    records.append({"step": "guard-evidence-staging-cleanup", "result": clean})
    save_controller()

    failed_completion = dma.get("failed_frame_completion") or {}
    frames = dma.get("frames", [])
    completed_frame = frames[0] if frames else {}
    summary = {
        "stage1_returncode": overlay_proc.returncode,
        "stage1_status": overlay.get("status"),
        "stage2_returncode": dma_proc.returncode,
        "stage2_status": dma.get("status"),
        "stage2_byte_mismatches": dma.get("byte_mismatches"),
        "stage2_s2mm_dmasr": failed_completion.get("s2mm_dmasr", completed_frame.get("s2mm_dmasr")),
        "stage2_mm2s_dmasr": failed_completion.get("mm2s_dmasr", completed_frame.get("mm2s_dmasr")),
    }
    records.append({"step": "final-summary", "result": summary})
    save_controller()
    print(json.dumps(summary, indent=2))
    if dma_proc.returncode:
        raise SystemExit(dma_proc.returncode)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        records.append({"step": "controller-error", "error": f"{type(exc).__name__}: {exc}"})
        save_controller()
        print(f"CONTROLLER_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        raise
