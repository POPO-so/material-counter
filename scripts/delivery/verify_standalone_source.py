"""Verify local correspondence between a standalone package and a Git commit."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


SCHEMA = "material-counter.standalone-source-verification.v1"
COMMIT_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_source(package_root: Path, source_root: Path, index_sha256: str) -> dict:
    # Resolve imports from the checkout root even when this file is run directly.
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from scripts.standalone.delivery import (  # noqa: PLC0415
        PACKAGE_SOURCE,
        PACKAGE_SOURCE_SCHEMA,
        _checked_file,
        _unique_json_object as _delivery_unique_json_object,
        _valid_relative_path,
        verify_package,
    )

    package_root = package_root.resolve(strict=True)
    source_root = source_root.resolve(strict=True)
    if not source_root.is_dir():
        raise ValueError("source root must be a directory")

    # Establish package integrity locally before comparing against Git objects.
    verify_package(package_root, index_sha256)
    source_path = _checked_file(package_root, PACKAGE_SOURCE)
    try:
        declaration = json.loads(
            source_path.read_text(encoding="utf-8"),
            object_pairs_hook=_delivery_unique_json_object,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid package-source.json") from exc
    if not isinstance(declaration, dict) or declaration.get("schema") != PACKAGE_SOURCE_SCHEMA:
        raise ValueError("unknown package source schema")

    commit = declaration.get("source_commit")
    branch = declaration.get("source_branch")
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise ValueError("package source commit must be a full Git object ID")
    if not isinstance(branch, str) or not branch:
        raise ValueError("package source branch is missing")
    raw_modules = declaration.get("module_files")
    if not isinstance(raw_modules, list) or not raw_modules:
        raise ValueError("package source module_files must be a non-empty list")
    modules = [_valid_relative_path(path) for path in raw_modules]
    if modules != sorted(set(modules)):
        raise ValueError("package source module_files must be unique and sorted")
    if any(
        not path.startswith("scripts/")
        or not path.endswith(".py")
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in path)
        for path in modules
    ):
        raise ValueError("package source module path is outside application scripts")

    verified_files = []
    for relative in modules:
        packaged = _checked_file(package_root, relative).read_bytes()
        try:
            result = subprocess.run(
                ["git", "-C", str(source_root), "cat-file", "blob", f"{commit}:{relative}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                timeout=30,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"cannot read source blob for {relative}") from exc
        source_bytes = result.stdout
        packaged_hash = _sha256(packaged)
        if len(source_bytes) != len(packaged) or _sha256(source_bytes) != packaged_hash:
            raise ValueError(f"package/source mismatch: {relative}")
        verified_files.append({
            "path": relative,
            "sha256": packaged_hash,
            "bytes": len(packaged),
        })

    return {
        "schema": SCHEMA,
        "source_commit": commit,
        "source_branch": branch,
        "module_count": len(verified_files),
        "index_sha256": index_sha256.lower(),
        "files": verified_files,
        "scope": "local_git_source_correspondence_only",
        "hardware_actions": 0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Locally compare standalone package modules with their declared Git commit"
    )
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--index-sha256", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.output is not None and args.output.exists():
        parser.error(f"refusing to overwrite output: {args.output}")
    try:
        result = verify_source(args.package_root, args.source_root, args.index_sha256)
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.output is None:
            print(payload, end="")
        else:
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
