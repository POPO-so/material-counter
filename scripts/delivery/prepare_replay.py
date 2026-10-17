"""Prepare a relocatable, offline WO-0014 replay package.

The script binds vectors and replay Tcl to the current checkout's sources. It
rebuilds the fixed H3 input set under --out, so no PNGs are written into the
checkout and no old result directory is required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


SOURCE_FILES = (
    "fpga/rtl/axis_rgb2gray.v",
    "fpga/rtl/axis_gray2binary.v",
    "fpga/experimental/wo0014/axis_dma_rgb_adapter.v",
    "fpga/experimental/wo0014/axis_binary_output_checker.v",
    "fpga/experimental/wo0014/axis_dma_image_pipeline.v",
    "fpga/sim/wo0014/tb_axis_dma_image_pipeline.sv",
    "python/refmodel/experiments/wo0014/image_vectors.py",
    "python/refmodel/experiments/wo0014/compare_output.py",
    "python/synth/generate.py",
    "python/synth/scene.py",
    "python/synth/specs/h3_matrix.json",
    "scripts/wo0014/run_full.tcl",
    "scripts/wo0014/run_directed.tcl",
    "scripts/wo0014/run_negative.tcl",
    "scripts/delivery/prepare_replay.py",
    "scripts/delivery/REPLAY.md",
)

OLD_ROOT = "C:/fpga/material-counter"
OLD_RESULT = OLD_ROOT + "/data/results/wo0014-offline-20261014"
ROOT_ASSIGNMENT = f"set root {OLD_ROOT}"
RESULT_ASSIGNMENT = "set result [file join $root data results wo0014-offline-20261014]"
SOURCE_LINES = {
    "source [file join $root scripts wo0014 run_negative.tcl]":
        "source [file join $script_dir run_negative.tcl]",
    "source [file join $root scripts wo0014 run_directed.tcl]":
        "source [file join $script_dir run_directed.tcl]",
}
TCL_PREAMBLE = """# Generated relocation wrapper. Offline simulation only.
set script_dir [file dirname [file normalize [info script]]]
if {![info exists ::env(MATERIAL_COUNTER_ROOT)]} {
    error {Set MATERIAL_COUNTER_ROOT to the checkout used to prepare this package}
}
set root [file normalize $::env(MATERIAL_COUNTER_ROOT)]
set result $script_dir
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checkout_root() -> Path:
    return Path(__file__).resolve().parents[2]


