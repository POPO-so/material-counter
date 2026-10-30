#!/usr/bin/env python3
"""Capture a read-only pre-deployment system baseline as a new JSON file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from datetime import datetime, timezone


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def path_record(path: Path) -> dict[str, object]:
    result: dict[str, object] = {"path": str(path), "state": "unknown"}
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {**result, "state": "missing", "exists": False}
    except OSError as exc:
        return {**result, "error": f"{type(exc).__name__}: {exc}"}

    kind = ("symlink" if stat.S_ISLNK(info.st_mode) else
            "file" if stat.S_ISREG(info.st_mode) else
            "directory" if stat.S_ISDIR(info.st_mode) else "other")
    result.update(state="present", exists=True, type=kind)
    if kind == "symlink":
        try:
            result["symlink_target"] = os.readlink(path)
            resolved = path.resolve(strict=True)
            result["resolved_target"] = str(resolved)
            result["resolved_target_state"] = path_record(resolved)
        except (OSError, RuntimeError) as exc:
            result["symlink_target_error"] = f"{type(exc).__name__}: {exc}"
    elif kind == "file":
        try:
            result["sha256"] = sha256_file(path)
        except OSError as exc:
            result["sha256_error"] = f"{type(exc).__name__}: {exc}"
    return result


def command_record(argv: list[str]) -> dict[str, object]:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True,
                                   check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"state": "unknown", "argv": argv,
                "error": f"{type(exc).__name__}: {exc}"}
    return {
        "state": "known" if completed.returncode == 0 else "unknown",
        "argv": argv,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        **({} if completed.returncode == 0 else {"error": "command failed"}),
    }


def systemd_record(unit: str) -> dict[str, object]:
    show = command_record([
        "systemctl", "show", unit, "--property=LoadState",
        "--property=ActiveState", "--property=SubState",
    ])
    enabled = command_record(["systemctl", "is-enabled", unit])
    fields: dict[str, str] = {}
    if show["state"] == "known":
        for line in str(show["stdout"]).splitlines():
            key, sep, value = line.partition("=")
            if sep:
                fields[key] = value
    return {
        "unit": unit,
        "load_state": fields.get("LoadState", "unknown"),
        "active_state": fields.get("ActiveState", "unknown"),
        "sub_state": fields.get("SubState", "unknown"),
        "enabled_state": enabled["stdout"] if enabled["state"] == "known" else "unknown",
        "show_result": show,
        "is_enabled_result": enabled,
    }


def process_records(proc_root: Path, needles: list[str]) -> dict[str, object]:
    found: list[dict[str, object]] = []
    try:
        entries = list(proc_root.iterdir())
    except OSError as exc:
        return {"state": "unknown", "error": f"{type(exc).__name__}: {exc}",
                "matches": found}
    errors: list[str] = []
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            command = raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
            if command and any(needle in command for needle in needles):
                found.append({"pid": int(entry.name), "cmdline": command})
        except (OSError, PermissionError) as exc:
            errors.append(f"{entry.name}: {type(exc).__name__}: {exc}")
    return {"state": "partial" if errors else "known", "matches": found,
            "errors": errors}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path,
                        help="new JSON path; creation is exclusive and never overwrites")
    parser.add_argument("--boot-id-file", required=True, type=Path)
    parser.add_argument("--unit-name", required=True)
    parser.add_argument("--unit-file", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--current-path", required=True, type=Path)
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--bit", required=True, type=Path)
    parser.add_argument("--hwh", required=True, type=Path)
    parser.add_argument("--native", required=True, action="append", type=Path,
                        help="candidate native library path; repeat when needed")
    parser.add_argument("--proc-root", required=True, type=Path)
    parser.add_argument("--process-needle", action="append", required=True,
                        help="literal process command-line substring; repeat for known entries")
    args = parser.parse_args()
    args.artifacts = [("index", args.index), ("manifest", args.manifest),
                      ("bit", args.bit), ("hwh", args.hwh)] + [
                          (f"native[{index}]", path)
                          for index, path in enumerate(args.native)
                      ]
    return args


def main() -> int:
    args = parse_args()
    try:
        boot_id = args.boot_id_file.read_text(encoding="utf-8").strip()
        boot = {"state": "known", "path": str(args.boot_id_file), "value": boot_id}
    except OSError as exc:
        boot = {"state": "unknown", "path": str(args.boot_id_file),
                "error": f"{type(exc).__name__}: {exc}"}

    candidate_artifacts = []
    for name, path in args.artifacts:
        record = path_record(path)
        if record["state"] == "present" and record.get("type") == "symlink":
            try:
                record["sha256"] = sha256_file(path)
            except OSError as exc:
                record["sha256_error"] = f"{type(exc).__name__}: {exc}"
        candidate_artifacts.append({"name": name, **record})

    document = {
        "schema": "material-counter-g4-release-baseline-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "read-only pre-deployment observation; not deployment authorization",
        "release_status": {"G4_release": False},
        "hardware_safety": {
            "hardware_safe_to_switch": False,
            "state": "unproven",
            "note": "This collector does not establish DMA idle, VDMA halted, error-free state, or owner/resource release.",
        },
        "boot_id": boot,
        "systemd": systemd_record(args.unit_name),
        "fixed_paths": {
            "unit_file": path_record(args.unit_file),
            "environment_file": path_record(args.env_file),
            "current": path_record(args.current_path),
        },
        "candidate_release": {
            "root": path_record(args.release_root),
            "artifacts": candidate_artifacts,
            "missing_artifacts": [item["name"] for item in candidate_artifacts
                                  if not item.get("sha256")],
        },
        "related_processes": process_records(args.proc_root, args.process_needle),
        "operations_performed": ["read files and metadata", "systemctl show",
                                  "systemctl is-enabled", "read proc cmdline"],
    }
    created = False
    try:
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError:
        print(f"refusing to overwrite existing output: {args.output}", file=sys.stderr)
        return 2
    except OSError as exc:
        if created:
            try:
                args.output.unlink(missing_ok=True)
            except OSError:
                pass
        print(f"cannot create output: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
