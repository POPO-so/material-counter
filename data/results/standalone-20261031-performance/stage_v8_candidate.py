"""Prepare a v8 package candidate locally from an inspected v7 package mirror.

This script does not connect to the board, install files, or activate a release.
Run only after the old v7 package and its candidate environment file have been
inspected and the Manager authorizes staging. The resulting local package can
then be uploaded to the agreed new release path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile


SOURCE_COMMIT = "e2fb5952bfc4f36c3eacd53a8bdb6bdeec6c4fe2"
SOURCE_BRANCH = "perf/board-performance-20261031"
EXPECTED_OLD_INDEX_SHA256 = "ca9348788e98efe187752a29dcbe3ddc0970efa8ff85a1f8e43bbee9142ae58a"
EXPECTED_MODULE_COUNT = 43
SOURCE_ARCHIVE_NAME = "v8-source-modules-e2fb595.tar.gz"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_source_bundle(archive_path: Path, correspondence_path: Path) -> tuple[dict, dict[str, bytes]]:
    declaration = json.loads(correspondence_path.read_text(encoding="utf-8"))
    if (declaration.get("source_commit") != SOURCE_COMMIT
            or declaration.get("source_branch") != SOURCE_BRANCH
            or declaration.get("module_count") != EXPECTED_MODULE_COUNT):
        raise ValueError("Git source bundle identity differs from the pinned release source")
    if _sha256(archive_path) != declaration.get("archive_sha256"):
        raise ValueError("Git source archive SHA-256 mismatch")
    modules: dict[str, bytes] = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) != EXPECTED_MODULE_COUNT:
            raise ValueError("Git source archive must contain exactly 43 modules")
        for member in members:
            name = member.name
            if (not member.isfile() or not name.startswith("scripts/")
                    or not name.endswith(".py") or ".." in PurePosixPath(name).parts
                    or name in modules):
                raise ValueError(f"unsafe or duplicate source archive entry: {name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"cannot read source archive entry: {name}")
            content = stream.read()
            modules[name] = content
    expected = {item["path"]: item for item in declaration["modules"]}
    if set(modules) != set(expected):
        raise ValueError("source archive paths differ from its correspondence manifest")
    for name, content in modules.items():
        if hashlib.sha256(content).hexdigest() != expected[name]["sha256"]:
            raise ValueError(f"source archive module hash mismatch: {name}")
    return declaration, modules


def _verify_package(root: Path) -> dict:
    index = root / "deployment-index.json"
    if not index.is_file():
        raise ValueError("package has no deployment-index.json")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "scripts.standalone.delivery",
         "--root", str(root), "--index-sha256", _sha256(index)],
        cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    return json.loads(result.stdout)


def _inventory(root: Path) -> dict[str, str]:
    values = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"package symlink is not allowed: {path}")
        if path.is_file():
            values[path.relative_to(root).as_posix()] = _sha256(path)
    return values


def _write_module(root: Path, relative: str, content: bytes) -> None:
    parts = PurePosixPath(relative).parts
    if (not relative.startswith("scripts/") or not relative.endswith(".py")
            or any(part in ("", ".", "..") for part in parts)):
        raise ValueError(f"invalid package module path: {relative}")
    destination = root.joinpath(*parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def _update_env_candidate(path: Path | None, package_root: Path,
                          index_sha256: str) -> dict | None:
    if path is None:
        return None
    if path.resolve(strict=False).is_relative_to(package_root.resolve()):
        raise ValueError("environment candidate must remain outside the indexed package")
    if path.exists() or path.is_symlink():
        raise ValueError("candidate environment output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(f"PACKAGE_ROOT={package_root}\n")
        stream.write(f"PACKAGE_INDEX_SHA256={index_sha256}\n")
        stream.flush()
    return {"path": str(path), "sha256": _sha256(path),
            "package_root": str(package_root), "package_index_sha256": index_sha256,
            "created": True}


def stage(base_root: Path, destination: Path, archive_path: Path,
          correspondence_path: Path, report_path: Path,
          env_candidate: Path | None = None) -> dict:
    base_root = base_root.resolve(strict=True)
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("candidate destination already exists")
    if destination == base_root or base_root in destination.parents or destination in base_root.parents:
        raise ValueError("candidate and base package must not contain one another")

    old_index_sha256 = _sha256(base_root / "deployment-index.json")
    if old_index_sha256 != EXPECTED_OLD_INDEX_SHA256:
        raise ValueError("old v7 index does not match the Manager-pinned trust baseline")
    if env_candidate is None:
        env_candidate = destination.parent / (destination.name + ".package.env.candidate")
    if env_candidate.exists() or env_candidate.is_symlink():
        raise ValueError("candidate environment output already exists")
    old_integrity = _verify_package(base_root)
    old_inventory = _inventory(base_root)
    if "package.env.candidate" in old_inventory:
        raise ValueError("old package contains an indexed env candidate; manager review required")
    old_source_path = base_root / "package-source.json"
    old_source = json.loads(old_source_path.read_text(encoding="utf-8"))
    old_modules = old_source.get("module_files")
    if not isinstance(old_modules, list) or old_modules != sorted(set(old_modules)):
        raise ValueError("old package source module inventory is invalid")
    if len(old_modules) != 42:
        raise ValueError("old v7 package does not declare exactly 42 modules")

    declaration, modules = _load_source_bundle(archive_path, correspondence_path)
    new_module_paths = sorted(modules)
    if set(new_module_paths) != set(old_modules) | {"scripts/standalone/performance_replay.py"}:
        raise ValueError("new source set is not the old 42 modules plus performance_replay.py")

    shutil.copytree(base_root, destination, symlinks=False)
    before_static = {path: digest for path, digest in old_inventory.items()
                      if path not in old_modules and path not in
                      ("package-source.json", "deployment-index.json")}
    for path, content in modules.items():
        _write_module(destination, path, content)

    package_source_path = destination / "package-source.json"
    package_source = json.loads(package_source_path.read_text(encoding="utf-8"))
    package_source.update({
        "source_commit": SOURCE_COMMIT,
        "source_branch": SOURCE_BRANCH,
        "module_files": new_module_paths,
    })
    package_source_path.write_text(
        json.dumps(package_source, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    (destination / "deployment-index.json").unlink()
    build = subprocess.run(
        [sys.executable, "-B", "-m", "scripts.standalone.delivery",
         "--root", str(destination), "--build-index"],
        cwd=destination, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    build_result = json.loads(build.stdout)
    new_index_sha = _sha256(destination / "deployment-index.json")
    if build_result.get("index_sha256") != new_index_sha:
        raise ValueError("delivery index builder returned a different index SHA-256")
    new_integrity = _verify_package(destination)

    new_inventory = _inventory(destination)
    after_static = {path: digest for path, digest in new_inventory.items()
                    if path not in new_module_paths and path not in
                    ("package-source.json", "deployment-index.json")}
    if before_static != after_static:
        raise ValueError("non-source package content changed during candidate staging")
    module_map = []
    for item in declaration["modules"]:
        packaged = destination / item["path"]
        actual_sha = _sha256(packaged)
        if actual_sha != item["sha256"]:
            raise ValueError(f"packaged module differs from pinned Git bytes: {item['path']}")
        module_map.append({**item, "package_sha256": actual_sha,
                           "byte_match": True})

    env_report = _update_env_candidate(env_candidate, destination, new_index_sha)
    report = {
        "schema": "material-counter.v8-package-integrity.v1",
        "status": "PASS_CANDIDATE_NOT_ACTIVATED",
        "source_commit": SOURCE_COMMIT,
        "source_branch": SOURCE_BRANCH,
        "destination": str(destination),
        "old_package": {"root": str(base_root), "index_sha256": old_index_sha256,
                        "integrity": old_integrity},
        "new_package": {"index_sha256": new_index_sha, "integrity": new_integrity,
                        "file_count": len(new_inventory)},
        "source_bundle_sha256": declaration["archive_sha256"],
        "modules": module_map,
        "non_source_files_identical": True,
        "env_candidate": env_report,
        "hardware_actions": 0,
        "activated": False,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-package", required=True, type=Path,
                        help="local, inspected mirror of the old v7 package")
    parser.add_argument("--destination", required=True, type=Path,
                        help="new local candidate directory; must not exist")
    parser.add_argument("--source-archive", type=Path,
                        default=Path(__file__).resolve().parent / SOURCE_ARCHIVE_NAME)
    parser.add_argument("--source-map", type=Path,
                        default=Path(__file__).resolve().parent / "v8-source-module-correspondence.json")
    parser.add_argument("--report", type=Path,
                        default=Path(__file__).resolve().parent / "package-integrity.json")
    parser.add_argument("--env-candidate", type=Path,
                        help="inspected environment candidate file outside the package root")
    args = parser.parse_args()
    result = stage(args.base_package, args.destination, args.source_archive,
                   args.source_map, args.report, args.env_candidate)
    print(json.dumps({"status": result["status"],
                      "destination": result["destination"],
                      "index_sha256": result["new_package"]["index_sha256"],
                      "module_count": len(result["modules"]),
                      "activated": result["activated"]}, sort_keys=True))


if __name__ == "__main__":
    main()
