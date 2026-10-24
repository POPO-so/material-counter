"""Build an isolated latest-main candidate locally; no network or board actions."""
import argparse
import ast
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

SCRIPT = Path(__file__).resolve().parent
TEST_FIXTURES = (
    "data/results/wo0015-final-20261017/pl-binary8-output.png",
    "data/results/wo0016-stageB2-attempt5-prep-20261021/counting-config.json",
    "data/results/wo0016-stageB2-attempt5-prep-20261021/attempt-5-recovery-2-sample-20261021/attempt-5-recovery-2/frame-results.json",
    "data/results/wo0016-stageB2-attempt5-prep-20261021/attempt-5-recovery-2-sample-20261021/attempt-5-recovery-2/attempt-5-recovery-2-result.json",
)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def bind_splitter(source):
    tree = ast.parse(source)
    function = next((node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == "formal_runtime_sha256"), None)
    if function and any(isinstance(node, ast.Constant) and node.value == "split_touching.py"
                        for node in ast.walk(function)):
        return source
    needle = b'"operator_gate_launcher.py", "stream_counter.py",\n'
    if not function or source.count(needle) != 1:
        raise ValueError("runtime hash function differs from the reviewed main source")
    return source.replace(needle, needle.rstrip(b"\n") + b' "split_touching.py",\n', 1)


def build(repo, source_ref, old_ref, output):
    repo, output = Path(repo).resolve(strict=True), Path(output).resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError("artifact destination already exists: " + str(output))
    source = git(repo, "rev-parse", "--verify", source_ref + "^{commit}").decode().strip()
    old = git(repo, "rev-parse", "--verify", old_ref + "^{commit}").decode().strip()
    paths = git(repo, "ls-tree", "-r", "--name-only", source, "--",
                "scripts/wo0015", "scripts/wo0016").decode().splitlines()
    paths = sorted(path for path in paths if path.endswith(".py"))
    if "scripts/wo0016/stream_counter.py" not in paths:
        raise ValueError("source has no reviewed counter runtime")
    exported = {path: git(repo, "show", source + ":" + path) for path in paths}
    runner = "scripts/wo0016/run_stream_counter.py"
    patched = bind_splitter(exported[runner])
    patch = "".join(difflib.unified_diff(exported[runner].decode().splitlines(True),
                                        patched.decode().splitlines(True),
                                        fromfile="a/" + runner, tofile="b/" + runner))
    originals = {path: digest(raw) for path, raw in exported.items()}
    exported[runner] = patched
    for path, raw in exported.items():
        compile(raw, path, "exec")
    fixtures = {path: git(repo, "show", source + ":" + path) for path in TEST_FIXTURES}
    output.mkdir(parents=True, exist_ok=False)
    payload = output / "payload"
    payload.mkdir()
    for path, raw in {**exported, **fixtures}.items():
        target = payload / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    (payload / "runtime-binding.patch").write_text(patch, encoding="utf-8", newline="\n")
    (payload / "tools").mkdir()
    for name in ("verify_split_package.py", "replay_split_board_candidate.py", "audit_split_startup_rollback.py"):
        (payload / "tools" / name).write_bytes((SCRIPT / name).read_bytes())
    audit = {}
    for name in ("split_touching.py", "stream_counter.py", "run_stream_counter.py",
                 "run_live_monitor.py", "live_calibration.py", "capture_dataset.py"):
        path = "scripts/wo0016/" + name
        exists = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", old + ":" + path],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        audit[path] = {"old_sha256": digest(git(repo, "show", old + ":" + path)) if exists else None,
                       "main_sha256": originals.get(path), "present_in_main": path in exported}
    (payload / "source-audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    rounds = []
    for scenario, expected in (("single", 1), ("touching-pair", 2), ("touching-triple", 3)):
        for repeat in range(1, 4):
            rounds.append({"round_id": f"{scenario}-r{repeat:02d}", "scenario": scenario,
                           "expected_count": expected, "frames": 300,
                           "status": "NOT_VERIFIED", "actual_count": None,
                           "independent_truth": {"status": "UNANNOTATED", "observer": "",
                               "independent_record_reference": "", "actual_window_entity_count": None,
                               "window_count_confirmed": False, "entities": []},
                           "entity_track_mapping": [], "dma_status": None,
                           "fps": None, "latency_ms": None, "cma_free_kib": None,
                           "missed_count": None, "duplicate_count": None, "false_split_count": None,
                           "evidence_paths_sha256": []})
    (payload / "round-plan.template.json").write_text(json.dumps({
        "status": "NOT_VERIFIED", "camera_height": "unchanged; operator confirmation pending",
        "hardware_authorized": False, "rounds": rounds}, indent=2) + "\n", encoding="utf-8")
    files = {path.relative_to(payload).as_posix(): digest(path.read_bytes())
             for path in sorted(payload.rglob("*")) if path.is_file()}
    manifest = {
        "schema": 1, "work_order": "WO-0017", "upstream_main_commit": source,
        "old_deployment_commit": old, "main_live_monitor_present": "scripts/wo0016/run_live_monitor.py" in exported,
        "variant": "main plus splitter runtime-hash binding only; algorithm unchanged",
        "patch_sha256": digest(patch.encode()), "files": files,
        "source_files": {path: {"upstream_sha256": originals[path], "sha256": digest(raw)}
                         for path, raw in exported.items()},
        "test_fixtures": {path: digest(raw) for path, raw in fixtures.items()},
        "hardware_status": "NOT_VERIFIED", "board_contacted": False,
        "reported_boot_id_unverified": "cf2ea1a2-f00e-4769-82bb-4f3e2a9e4d76",
        "planned_deployment_directory": "/home/xilinx/wo0017/split-validation-20261024-" + source[:8],
        "old_deployment_untouched": "/home/xilinx/wo0017/live-monitor-20261024-ee55267e",
        "current_cma_pynq_camera_dma": "NOT_VERIFIED; no remote checks performed",
        "hardware_start_blockers": [
            "CMA startup rollback requires explicit cleanup repair/review; see host fault-injection audit",
            "Split child pixel connectedness must be reviewed; replay reports geometry FAIL separately from count agreement"],
        "authority": "No Manifest, Stage 1 PASS, READY lock, consumed record or Permit is supplied"}
    raw_manifest = (json.dumps(manifest, indent=2) + "\n").encode()
    (payload / "PACKAGE.json").write_bytes(raw_manifest)
    archive = output / "deployment.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as stored:
        for path in sorted(payload.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(payload).as_posix(), date_time=(2026, 10, 24, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                stored.writestr(info, path.read_bytes())
    receipt = {"status": "PACKAGE_PREPARED", "hardware_status": "NOT_VERIFIED", "hardware_actions": 0,
               "tooling_commit": git(repo, "rev-parse", "HEAD").decode().strip(),
               "builder_sha256": digest(Path(__file__).read_bytes()),
               "upstream_main_commit": source, "manifest_sha256": digest(raw_manifest),
               "archive_sha256": digest(archive.read_bytes()), "archive": str(archive),
               "payload": str(payload), "patch_sha256": manifest["patch_sha256"],
               "planned_deployment_directory": manifest["planned_deployment_directory"]}
    (output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--old-ref", default="ee55267e288ccf05781545544391213a40dfe491")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(SCRIPT.parents[1], args.source_ref, args.old_ref, args.output), indent=2))
