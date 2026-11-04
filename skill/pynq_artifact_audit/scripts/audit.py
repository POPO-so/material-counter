#!/usr/bin/env python3
"""Read-only SHA-256 audit for a reviewed PYNQ artifact manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")
CHUNK_SIZE = 1024 * 1024
VALID_ROLES = {"bit", "hwh", "source", "config"}


class AuditError(Exception):
    """An expected validation error suitable for the JSON report."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise AuditError(message)


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def contained_file(root: Path, relative_path: str) -> Path:
    """Resolve a relative manifest path and reject root escapes, including links."""
    if not relative_path or "\x00" in relative_path:
        raise AuditError("file path must be a non-empty relative path")

    windows_path = PureWindowsPath(relative_path)
    if windows_path.drive or windows_path.root:
        raise AuditError(f"absolute or rooted file path is not allowed: {relative_path}")

    portable_path = relative_path.replace("\\", "/")
    if any(part == ".." for part in portable_path.split("/")):
        raise AuditError(f"parent traversal is not allowed: {relative_path}")
    normalized = PurePosixPath(portable_path)
    if normalized.is_absolute() or not normalized.parts:
        raise AuditError(f"invalid relative file path: {relative_path}")

    lexical_path = root.joinpath(*normalized.parts)
    try:
        resolved = lexical_path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise AuditError(f"file is missing or cannot be resolved: {relative_path}: {error}") from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise AuditError(f"resolved file escapes audit root: {relative_path}") from error
    if not resolved.is_file():
        raise AuditError(f"manifest path is not a regular file: {relative_path}")
    return resolved


def validate_manifest_structure(document: Any) -> list[dict[str, str]]:
    if not isinstance(document, dict) or set(document) != {"schema", "files"}:
        raise AuditError("manifest must contain exactly 'schema' and 'files'")
    if type(document.get("schema")) is not int or document["schema"] != 1:
        raise AuditError("unsupported manifest schema; expected integer 1")
    entries = document.get("files")
    if not isinstance(entries, list):
        raise AuditError("manifest 'files' must be an array")

    seen_paths: set[str] = set()
    role_counts = {"bit": 0, "hwh": 0}
    validated: list[dict[str, str]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != {"role", "path", "sha256"}:
            raise AuditError(f"files[{index}] must contain exactly role, path, and sha256")
        role, relative_path, expected_hash = entry["role"], entry["path"], entry["sha256"]
        if not isinstance(role, str) or role not in VALID_ROLES:
            raise AuditError(f"files[{index}] has unsupported role: {role!r}")
        if not isinstance(relative_path, str):
            raise AuditError(f"files[{index}].path must be a string")
        if not is_sha256(expected_hash):
            raise AuditError(f"files[{index}].sha256 must contain exactly 64 hexadecimal characters")

        portable_path = relative_path.replace("\\", "/")
        normalized_path = "/".join(PurePosixPath(portable_path).parts)
        duplicate_key = os.path.normcase(normalized_path)
        if duplicate_key in seen_paths:
            raise AuditError(f"duplicate manifest path: {relative_path}")
        seen_paths.add(duplicate_key)
        if role in role_counts:
            role_counts[role] += 1
        validated.append({"role": role, "path": relative_path, "sha256": expected_hash.lower()})

    for role, count in role_counts.items():
        if count != 1:
            raise AuditError(f"manifest must contain exactly one {role!r} entry; found {count}")
    return validated


def audit(manifest_path: Path, expected_manifest_hash: str, root_argument: Path | None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": "ARTIFACT_HASH_FAIL",
        "manifest": str(manifest_path),
        "expected_manifest_sha256": expected_manifest_hash.lower(),
        "files": [],
        "errors": [],
    }
    if not is_sha256(expected_manifest_hash):
        raise AuditError("--manifest-sha256 must contain exactly 64 hexadecimal characters")

    try:
        manifest_path = manifest_path.resolve(strict=True)
        manifest_bytes = manifest_path.read_bytes()
    except (OSError, RuntimeError, ValueError) as error:
        raise AuditError(f"cannot read manifest: {error}") from error

    actual_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    report["manifest"] = str(manifest_path)
    report["manifest_sha256"] = actual_manifest_hash
    if actual_manifest_hash != expected_manifest_hash.lower():
        report["errors"].append("manifest SHA-256 does not match the reviewed value")
        return report

    try:
        document = json.loads(manifest_bytes.decode("utf-8"))
        entries = validate_manifest_structure(document)
        root = (root_argument if root_argument is not None else manifest_path.parent).resolve(strict=True)
        if not root.is_dir():
            raise AuditError("audit root is not a directory")
    except (UnicodeDecodeError, json.JSONDecodeError, OSError, RuntimeError, ValueError, AuditError) as error:
        report["errors"].append(str(error))
        return report

    report["root"] = str(root)
    for entry in entries:
        result: dict[str, Any] = {
            "role": entry["role"],
            "path": entry["path"],
            "expected_sha256": entry["sha256"],
        }
        try:
            resolved_path = contained_file(root, entry["path"])
            actual_hash = hash_file(resolved_path)
            result["actual_sha256"] = actual_hash
            result["status"] = "PASS" if actual_hash == entry["sha256"] else "FAIL"
            if result["status"] == "FAIL":
                report["errors"].append(f"SHA-256 mismatch: {entry['path']}")
        except (OSError, RuntimeError, ValueError, AuditError) as error:
            result["status"] = "FAIL"
            result["error"] = str(error)
            report["errors"].append(f"{entry['path']}: {error}")
        report["files"].append(result)

    if not report["errors"]:
        report["status"] = "ARTIFACT_HASH_PASS"
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="Read-only SHA-256 audit for a reviewed PYNQ artifact manifest.")
    parser.add_argument("--manifest", required=True, type=Path, help="path to the reviewed JSON manifest")
    parser.add_argument("--manifest-sha256", required=True, help="reviewed SHA-256 of the exact manifest bytes")
    parser.add_argument("--root", type=Path, help="base directory for manifest-relative files (default: manifest directory)")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        report = audit(args.manifest, args.manifest_sha256, args.root)
    except AuditError as error:
        report = {"status": "ARTIFACT_HASH_FAIL", "files": [], "errors": [str(error)]}
    except Exception as error:  # Keep expected CLI failures machine-readable without a traceback.
        report = {"status": "ARTIFACT_HASH_FAIL", "files": [], "errors": [f"audit failed: {error}"]}

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "ARTIFACT_HASH_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
