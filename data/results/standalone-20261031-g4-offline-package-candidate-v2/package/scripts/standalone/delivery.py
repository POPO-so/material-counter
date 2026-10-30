"""Build a deployment index and verify a relocatable standalone RLE package."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

from .bundle import verify_bundle


INDEX_SCHEMA = "material-counter.standalone-package.v1"
DISPLAY_MANIFEST = "export/display-manifest.json"
PACKAGE_SOURCE = "package-source.json"
REQUIRED_FILES = {
    DISPLAY_MANIFEST,
    PACKAGE_SOURCE,
    "boot-config.json",
    "board-runtime.json",
    "export/g2-integrated.bit",
    "export/g2-integrated.hwh",
    "native/rle_components_v2.so",
    "fonts/DroidSansFallbackFull.ttf",
    "fonts/DejaVuSans.ttf",
    "scripts/standalone/boot_integrated.py",
    "scripts/standalone/boot_policy.py",
    "scripts/standalone/bundle.py",
    "scripts/standalone/delivery.py",
    "scripts/standalone/evidence.py",
    "scripts/standalone/integrated_run.py",
    "scripts/standalone/runtime_environment.py",
}
PACKAGE_SOURCE_SCHEMA = "material-counter.offline-package-source.v1"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _valid_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("unsafe package path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (posix.is_absolute() or windows.is_absolute() or windows.drive
            or any(part in ("", ".", "..") for part in value.split("/"))
            or posix.as_posix() != value):
        raise ValueError(f"unsafe package path: {value}")
    return value


def _checked_file(root: Path, relative: str) -> Path:
    path = root.joinpath(*PurePosixPath(relative).parts)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"missing or unresolved package file: {relative}") from exc
    if not resolved.is_relative_to(root):
        raise ValueError(f"package path escapes root: {relative}")
    if not resolved.is_file():
        raise ValueError(f"package path is not a regular file: {relative}")
    return resolved


def _validate_elf_arm(path: Path) -> None:
    with path.open("rb") as stream:
        header = stream.read(20)
    if (len(header) < 20 or header[:4] != b"\x7fELF" or header[4] != 1
            or header[5] != 1 or struct.unpack_from("<H", header, 18)[0] != 40):
        raise ValueError("native library must be ELF32 little-endian ARM")


def _actual_files(root: Path) -> set[str]:
    result = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            children = directory.iterdir()
            for path in children:
                is_link = path.is_symlink() or getattr(path, "is_junction", lambda: False)()
                if is_link:
                    try:
                        target = path.resolve(strict=True)
                    except (OSError, RuntimeError) as exc:
                        raise ValueError(f"unresolved package symlink: {path}") from exc
                    if not target.is_relative_to(root):
                        raise ValueError(f"package symlink escapes root: {path}")
                    raise ValueError(f"package symlinks are not allowed: {path}")
                if path.is_dir():
                    pending.append(path)
                elif path.is_file():
                    result.add(path.relative_to(root).as_posix())
        except OSError as exc:
            raise ValueError(f"cannot inspect package directory: {directory}") from exc
    return result


def _validate_boot_config(root: Path, manifest_sha256: str) -> dict:
    config_path = _checked_file(root, "boot-config.json")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"),
                            object_pairs_hook=_unique_json_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid boot-config.json") from exc
    from .boot_policy import build_argv
    build_argv(root, config, "00000000-0000-0000-0000-000000000000",
               root / "candidate-session.json", manifest_sha256, sys.executable)
    return config


def _validate_package_source(checked: dict[str, Path]) -> None:
    source_path = checked[PACKAGE_SOURCE]
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"),
                            object_pairs_hook=_unique_json_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid package-source.json") from exc
    if not isinstance(source, dict) or source.get("schema") != PACKAGE_SOURCE_SCHEMA:
        raise ValueError("unknown package source schema")
    if source.get("status") != "CANDIDATE_NOT_BOARD_ACCEPTED":
        raise ValueError("package source must retain candidate acceptance status")
    if not isinstance(source.get("source_commit"), str) or not GIT_COMMIT_RE.fullmatch(
        source["source_commit"]
    ):
        raise ValueError("package source commit must be a full Git object ID")
    if not isinstance(source.get("source_branch"), str) or not source["source_branch"]:
        raise ValueError("package source branch is missing")

    raw_modules = source.get("module_files")
    if not isinstance(raw_modules, list):
        raise ValueError("package source module_files must be a list")
    modules = [_valid_relative_path(value) for value in raw_modules]
    if modules != sorted(set(modules)):
        raise ValueError("package source module_files must be unique and sorted")
    if any(not value.startswith("scripts/") or not value.endswith(".py")
           for value in modules):
        raise ValueError("package source module path is outside application scripts")
    packaged_modules = {
        path for path in checked
        if path.startswith("scripts/") and path.endswith(".py")
    }
    if set(modules) != packaged_modules:
        raise ValueError("package source module inventory differs from indexed Python files")


def verify_package(root, index_sha256):
    """Validate package contents using the caller-pinned deployment-index digest."""
    if not isinstance(index_sha256, str) or not SHA256_RE.fullmatch(index_sha256):
        raise ValueError("invalid deployment index SHA-256")
    try:
        package_root = Path(root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("package root does not exist") from exc
    if not package_root.is_dir():
        raise ValueError("package root must be a directory")

    index_path = package_root / "deployment-index.json"
    if index_path.is_symlink() or not index_path.is_file():
        raise ValueError("deployment index is missing or not a regular file")
    if _sha256(index_path) != index_sha256.lower():
        raise ValueError("deployment index hash mismatch")
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"),
                            object_pairs_hook=_unique_json_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid deployment index JSON") from exc
    if not isinstance(index, dict) or index.get("schema") != INDEX_SCHEMA:
        raise ValueError("unknown standalone package schema")
    declared = index.get("files")
    if not isinstance(declared, dict):
        raise ValueError("deployment index files must be an object")

    checked = {}
    resolved_targets = set()
    for raw_path, metadata in declared.items():
        relative = _valid_relative_path(raw_path)
        if relative == "deployment-index.json":
            raise ValueError("deployment index must not list itself")
        if relative in checked:
            raise ValueError(f"duplicate package path: {relative}")
        if not isinstance(metadata, dict) or set(metadata) != {"sha256", "bytes"}:
            raise ValueError(f"invalid file metadata: {relative}")
        digest = metadata["sha256"]
        size = metadata["bytes"]
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ValueError(f"invalid SHA-256 for {relative}")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError(f"invalid byte size for {relative}")
        path = _checked_file(package_root, relative)
        if path in resolved_targets:
            raise ValueError(f"duplicate resolved package path: {relative}")
        resolved_targets.add(path)
        if path.stat().st_size != size:
            raise ValueError(f"byte size mismatch for {relative}")
        if _sha256(path) != digest.lower():
            raise ValueError(f"file hash mismatch for {relative}")
        checked[relative] = path

    if not REQUIRED_FILES.issubset(checked):
        missing = sorted(REQUIRED_FILES - set(checked))
        raise ValueError(f"required package files missing: {missing}")
    if _actual_files(package_root) != set(checked) | {"deployment-index.json"}:
        raise ValueError("package file set contains missing or unexpected files")
    _validate_package_source(checked)

    manifest_path = checked[DISPLAY_MANIFEST]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"),
                              object_pairs_hook=_unique_json_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid display manifest JSON") from exc
    if manifest.get("rle_monitor") != {"abi": 1, "clock_hz": 50_000_000}:
        raise ValueError("unknown RLE monitor ABI or clock")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("display manifest runtime metadata is missing")
    for field, font_path in (
        ("font_sha256", "fonts/DroidSansFallbackFull.ttf"),
        ("latin_font_sha256", "fonts/DejaVuSans.ttf"),
    ):
        expected = runtime.get(field)
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            raise ValueError(f"invalid runtime {field}")
        if _sha256(checked[font_path]) != expected.lower():
            raise ValueError(f"runtime font hash mismatch: {field}")

    sources = runtime.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("runtime source lock must be a non-empty list")
    source_paths = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"path", "sha256"}:
            raise ValueError("invalid runtime source lock entry")
        source_relative = _valid_relative_path(source["path"])
        if source_relative in source_paths:
            raise ValueError(f"duplicate runtime source path: {source_relative}")
        source_paths.add(source_relative)
        packaged_relative = f"runtime/pynq/{source_relative}"
        packaged = checked.get(packaged_relative)
        expected = source["sha256"]
        if packaged is None or not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            raise ValueError(f"runtime source is missing or has invalid hash: {source_relative}")
        if _sha256(packaged) != expected.lower():
            raise ValueError(f"runtime source hash mismatch: {source_relative}")

    _validate_boot_config(package_root, declared[DISPLAY_MANIFEST]["sha256"])
    _validate_elf_arm(checked["native/rle_components_v2.so"])
    verify_bundle(manifest_path, declared[DISPLAY_MANIFEST]["sha256"])
    return {
        "status": "PASS",
        "files_checked": len(checked) + 1,
        "hardware_actions": 0,
        "scope": "integrity_only_no_gate",
    }


def build_deployment_index(root):
    """Create and verify an index for an already assembled offline package.

    This does not assemble or install dependencies and does not perform board
    actions. The package must include a valid boot-config.json; the resulting
    digest must be pinned separately by the deployment environment.
    """
    try:
        package_root = Path(root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("package root does not exist") from exc
    if not package_root.is_dir():
        raise ValueError("package root must be a directory")

    index_path = package_root / "deployment-index.json"
    if index_path.is_symlink() or index_path.exists():
        raise ValueError("refusing to overwrite deployment-index.json")

    actual = _actual_files(package_root)
    missing = sorted(REQUIRED_FILES - actual)
    if missing:
        raise ValueError(f"required package files missing: {missing}")

    manifest_path = _checked_file(package_root, DISPLAY_MANIFEST)
    manifest_digest = _sha256(manifest_path)
    _validate_boot_config(package_root, manifest_digest)

    declared = {}
    for relative in sorted(actual):
        relative = _valid_relative_path(relative)
        path = _checked_file(package_root, relative)
        declared[relative] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    payload = json.dumps({"schema": INDEX_SCHEMA, "files": declared},
                         sort_keys=True, separators=(",", ":")) + "\n"

    created = False
    try:
        with index_path.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            stream.write(payload)
            stream.flush()
        digest = _sha256(index_path)
        verified = verify_package(package_root, digest)
    except BaseException:
        if created:
            try:
                index_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return {
        "status": "INDEX_BUILT_CANDIDATE",
        "index_sha256": digest,
        "files_indexed": len(declared),
        "hardware_actions": 0,
        "scope": "offline_package_preparation_only",
        "verification": verified,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build an offline package index or verify a pinned package")
    parser.add_argument("--root", required=True, type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--index-sha256")
    action.add_argument("--build-index", action="store_true",
                        help="create and verify deployment-index.json in an assembled package")
    args = parser.parse_args(argv)
    result = (build_deployment_index(args.root) if args.build_index
              else verify_package(args.root, args.index_sha256))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
