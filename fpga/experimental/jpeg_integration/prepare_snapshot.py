"""Stage a fixed offload source snapshot without merging another worktree.

Only Git objects from BASE_COMMIT are read. Existing output directories and
source worktrees are never overwritten. Generated Vivado projects belong in
the staged tree, leaving experiment sources and other agents' work separate.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile


BASE_COMMIT = "c5a86807c93e6ed572e2f75e13a6f9c7f1320321"
SOURCE_FILES = (
    "scripts/standalone/create_display.tcl",
    "scripts/standalone/create_g2_integration.tcl",
    "scripts/standalone/connect_offload.tcl",
    "scripts/standalone/offload_cdc.xdc",
    "scripts/standalone/build_display.tcl",
    "scripts/standalone/build_g2_integration.tcl",
    "scripts/standalone/video_sources.lock.json",
    "scripts/standalone/verify_sources.py",
    "scripts/standalone/display_timing.xdc",
    "scripts/gray8/create_bd.tcl",
    "fpga/experimental/gray8/axis_dma_gray_adapter.v",
    "fpga/experimental/gray8/axis_raw_gray_adapter.v",
    "fpga/rtl/axis_morphology3x3.v",
    "fpga/experimental/rle/axis_dma_rle_pipeline_bd.v",
    "fpga/experimental/rle/axis_rle_encoder.v",
    "fpga/experimental/rle/rle_feature_tap.v",
    "fpga/experimental/rle/rle_region_features.v",
    "fpga/experimental/standalone/video_binary_overlay.v",
    "fpga/experimental/standalone/video_binary_overlay_bd.v",
    "fpga/experimental/standalone/video_frame_monitor.v",
    "fpga/sim/standalone/tb_video_frame_monitor.sv",
)


def git_bytes(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise SystemExit(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def bind_native_log(payload: bytes, native_log: str) -> bytes:
    """Adapt only the staged legacy driver to the real MCP session log."""
    text = payload.decode("utf-8")
    assignment = "set ::env(DISPLAY_NATIVE_LOG) [file join $g2_out vivado-native.log]"
    creation = ("set native_log_channel [open $::env(DISPLAY_NATIVE_LOG) {WRONLY CREAT EXCL}]\n"
                "    close $native_log_channel")
    if text.count(assignment) != 1 or text.count(creation) != 1:
        raise ValueError("pinned G2 native-log setup changed; refuse an unchecked patch")
    normalized = native_log.replace("\\", "/")
    if any(c in normalized for c in "{}\n\r"):
        raise ValueError("native log path contains unsupported Tcl quoting characters")
    text = text.replace(assignment, "set ::env(DISPLAY_NATIVE_LOG) {" + normalized + "}", 1)
    text = text.replace(creation,
        'if {![file isfile $::env(DISPLAY_NATIVE_LOG)]} {error "JPEG_NATIVE_SESSION_LOG_MISSING"}', 1)
    return text.encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--native-log", help="Exact log_file returned by the dedicated Vivado MCP session")
    args = parser.parse_args()
    repo, output = args.repo.resolve(), args.out.resolve()
    if output.exists():
        parser.error(f"output already exists; use a new directory: {output}")
    commit = git_bytes(repo, "rev-parse", f"{BASE_COMMIT}^{{commit}}").decode().strip()
    archive = git_bytes(repo, "archive", "--format=zip", commit, "--", *SOURCE_FILES)
    manifest = {"schema": "material-counter.jpeg-joint-snapshot.v1",
                "base_commit": commit, "files": {}, "overrides": {},
                "stage": "source_snapshot_only", "ready_for_board": False}
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        files = {item.filename for item in source.infolist() if not item.is_dir()}
        if files != set(SOURCE_FILES):
            parser.error(f"snapshot file set mismatch: missing={set(SOURCE_FILES)-files}, extra={files-set(SOURCE_FILES)}")
        payloads = {}
        for name in sorted(files):
            parts = PurePosixPath(name)
            if parts.is_absolute() or ".." in parts.parts:
                parser.error(f"unsafe archive member: {name}")
            payload = source.read(name)
            manifest["files"][name] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            payloads[name] = payload
        if args.native_log:
            path = "scripts/standalone/create_g2_integration.tcl"
            payloads[path] = bind_native_log(payloads[path], args.native_log)
            manifest["overrides"][path] = {
                "reason": "Bind capture to the actual dedicated Vivado MCP log without truncating it",
                "base_sha256": manifest["files"][path]["sha256"],
                "staged_sha256": hashlib.sha256(payloads[path]).hexdigest(),
                "native_log": args.native_log,
            }
        if not args.dry_run:
            output.mkdir(parents=True, exist_ok=False)
            for name in sorted(files):
                target = output.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payloads[name])
            (output / "jpeg-joint-source.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PREFLIGHT_PASS" if args.dry_run else "SNAPSHOT_READY",
                      "base_commit": commit, "file_count": len(manifest["files"]),
                      "output": str(output), "ready_for_board": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
