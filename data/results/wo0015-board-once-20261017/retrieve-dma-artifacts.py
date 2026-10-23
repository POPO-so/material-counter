import datetime
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

RESULT = pathlib.Path(__file__).parent
EVIDENCE = RESULT / "dma-single-result.json"
diagnostics = json.loads(EVIDENCE.read_text(encoding="utf-8"))["rx_diagnostics"]
expected_remote = {
    "rx_raw.bin": diagnostics["rx_path"],
    "tx_raw.bin": diagnostics["tx_path"],
    "expected_golden.bin": diagnostics["expected_golden_path"],
    "diagnostics.json": diagnostics["diagnostics_path"],
}
expected_hashes = {
    "rx_raw.bin": diagnostics["rx_sha256"],
    "tx_raw.bin": diagnostics["tx_sha256"],
    "expected_golden.bin": diagnostics["expected_golden_sha256"],
}
dest = RESULT / "dma-single-diagnostics"
if dest.exists():
    raise SystemExit(f"refusing to merge into existing evidence directory: {dest}")
dest.mkdir()
scp_args = [
    "scp.exe",
    "-q",
    "-oBatchMode=yes",
    "-oConnectionAttempts=1",
    "-oConnectTimeout=10",
]
for remote_path in expected_remote.values():
    scp_args.append(f"xilinx@192.168.2.99:{remote_path}")
scp_args.append(str(dest))
proc = subprocess.run(scp_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
record = {
    "timestamp_local": datetime.datetime.now().astimezone().isoformat(),
    "scp_args": scp_args,
    "returncode": proc.returncode,
    "stdout": proc.stdout.decode("utf-8", "replace"),
    "stderr": proc.stderr.decode("utf-8", "replace"),
    "password_prompted_or_supplied": False,
}
if proc.returncode:
    (RESULT / "dma-artifact-retrieval-result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    raise SystemExit(proc.returncode)

stage_stem = "step2_frame00_id0000000000"
mapping = {
    f"{stage_stem}_rx_raw.bin": "rx_raw.bin",
    f"{stage_stem}_tx_raw.bin": "tx_raw.bin",
    f"{stage_stem}_expected_golden.bin": "expected_golden.bin",
    f"{stage_stem}_diagnostics.json": "diagnostics.json",
}
verified = {}
for staged_name, canonical in mapping.items():
    path = dest / staged_name
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    expected = expected_hashes.get(canonical)
    if expected is not None and actual != expected:
        raise SystemExit(f"sha256 mismatch for {staged_name}: {actual} != {expected}")
    verified[staged_name] = {"size": len(data), "sha256": actual}
    if canonical in expected_hashes:
        shutil.copyfile(path, RESULT / canonical)
for name, expected_size in (("rx_raw.bin", 76800), ("tx_raw.bin", 230432), ("expected_golden.bin", 76800)):
    actual_size = (RESULT / name).stat().st_size
    if actual_size != expected_size:
        raise SystemExit(f"unexpected {name} size: {actual_size} != {expected_size}")
record.update({"result": "PASS", "verified": verified, "local_aliases": ["rx_raw.bin", "tx_raw.bin", "expected_golden.bin"]})
(RESULT / "dma-artifact-retrieval-result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record, indent=2))
