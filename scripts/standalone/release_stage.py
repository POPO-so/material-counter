"""Stage a verified standalone package into a new local directory, without activating it."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
from pathlib import Path

from .delivery import verify_package


def _reject_linked_path(path: Path) -> Path:
    """Resolve an existing path while rejecting symlink/junction components."""
    absolute = Path(path.absolute())
    chain = list(reversed((absolute, *absolute.parents)))
    for item in chain:
        try:
            mode = item.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode) or getattr(item, "is_junction", lambda: False)():
            raise ValueError(f"symlink or junction path component is not allowed: {item}")
    return absolute.resolve(strict=False)


def _inventory(root: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for child in sorted(directory.iterdir(), key=lambda path: path.name):
            mode = child.lstat().st_mode
            if stat.S_ISLNK(mode) or getattr(child, "is_junction", lambda: False)():
                raise ValueError(f"package links are not allowed: {child}")
            if stat.S_ISDIR(mode):
                pending.append(child)
            elif stat.S_ISREG(mode):
                files.append((child.relative_to(root).as_posix(), child))
            else:
                raise ValueError(f"package contains a non-regular entry: {child}")
    return sorted(files)


def _tree_sha256(root: Path, inventory: list[tuple[str, Path]]) -> str:
    digest = hashlib.sha256()
    for relative, path in inventory:
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def stage_package(root: Path, pinned_index_sha: str, destination: Path) -> dict:
    source_path = _reject_linked_path(root)
    target_input = Path(destination).absolute()
    parent = _reject_linked_path(target_input.parent)
    target_path = parent / target_input.name
    if not source_path.is_dir():
        raise ValueError("package root must be a directory")
    if not parent.is_dir():
        raise ValueError("destination parent must already exist")
    if source_path == target_path or source_path in target_path.parents or target_path in source_path.parents:
        raise ValueError("source and destination must not contain one another")
    if target_path.exists() or target_path.is_symlink() or getattr(target_path, "is_junction", lambda: False)():
        raise ValueError("destination already exists")

    verify_package(source_path, pinned_index_sha)
    source_files = _inventory(source_path)
    created_dirs: list[Path] = []
    created_files: list[Path] = []
    try:
        target_path.mkdir()
        created_dirs.append(target_path)
        for relative, source_file in source_files:
            target_file = target_path / relative
            directory = target_file.parent
            missing = []
            while directory != target_path and not directory.exists():
                missing.append(directory)
                directory = directory.parent
            for new_dir in reversed(missing):
                new_dir.mkdir()
                created_dirs.append(new_dir)
            with source_file.open("rb") as source_stream, target_file.open("xb") as target_stream:
                created_files.append(target_file)
                shutil.copyfileobj(source_stream, target_stream, 1024 * 1024)

        verify_package(target_path, pinned_index_sha)
        staged_inventory = _inventory(target_path)
        if [name for name, _ in source_files] != [name for name, _ in staged_inventory]:
            raise ValueError("staged package file set differs from source")
        return {
            "status": "STAGED_ONLY",
            "G4_release": False,
            "destination": str(target_path),
            "files_staged": len(staged_inventory),
            "deployment_index_sha256": pinned_index_sha.lower(),
            "tree_sha256": _tree_sha256(target_path, staged_inventory),
            "verification": "source_and_staged_package_verified",
            "hardware_actions": 0,
        }
    except BaseException:
        for created_file in reversed(created_files):
            try:
                created_file.unlink(missing_ok=True)
            except OSError:
                pass
        for created_dir in reversed(created_dirs):
            try:
                created_dir.rmdir()
            except OSError:
                pass
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Stage a verified package locally; does not activate G4")
    parser.add_argument("--root", required=True, type=Path, help="already assembled package root")
    parser.add_argument("--index-sha256", required=True, help="caller-pinned deployment index SHA-256")
    parser.add_argument("--destination", required=True, type=Path, help="new destination directory (must not exist)")
    args = parser.parse_args(argv)
    result = stage_package(args.root, args.index_sha256, args.destination)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
