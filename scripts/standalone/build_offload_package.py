"""Assemble a new, integrity-checked offline PS/PL offload candidate."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile

from .bundle import verify_bundle, verify_offload_capabilities
from .delivery import (PACKAGE_SOURCE, PACKAGE_SOURCE_SCHEMA,
                       _checked_file, _valid_relative_path,
                       build_deployment_index, verify_package)


OLD_BIT_SHA256 = "79609f9a60c3e727b5f2f54a8d639a464a098715fb5eb28d294646a9735948e0"
INITIAL_OFFLOAD_MODULES = (
    "scripts/standalone/button_events.py",
    "scripts/standalone/exposure.py",
    "scripts/standalone/runtime_metrics.py",
    "scripts/standalone/diagnostic_mode.py",
    "scripts/standalone/pl_preview.py",
)
STATIC_PREFIXES = ("fonts/", "native/", "runtime/", "lib/", "libs/")
STATIC_ROOT_FILES = {"board-runtime.json", "boot-config.json"}
COMMIT_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob(source_root: Path, commit: str, relative: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), "cat-file", "blob", f"{commit}:{relative}"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"cannot read fixed-commit source blob: {relative}") from exc
    return result.stdout


def _module_imports(relative: str, data: bytes) -> set[str]:
    try:
        tree = ast.parse(data, filename=relative)
    except (SyntaxError, UnicodeError) as exc:
        raise ValueError(f"source module cannot be parsed: {relative}") from exc
    parts = list(PurePosixPath(relative).with_suffix("").parts)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "scripts" or alias.name.startswith("scripts."):
                    found.add(alias.name.replace(".", "/") + ".py")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = parts[:-1]
                if node.level > len(base):
                    continue
                base = base[:len(base) - node.level + 1]
                if node.module:
                    base.extend(node.module.split("."))
            else:
                base = node.module.split(".") if node.module else []
            if base and base[0] == "scripts":
                found.add("/".join(base) + ".py")
            elif node.level and base and base[0] == "scripts":
                found.add("/".join(base) + ".py")
            if node.level and (not base or base[0] != "scripts"):
                package = base
                for alias in node.names:
                    if alias.name != "*":
                        found.add("/".join(package + [alias.name]) + ".py")
    return {path for path in found if path.startswith("scripts/")}


def _source_modules(source_root: Path, commit: str, initial) -> dict[str, bytes]:
    modules = {}
    pending = list(initial)
    while pending:
        relative = _valid_relative_path(pending.pop())
        if not relative.startswith("scripts/") or not relative.endswith(".py"):
            raise ValueError(f"invalid application module path: {relative}")
        if relative in modules:
            continue
        data = _git_blob(source_root, commit, relative)
        modules[relative] = data
        for imported in _module_imports(relative, data):
            # Some imported names are package attributes rather than modules;
            # include only paths that exist as blobs at the pinned commit.
            try:
                _git_blob(source_root, commit, imported)
            except ValueError:
                continue
            if imported not in modules:
                pending.append(imported)
    return dict(sorted(modules.items()))


def _source_branch(source_root: Path, commit: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), "branch", "--contains", commit,
             "--format=%(refname:short)"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, shell=False, timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "fixed-commit"
    branches = sorted(line.strip() for line in result.stdout.splitlines() if line.strip())
    return branches[0] if branches else "fixed-commit"


def _copy_indexed_static_files(baseline: Path, destination: Path) -> None:
    try:
        index = json.loads((baseline / "deployment-index.json").read_text(encoding="utf-8"))
        files = index["files"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("trusted baseline index cannot be read") from exc
    selected = []
    for relative in files:
        if relative in STATIC_ROOT_FILES or relative.startswith(STATIC_PREFIXES):
            parts = PurePosixPath(relative).parts
            lowered = relative.lower()
            if (any(part.lower() in {"cache", "caches", "__pycache__"} for part in parts)
                    or lowered.endswith((".pyc", ".pyo", ".xclbin", ".bit.bin"))):
                raise ValueError(f"baseline static allowlist contains forbidden hardware image: {relative}")
            selected.append(_valid_relative_path(relative))
    for relative in selected:
        source = _checked_file(baseline, relative)
        target = destination.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    if "boot-config.json" not in selected:
        raise ValueError("baseline boot-config.json is not in the trusted package index")


def _load_hardware_manifest(hardware_export: Path):
    manifest_path = hardware_export / "display-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("new display-manifest.json is missing or invalid") from exc
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    files = verify_bundle(manifest_path, digest)
    if manifest.get("ready_for_board") is not True:
        raise ValueError("new hardware manifest is not Manager-ready")
    verify_offload_capabilities(manifest, files["hwh"])
    bit_digest = _sha256_bytes(files["bit"].read_bytes())
    if bit_digest == OLD_BIT_SHA256:
        raise ValueError("new offload BIT matches the prohibited old G2 image")
    return manifest, files


def build_offload_package(*, baseline_root, baseline_index_sha256,
                          source_root, source_commit, hardware_export, output):
    """Build into a new directory, pinned to baseline index and source commit."""
    if not isinstance(baseline_index_sha256, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", baseline_index_sha256
    ):
        raise ValueError("an explicit trusted baseline index SHA-256 is required")
    if not isinstance(source_commit, str) or not COMMIT_RE.fullmatch(source_commit):
        raise ValueError("source commit must be a full Git object ID")

    baseline = Path(baseline_root).resolve(strict=True)
    source = Path(source_root).resolve(strict=True)
    hardware = Path(hardware_export).resolve(strict=True)
    if not baseline.is_dir() or not source.is_dir() or not hardware.is_dir():
        raise ValueError("baseline, source and hardware inputs must be directories")
    verify_package(baseline, baseline_index_sha256)

    try:
        source_declaration = json.loads(
            (baseline / PACKAGE_SOURCE).read_text(encoding="utf-8"))
        baseline_modules = source_declaration["module_files"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("baseline package-source module inventory is invalid") from exc
    if (not isinstance(source_declaration, dict)
            or source_declaration.get("schema") != PACKAGE_SOURCE_SCHEMA
            or not isinstance(baseline_modules, list)):
        raise ValueError("baseline package-source declaration is invalid")
    modules = _source_modules(source, source_commit,
                              [*baseline_modules, *INITIAL_OFFLOAD_MODULES])

    hardware_manifest, hardware_files = _load_hardware_manifest(hardware)
    output_path = Path(output).absolute()
    if output_path.exists() or output_path.is_symlink():
        raise ValueError("refusing to overwrite existing output path")
    parent = output_path.parent.resolve(strict=True)
    output_path = parent / output_path.name
    if output_path == baseline or baseline in output_path.parents:
        raise ValueError("candidate output must be outside the trusted baseline")
    for input_root in (source, hardware):
        if output_path == input_root or input_root in output_path.parents:
            raise ValueError("candidate output must be outside its input directories")

    temporary = Path(tempfile.mkdtemp(prefix=f".{output_path.name}.building-", dir=parent))
    try:
        _copy_indexed_static_files(baseline, temporary)

        export = temporary / "export"
        export.mkdir(parents=True, exist_ok=True)
        bit_target = export / "g2-integrated.bit"
        hwh_target = export / "g2-integrated.hwh"
        shutil.copyfile(hardware_files["bit"], bit_target)
        shutil.copyfile(hardware_files["hwh"], hwh_target)
        hardware_manifest["files"]["bit"]["path"] = bit_target.name
        hardware_manifest["files"]["hwh"]["path"] = hwh_target.name
        manifest_target = export / "display-manifest.json"
        manifest_target.write_text(
            json.dumps(hardware_manifest, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n",
            encoding="utf-8", newline="\n")

        for relative, data in modules.items():
            target = temporary.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        source_payload = {
            "schema": PACKAGE_SOURCE_SCHEMA,
            "status": "CANDIDATE_NOT_BOARD_ACCEPTED",
            "source_commit": source_commit,
            "source_branch": _source_branch(source, source_commit),
            "module_files": sorted(modules),
        }
        (temporary / PACKAGE_SOURCE).write_text(
            json.dumps(source_payload, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n",
            encoding="utf-8", newline="\n")

        built = build_deployment_index(temporary)
        from scripts.delivery.verify_standalone_source import verify_source
        source_verification = verify_source(temporary, source, built["index_sha256"])
        if source_verification["source_commit"] != source_commit:
            raise ValueError("candidate source verification returned a different commit")
        if output_path.exists() or output_path.is_symlink():
            raise ValueError("refusing to overwrite output path created during build")
        os.rename(temporary, output_path)
        return {
            "status": "OFFLINE_CANDIDATE_BUILT",
            "output": str(output_path),
            "baseline_index_sha256": baseline_index_sha256.lower(),
            "source_commit": source_commit,
            "index_sha256": built["index_sha256"],
            "files_indexed": built["files_indexed"],
            "source_modules_verified": source_verification["module_count"],
            "verification": verify_package(output_path, built["index_sha256"]),
            "hardware_actions": 0,
        }
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build a local offline PS/PL candidate package")
    parser.add_argument("--baseline-root", required=True, type=Path)
    parser.add_argument("--baseline-index-sha256", required=True)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--hardware-export", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = build_offload_package(
            baseline_root=args.baseline_root,
            baseline_index_sha256=args.baseline_index_sha256,
            source_root=args.source_root,
            source_commit=args.source_commit,
            hardware_export=args.hardware_export,
            output=args.output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
