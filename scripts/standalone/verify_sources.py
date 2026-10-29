"""Verify a locked PYNQ video source checkout without changing it."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath


LOCK_PATH = Path(__file__).with_name("video_sources.lock.json")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _valid_relative_path(value) -> tuple[bool, str]:
    if not isinstance(value, str) or not value or "\\" in value:
        return False, "path must be a non-empty POSIX relative path"
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return False, "absolute paths are forbidden"
    if any(part in {"", ".", ".."} for part in value.split("/")):
        return False, "path traversal or empty path segments are forbidden"
    return True, ""


def _git_head(source_root: Path) -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return None, f"could not run git rev-parse: {exc}"
    if result.returncode != 0:
        return None, result.stderr.strip() or "git rev-parse HEAD failed"
    head = result.stdout.strip()
    if not head:
        return None, "git rev-parse HEAD returned an empty revision"
    return head, None


def verify_sources(source_root: Path, lock_path: Path = LOCK_PATH) -> dict:
    """Check the pinned commit and hashes for every lock entry, read-only."""
    errors = []
    file_results = []
    root_input = Path(source_root)
    lock_path = Path(lock_path)
    try:
        root = root_input.resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(str(root_input))
    except OSError as exc:
        return {
            "status": "failed",
            "source_root": str(root_input),
            "head": "unknown",
            "lock_commit": "unknown",
            "files": file_results,
            "errors": [{"kind": "source_root", "message": str(exc)}],
        }

    head, git_error = _git_head(root)
    if git_error:
        errors.append({"kind": "git", "message": git_error})

    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "status": "failed",
            "source_root": str(root),
            "head": head or "unknown",
            "lock_commit": "unknown",
            "files": file_results,
            "errors": errors + [{"kind": "lock", "message": str(exc)}],
        }

    if not isinstance(lock, dict):
        lock = {}
    lock_commit = lock.get("commit")
    entries = lock.get("files")
    if not isinstance(lock_commit, str) or not lock_commit:
        errors.append({"kind": "lock", "message": "lock commit is missing or invalid"})
        lock_commit = "unknown"
    if head is not None and lock_commit != head:
        errors.append({"kind": "commit", "message": f"HEAD {head} does not match lock commit {lock_commit}"})
    if not isinstance(entries, list):
        errors.append({"kind": "lock", "message": "lock files must be a list"})
        entries = []

    for entry in entries:
        if not isinstance(entry, dict):
            file_results.append({"path": "unknown", "status": "invalid_entry"})
            errors.append({"kind": "lock", "message": "file entry must be an object"})
            continue
        relative = entry.get("path")
        expected = entry.get("sha256_lf")
        path_result = {"path": relative if isinstance(relative, str) else "unknown", "expected_sha256": expected}
        valid_path, reason = _valid_relative_path(relative)
        if not valid_path:
            path_result["status"] = "invalid_path"
            errors.append({"kind": "path", "path": path_result["path"], "message": reason})
            file_results.append(path_result)
            continue
        if not isinstance(expected, str) or _SHA256.fullmatch(expected) is None:
            path_result["status"] = "invalid_hash"
            errors.append({"kind": "lock", "path": relative, "message": "sha256_lf must be 64 hexadecimal characters"})
            file_results.append(path_result)
            continue

        candidate = root.joinpath(*PurePosixPath(relative).parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except FileNotFoundError:
            path_result["status"] = "missing"
            errors.append({"kind": "missing", "path": relative, "message": "source file does not exist"})
            file_results.append(path_result)
            continue
        except (OSError, ValueError) as exc:
            path_result["status"] = "invalid_path"
            errors.append({"kind": "path", "path": relative, "message": f"source path escapes root or cannot be resolved: {exc}"})
            file_results.append(path_result)
            continue
        if not resolved.is_file():
            path_result["status"] = "missing"
            errors.append({"kind": "missing", "path": relative, "message": "source path is not a regular file"})
            file_results.append(path_result)
            continue
        try:
            content = resolved.read_bytes()
        except OSError as exc:
            path_result["status"] = "unreadable"
            errors.append({"kind": "read", "path": relative, "message": str(exc)})
            file_results.append(path_result)
            continue
        actual = hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()
        path_result["actual_sha256"] = actual
        if actual != expected.lower():
            path_result["status"] = "hash_mismatch"
            errors.append({"kind": "hash", "path": relative, "message": "SHA256 does not match lock"})
        else:
            path_result["status"] = "ok"
        file_results.append(path_result)

    return {
        "status": "ok" if not errors and head is not None else "failed",
        "source_root": str(root),
        "head": head or "unknown",
        "lock_commit": lock_commit,
        "files": file_results,
        "errors": errors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify locked PYNQ source files without modifying the checkout")
    parser.add_argument("--source-root", required=True, type=Path, help="PYNQ git checkout containing the locked files")
    parser.add_argument("--brief", action="store_true", help="print only status and file/error counts")
    args = parser.parse_args(argv)
    result = verify_sources(args.source_root, LOCK_PATH)
    if args.brief:
        error_kinds = Counter(error.get("kind", "unknown") for error in result["errors"])
        files_ok = sum(item.get("status") == "ok" for item in result["files"])
        output = {
            "status": result["status"],
            "files": {
                "total": len(result["files"]),
                "ok": files_ok,
                "failed": len(result["files"]) - files_ok,
            },
            "errors": {
                "count": len(result["errors"]),
                "by_kind": dict(sorted(error_kinds.items())),
            },
        }
    else:
        output = result
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
