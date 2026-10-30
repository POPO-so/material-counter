#!/usr/bin/env python3
"""Reproducibly compare fixed scaling candidates with the current local G3 source."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import platform
import subprocess
import sys
from pathlib import Path
from uuid import UUID


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TARGET_WIDTH = 576
TARGET_HEIGHT = 360
TARGET_PIXELS = TARGET_WIDTH * TARGET_HEIGHT
CANVAS_X = 32
LINE_X = 320
SESSION_ID = UUID("00000000-0000-4000-8000-000000000001")
FRAME_ID = 1
FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")
VECTORS = (
    ("constant_gray_73", "constant-gray-576x360.raw", "constant-gray-73.raw", "gray"),
    ("gray_ramp_inter_area", "gray-ramp-inter-area-576x360.raw", "gray-ramp-12.raw", "gray"),
    ("binary_nearest_rectangle_and_boundary_points", "binary-inter-nearest-576x360.raw",
     "binary-nearest-rect-points.raw", "binary"),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes())


def require_file(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"Required file is missing: {path}")


def runner_composer_call_counts(path: Path) -> tuple[int, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    total = 0
    area = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "GrayComposer"):
            continue
        total += 1
        for keyword in node.keywords:
            value = keyword.value
            if (keyword.arg == "gray_interpolation" and isinstance(value, ast.Attribute)
                    and value.attr == "INTER_AREA" and isinstance(value.value, ast.Name)
                    and value.value.id == "cv2"):
                area += 1
    return total, area


def package_version(module, name: str) -> str:
    return str(getattr(module, "__version__", "unknown"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="New directory for JSON report and actual raw/PNG files")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(f"Refusing to overwrite existing output directory: {output_dir}")

    source_manifest_path = HERE / "scaling-candidate-vectors.json"
    compositor_path = ROOT / "scripts/standalone/compositor.py"
    runner_path = ROOT / "scripts/standalone/integrated_run.py"
    for required in (source_manifest_path, compositor_path, runner_path, FONT_PATH):
        require_file(required)

    # Check all source and expected artifacts before importing/rendering anything.
    manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_by_name = {Path(item["path"]).name: item for item in manifest["source_artifacts"]}
    expected_by_name = {Path(item["path"]).name: item for item in manifest["expected_artifacts"]}
    for vector_id, expected_name, source_name, _ in VECTORS:
        source_info = source_by_name[source_name]
        expected_info = expected_by_name[expected_name]
        if expected_info["vector_id"] != vector_id:
            raise RuntimeError(f"Manifest vector identity mismatch for {vector_id}")
        if (expected_info["source_path"] != source_info["path"]
                or expected_info["source_sha256"] != source_info["sha256"]):
            raise RuntimeError(f"Manifest expected/source linkage mismatch for {vector_id}")
        for info, expected_size, label in ((source_info, 640 * 400, "source"),
                                           (expected_info, TARGET_PIXELS, "expected")):
            path = HERE / info["path"]
            require_file(path)
            payload = path.read_bytes()
            if len(payload) != expected_size or info["size_bytes"] != expected_size:
                raise RuntimeError(f"{label} size mismatch for {vector_id}: {path}")
            if sha256(payload) != info["sha256"]:
                raise RuntimeError(f"{label} SHA-256 mismatch for {vector_id}: {path}")

    composer_call_count, area_call_count = runner_composer_call_counts(runner_path)
    if composer_call_count != 1 or area_call_count != 1:
        raise RuntimeError("Integrated runner must contain exactly one GrayComposer call total, "
                           "and exactly one with gray_interpolation=cv2.INTER_AREA "
                           f"(found total={composer_call_count}, area={area_call_count})")

    try:
        import cv2
        import numpy as np
        import PIL
        from PIL import Image
        from scripts.standalone.compositor import GrayComposer
    except ImportError as exc:
        raise RuntimeError(f"Required Python dependency unavailable: {exc}") from exc

    outputs = []
    artifacts = []
    mask = np.ones((TARGET_HEIGHT, TARGET_WIDTH), dtype=bool)
    line_scaled_x = int((LINE_X * 9 / 10) + 0.5)
    # The established comparison scope excludes the 7-column count-line overlay.
    overlay_cols = sorted({col for col in range(TARGET_WIDTH)
                           if abs(col - line_scaled_x) <= 3})
    binary_mask = mask.copy()
    binary_mask[:, overlay_cols] = False

    for vector_id, expected_name, source_name, kind in VECTORS:
        source = np.frombuffer((HERE / source_by_name[source_name]["path"]).read_bytes(),
                               dtype=np.uint8).reshape((400, 640)).copy()
        expected = np.frombuffer((HERE / expected_by_name[expected_name]["path"]).read_bytes(),
                                 dtype=np.uint8).reshape((TARGET_HEIGHT, TARGET_WIDTH))
        gray = source if kind == "gray" else np.zeros((400, 640), dtype=np.uint8)
        binary = source if kind == "binary" else np.zeros((400, 640), dtype=np.uint8)
        composer = GrayComposer(FONT_PATH, latin_font_path=FONT_PATH,
                                gray_interpolation=cv2.INTER_AREA)
        canvas = composer.render(gray, binary, SESSION_ID, FRAME_ID, line_x=LINE_X)
        region_y = 0 if kind == "gray" else 360
        actual = np.ascontiguousarray(canvas[region_y:region_y + 360, CANVAS_X:CANVAS_X + 576])
        compare_mask = binary_mask if kind == "binary" else mask
        differences = np.not_equal(actual, expected) & compare_mask
        actual_bytes = actual.tobytes(order="C")
        raw_name = f"{vector_id}.actual.raw"
        png_name = f"{vector_id}.actual.png"
        outputs.append({
            "vector_id": vector_id,
            "kind": kind,
            "source_path": source_by_name[source_name]["path"],
            "source_sha256": file_sha256(HERE / source_by_name[source_name]["path"]),
            "expected_path": expected_by_name[expected_name]["path"],
            "expected_sha256": file_sha256(HERE / expected_by_name[expected_name]["path"]),
            "actual_raw": raw_name,
            "actual_png": png_name,
            "actual_sha256": sha256(actual_bytes),
            "compared_pixels": int(np.count_nonzero(compare_mask)),
            "excluded_overlay_pixels": int(TARGET_PIXELS - np.count_nonzero(compare_mask)),
            "differing_pixels": int(np.count_nonzero(differences)),
            "identical_over_compared_pixels": bool(not np.any(differences)),
        })
        png_buffer = io.BytesIO()
        Image.fromarray(actual, mode="L").save(png_buffer, format="PNG")
        artifacts.extend(((raw_name, actual_bytes), (png_name, png_buffer.getvalue())))

    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                       text=True, stderr=subprocess.PIPE).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Unable to read git HEAD: {exc}") from exc
    passed = all(item["differing_pixels"] == 0 for item in outputs)
    report = {
        "schema": "material-counter.current-scaling-local-comparison.v1",
        "status": "PASS" if passed else "FAIL",
        "scope": "local_current_source_scaling_candidate_only",
        "board_actions": 0,
        "G3_release": False,
        "git_head": head,
        "python_version": platform.python_version(),
        "opencv_version": package_version(cv2, "opencv"),
        "numpy_version": package_version(np, "numpy"),
        "pillow_version": package_version(PIL, "pillow"),
        "font_path": str(FONT_PATH),
        "font_sha256": file_sha256(FONT_PATH),
        "runner_path": runner_path.relative_to(ROOT).as_posix(),
        "runner_sha256": file_sha256(runner_path),
        "compositor_path": compositor_path.relative_to(ROOT).as_posix(),
        "compositor_sha256": file_sha256(compositor_path),
        "runner_gray_composer_constructor_ast_calls": composer_call_count,
        "runner_area_constructor_ast_calls": area_call_count,
        "session_id": str(SESSION_ID),
        "frame_id": FRAME_ID,
        "target_shape": [TARGET_HEIGHT, TARGET_WIDTH],
        "binary_overlay": {
            "source_line_x": LINE_X,
            "scaled_x": line_scaled_x,
            "overlay_columns_excluded": overlay_cols,
            "overlay_pixels_excluded": len(overlay_cols) * TARGET_HEIGHT,
            "note": "Exactly seven columns are excluded from binary comparison; full image is not declared identical.",
        },
        "comparisons": outputs,
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    for name, payload in artifacts:
        (output_dir / name).write_bytes(payload)
    report_path = output_dir / "comparison.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