def validate_output_dir(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError(f"--out must not be a symlink: {path}")
    if path.exists():
        if not path.is_dir():
            raise ValueError(f"--out exists and is not a directory: {path}")
        if next(path.iterdir(), None) is not None:
            raise ValueError(f"--out must be missing or empty: {path}")
    else:
        path.mkdir(parents=True)
    return path.resolve()


def _read_fixed_spec(root: Path) -> dict[str, Any]:
    spec_path = root / "python/synth/specs/h3_matrix.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    conditions = spec.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise ValueError(f"invalid fixed H3 spec: {spec_path}")
    frame_total = sum(
        int(spec["num_frames"]) - len(condition.get("drop_frame_ids", []))
        for condition in conditions
    )
    if frame_total != 487:
        raise ValueError(f"fixed H3 spec must produce 487 frames, got {frame_total}")
    return spec


def _tcl_preamble() -> str:
    return TCL_PREAMBLE


def relocate_tcl(source: Path, destination: Path, *, full: bool = False) -> None:
    text = source.read_text(encoding="utf-8")
    lines = text.splitlines()
    if full:
        if lines.count(ROOT_ASSIGNMENT) != 1 or lines.count(RESULT_ASSIGNMENT) != 1:
            raise ValueError(f"unexpected root/result setup in {source}")
        for original, relocated in SOURCE_LINES.items():
            if lines.count(original) != 1:
                raise ValueError(f"expected one source line {original!r} in {source}")
            lines[lines.index(original)] = relocated
        lines = [line for line in lines if line not in (ROOT_ASSIGNMENT, RESULT_ASSIGNMENT)]

    text = "\n".join(lines) + "\n"
    # Replace the longer result prefix before its checkout-root prefix.
    if OLD_RESULT in text:
        text = text.replace(OLD_RESULT, "$result")
    if OLD_ROOT in text:
        text = text.replace(OLD_ROOT, "$root")
    if OLD_ROOT in text or OLD_RESULT in text:
        raise ValueError(f"legacy absolute path remains after relocation: {source}")
    if "source [file join $root scripts wo0014 run_" in text:
        raise ValueError(f"source child Tcl still points at the checkout: {source}")
    destination.write_text(_tcl_preamble() + text, encoding="utf-8", newline="\n")


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _source_hashes(root: Path) -> list[dict[str, str]]:
    records = []
    for relative in SOURCE_FILES:
        path = root / Path(relative)
        if not path.is_file():
            raise FileNotFoundError(f"required WO-0014 source is missing: {relative}")
        records.append({"path": relative, "sha256": sha256(path)})
    return records


def _frame_files(h3_root: Path) -> dict[str, Path]:
    return {
        path.relative_to(h3_root).as_posix(): path
        for path in sorted(h3_root.glob("*/frames/frame_*.png"))
    }


def _compare_reference_frames(generated: dict[str, Path], reference_root: Path | None) -> dict[str, Any]:
    if reference_root is None:
        return {"status": "NOT_REQUESTED", "compared_frames": 0, "matches": 0}
    if not reference_root.is_dir():
        raise ValueError(f"--reference-h3-root is not a directory: {reference_root}")
    reference = _frame_files(reference_root.resolve())
    generated_keys = set(generated)
    reference_keys = set(reference)
    common_keys = generated_keys & reference_keys

    frames = []
    matches = 0
    for key in sorted(common_keys):
        generated_hash = sha256(generated[key])
        reference_hash = sha256(reference[key])
        is_match = generated_hash == reference_hash
        matches += int(is_match)
        frames.append({
            "key": key,
            "generated_sha256": generated_hash,
            "reference_sha256": reference_hash,
            "match": is_match,
        })
    return {
        "status": "MATCH" if generated_keys == reference_keys and matches == len(frames) else "MISMATCH",
        "compared_frames": len(frames),
        "matches": matches,
        "missing_reference_keys": sorted(generated_keys - reference_keys),
        "extra_reference_keys": sorted(reference_keys - generated_keys),
        "frames": frames,
    }


def _all_file_hashes(root: Path) -> list[dict[str, str]]:
    return [
        {"path": path.relative_to(root).as_posix(), "sha256": sha256(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def prepare(output: Path, reference_h3_root: Path | None = None) -> dict[str, Any]:
    # Imports below use the checkout's source tree; keep bytecode out of it.
    sys.dont_write_bytecode = True
    root = checkout_root()
    _read_fixed_spec(root)
    source_hashes = _source_hashes(root)
    if reference_h3_root is not None:
        reference_h3_root = reference_h3_root.resolve(strict=True)
        reference_files = _frame_files(reference_h3_root)
        if len(reference_files) != 487:
            raise ValueError(
                f"--reference-h3-root must contain 487 Gray8 frames, found {len(reference_files)}"
            )

    output = validate_output_dir(output)
    dataset_root = output / "h3-source"
    h3_root = dataset_root / "data/synthetic/h3_full"
    replay_root = output / "replay"
    replay_root.mkdir()

    sys.path.insert(0, str(root / "python"))
    from synth.generate import generate_dataset

    refmodel_dir = root / "python/refmodel/experiments/wo0014"
    sys.path.insert(0, str(refmodel_dir))
    import image_vectors

    spec_path = root / "python/synth/specs/h3_matrix.json"
    generated_config = generate_dataset(spec_path, h3_root)
    if generated_config["dataset_id"] != "h3_matrix":
        raise ValueError("fixed spec generated an unexpected dataset id")

    # image_vectors records paths relative to ROOT; point it at the packaged
    # dataset root so its manifest is portable and compare_output can re-read it.
    image_vectors.ROOT = dataset_root.resolve()
    image_vectors.H3_ROOT = h3_root.resolve()
    vectors_root = replay_root / "vectors"
    vector_manifest = image_vectors.generate(vectors_root, threshold=128, h3_root=h3_root)
    if vector_manifest["h3"]["frames"] != 487:
        raise ValueError("vector generation did not produce the complete 487-frame H3 set")

    for name, is_full in (("run_full.tcl", True), ("run_directed.tcl", False),
                          ("run_negative.tcl", False)):
        relocate_tcl(root / "scripts/wo0014" / name, replay_root / name, full=is_full)

    source_manifest: dict[str, Any] = {
        "schema": 1,
        "purpose": "offline WO-0014 replay source and output SHA-256 manifest",
        "checkout_head": _git_head(root),
        "checkout_root_binding": "Set MATERIAL_COUNTER_ROOT to this checkout before Vivado run",
        "source_files": source_hashes,
        "dataset": {
            "spec": "python/synth/specs/h3_matrix.json",
            "generated_root": "h3-source/data/synthetic/h3_full",
            "dataset_id": generated_config["dataset_id"],
            "condition_count": len(generated_config["conditions"]),
            "frame_count": vector_manifest["h3"]["frames"],
            "frame_key_root": "relative to generated_root",
            "reference_comparison": _compare_reference_frames(
                _frame_files(h3_root), reference_h3_root
            ),
        },
        "python_environment": {
            "python": platform.python_version(),
        },
        "generated_files": _all_file_hashes(output),
        "manifest_note": "This file is excluded from generated_files to avoid a self-hash.",
    }
    try:
        import numpy
        from PIL import __version__ as pillow_version

        source_manifest["python_environment"].update({
            "numpy": numpy.__version__,
            "pillow": pillow_version,
        })
    except ImportError:
        pass

    manifest_path = replay_root / "source_sha256.json"
    manifest_path.write_text(
        json.dumps(source_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return source_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path,
                        help="missing or empty destination directory")
    parser.add_argument("--reference-h3-root", type=Path,
                        help="optional existing 487-frame source tree to SHA-compare")
    args = parser.parse_args()
    try:
        manifest = prepare(args.out, args.reference_h3_root)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    comparison = manifest["dataset"]["reference_comparison"]
    print(json.dumps({
        "output": str(args.out.resolve()),
        "h3_frames": manifest["dataset"]["frame_count"],
        "source_files": len(manifest["source_files"]),
        "reference_comparison": comparison["status"],
        "reference_matches": comparison["matches"],
    }, indent=2))
    return 0 if comparison["status"] != "MISMATCH" else 2


if __name__ == "__main__":
    raise SystemExit(main())
