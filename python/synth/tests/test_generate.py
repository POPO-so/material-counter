import ast
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


SYNTH_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = SYNTH_ROOT / "specs" / "h3_matrix.json"
sys.path.insert(0, str(SYNTH_ROOT.parent))
from synth.generate import generate_dataset

REQUIRED_GT_FIELDS = {
    "frame_id",
    "object_id",
    "x_min",
    "x_max",
    "y_min",
    "y_max",
    "centroid_x",
    "centroid_y",
    "speed_px_per_frame",
    "visible",
}


def _run_generator(output_dir: Path, spec_path: Path = SPEC_PATH) -> None:
    subprocess.run(
        [sys.executable, "-m", "synth.generate", "--spec", str(spec_path), "--out", str(output_dir)],
        cwd=SYNTH_ROOT.parent,
        check=True,
    )


def _write_shortened_spec(path: Path) -> tuple[str, str]:
    with SPEC_PATH.open(encoding="utf-8") as handle:
        spec = json.load(handle)
    previous_condition_ids = [condition["id"] for condition in spec["conditions"][:2]]
    spec["num_frames"] = 2
    spec["conditions"] = spec["conditions"][:1]
    path.write_text(json.dumps(spec), encoding="utf-8")
    return previous_condition_ids[0], previous_condition_ids[1]


def _load_dataset(output_dir: Path):
    with (output_dir / "config.json").open(encoding="utf-8") as handle:
        config = json.load(handle)
    return config


def _load_sequence_files(output_dir: Path, condition_id: str):
    condition_dir = output_dir / condition_id
    with (condition_dir / "config.json").open(encoding="utf-8") as handle:
        sequence_config = json.load(handle)
    with (condition_dir / "ground_truth.json").open(encoding="utf-8") as handle:
        ground_truth = json.load(handle)
    return condition_dir, sequence_config, ground_truth


def _actual_frame_ids(frames_dir: Path) -> set[int]:
    return {
        int(path.stem.split("_")[1])
        for path in frames_dir.glob("frame_*.png")
    }


def _clip_bbox(bbox: dict, width: int, height: int) -> tuple[int, int, int, int]:
    """Convert an inclusive GT bbox to a clipped, half-open pixel slice."""

    x_min = max(0, min(width, int(bbox["x_min"])))
    x_max = max(0, min(width, int(bbox["x_max"]) + 1))
    y_min = max(0, min(height, int(bbox["y_min"])))
    y_max = max(0, min(height, int(bbox["y_max"]) + 1))
    return x_min, x_max, y_min, y_max


def test_h3_matrix_has_required_scale_coverage_and_png_io(tmp_path):
    output_dir = tmp_path / "h3_full"
    _run_generator(output_dir)
    config = _load_dataset(output_dir)

    assert config["canvas"] == {"width": 320, "height": 240}
    assert config["image_format"] == "PNG"
    assert config["image_mode"] == "L"

    conditions = config["conditions"]
    backbone = [item for item in conditions if item["category"] == "backbone"]
    special = [item for item in conditions if item["category"] == "special"]
    assert len(backbone) >= 15
    assert len(special) >= 6
    assert len(conditions) >= 21

    speed_spacing = {
        (item["speed_px_per_frame"], item["spacing_px"])
        for item in backbone
    }
    expected_speeds = {0.5, 1.0, 1.5}
    expected_spacings = {8, 11, 14}
    assert {speed for speed, _ in speed_spacing} == expected_speeds
    assert {spacing for _, spacing in speed_spacing} == expected_spacings
    assert speed_spacing == {
        (speed, spacing)
        for speed in expected_speeds
        for spacing in expected_spacings
    }

    # This deliberately counts each physical object once from config, not every frame annotation.
    unique_physical_objects = sum(
        sequence["physical_object_count"]
        for condition in conditions
        for sequence in condition["sequences"]
    )
    assert unique_physical_objects >= 300
    assert any(
        sequence["noise_sigma"] > 0
        for condition in backbone
        for sequence in condition["sequences"]
    )
    assert any(
        sequence["dropped_frame_ids"]
        for condition in backbone
        for sequence in condition["sequences"]
    )

    for condition in conditions:
        condition_dir = output_dir / condition["id"]
        assert (condition_dir / "config.json").is_file()
        assert (condition_dir / "ground_truth.json").is_file()
        frame_paths = sorted((condition_dir / "frames").glob("frame_*.png"))
        assert frame_paths
        with Image.open(frame_paths[0]) as image:
            assert image.mode == "L"
            assert image.size == (320, 240)
            pixels = np.asarray(image)
        assert pixels.dtype == np.uint8

        with (condition_dir / "ground_truth.json").open(encoding="utf-8") as handle:
            ground_truth = json.load(handle)
        assert len(ground_truth["frames"]) == 24
        for frame in ground_truth["frames"]:
            assert frame["frame_id"] >= 0
            for object_truth in frame["objects"]:
                assert REQUIRED_GT_FIELDS <= object_truth.keys()
                assert object_truth["frame_id"] == frame["frame_id"]
        dropped_ids = set(condition["sequences"][0]["dropped_frame_ids"])
        written_ids = {
            int(path.stem.split("_")[1]) for path in frame_paths
        }
        assert written_ids == set(range(24)) - dropped_ids


def test_dropped_frame_metadata_cross_validates_config_png_and_ground_truth(tmp_path):
    output_dir = tmp_path / "h3_drop_metadata"
    _run_generator(output_dir)
    dataset_config = _load_dataset(output_dir)

    for condition in dataset_config["conditions"]:
        condition_dir, sequence_config, ground_truth = _load_sequence_files(
            output_dir, condition["id"]
        )
        expected_frame_ids = set(range(sequence_config["sequences"][0]["num_frames"]))
        actual_png_frame_ids = _actual_frame_ids(condition_dir / "frames")
        dropped_frame_ids = sequence_config["dropped_frame_ids"]

        assert dropped_frame_ids == sorted(dropped_frame_ids)
        assert set(dropped_frame_ids) == expected_frame_ids - actual_png_frame_ids
        if not dropped_frame_ids:
            assert dropped_frame_ids == []

        frames_by_id = {frame["frame_id"]: frame for frame in ground_truth["frames"]}
        for frame_id in expected_frame_ids:
            objects = frames_by_id[frame_id]["objects"]
            if frame_id in dropped_frame_ids:
                assert objects
                assert all(not object_truth["visible"] for object_truth in objects)
            else:
                assert any(object_truth["visible"] for object_truth in objects)


def test_nonempty_output_is_rejected_without_changing_old_dataset_files(tmp_path):
    output_dir = tmp_path / "existing_dataset"
    kept_condition_id, removed_condition_id = _write_shortened_spec(
        tmp_path / "shortened_spec.json"
    )

    old_files = {
        Path("config.json"): b'{"previous_dataset": true}\n',
        Path("sentinel.txt"): b"preserve this experimental record\n",
        Path(kept_condition_id) / "config.json": b'{"num_frames": 24}\n',
        Path(kept_condition_id) / "ground_truth.json": b'{"old_ground_truth": true}\n',
        Path(kept_condition_id) / "frames" / "frame_0000.png": None,
        Path(kept_condition_id) / "frames" / "frame_0023.png": None,
        Path(removed_condition_id) / "frames" / "frame_0007.png": None,
    }
    for relative_path, contents in old_files.items():
        path = output_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if contents is None:
            Image.fromarray(np.full((1, 1), 91, dtype=np.uint8), mode="L").save(path)
        else:
            path.write_bytes(contents)
    before = {
        path.relative_to(output_dir): path.read_bytes()
        for path in output_dir.rglob("*")
        if path.is_file()
    }

    with pytest.raises(FileExistsError, match="not empty"):
        generate_dataset(tmp_path / "shortened_spec.json", output_dir)

    after = {
        path.relative_to(output_dir): path.read_bytes()
        for path in output_dir.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_generator_allows_an_existing_empty_output_directory(tmp_path):
    output_dir = tmp_path / "empty_dataset"
    output_dir.mkdir()
    spec_path = tmp_path / "shortened_spec.json"
    kept_condition_id, _ = _write_shortened_spec(spec_path)

    generate_dataset(spec_path, output_dir)

    assert (output_dir / "config.json").is_file()
    assert (output_dir / kept_condition_id / "config.json").is_file()
    assert (output_dir / kept_condition_id / "ground_truth.json").is_file()


def test_pillow_usage_is_limited_to_png_io(tmp_path):
    del tmp_path
    allowed_files = {
        Path("generate.py"),
        Path("tests/test_generate.py"),
    }
    forbidden_calls = {"filter", "resize", "convert", "point"}
    allowed_image_calls = {"fromarray", "open", "save"}
    pillow_import_files = set()

    def root_name(expression):
        if isinstance(expression, ast.Name):
            return expression.id
        if isinstance(expression, ast.Attribute):
            return root_name(expression.value)
        if isinstance(expression, ast.Call):
            return root_name(expression.func)
        return None

    for source_path in SYNTH_ROOT.rglob("*.py"):
        relative_path = source_path.relative_to(SYNTH_ROOT)
        tree = ast.parse(source_path.read_text(encoding="utf-8"), str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported_modules = [node.module or ""]
            else:
                imported_modules = []
            if any(module == "PIL" or module.startswith("PIL.") for module in imported_modules):
                pillow_import_files.add(relative_path)

            if isinstance(node, ast.Attribute):
                if root_name(node) == "Image":
                    assert node.attr in allowed_image_calls
                assert node.attr not in {"ImageOps", "ImageFilter"}
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls

    assert pillow_import_files <= allowed_files
    assert pillow_import_files == {Path("generate.py"), Path("tests/test_generate.py")}


def test_requirements_declare_only_approved_dependencies(tmp_path):
    del tmp_path
    requirements = SYNTH_ROOT / "requirements.txt"
    declared = [
        line.strip()
        for line in requirements.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert declared == ["numpy", "Pillow", "pytest"]


def test_ground_truth_matches_rendered_gray8_pixels_independently(tmp_path):
    output_dir = tmp_path / "h3_pixels"
    _run_generator(output_dir)
    condition_dir, sequence_config, ground_truth = _load_sequence_files(
        output_dir, "backbone_nominal_medium_02"
    )
    sequence = sequence_config["sequences"][0]
    assert sequence_config["noise_sigma"] > 0
    assert sequence_config["brightness_offset"] != 0

    # Exercise the clipping rule explicitly even though the formal matrix keeps
    # this representative sequence inside the canvas.
    assert _clip_bbox(
        {"x_min": -3, "x_max": 5, "y_min": -2, "y_max": 7}, 320, 240
    ) == (0, 6, 0, 8)

    frame_id = next(
        frame["frame_id"]
        for frame in ground_truth["frames"]
        if any(object_truth["visible"] for object_truth in frame["objects"])
    )
    frame_truth = next(frame for frame in ground_truth["frames"] if frame["frame_id"] == frame_id)
    frame_path = condition_dir / "frames" / f"frame_{frame_id:04d}.png"
    with Image.open(frame_path) as image:
        assert image.mode == "L"
        assert image.size == (320, 240)
        pixels = np.asarray(image)
    assert pixels.dtype == np.uint8

    width, height = pixels.shape[1], pixels.shape[0]
    expected_background = sequence["background_gray"] + sequence["brightness_offset"]
    expected_foreground = sequence["foreground_gray"] + sequence["brightness_offset"]
    noise_sigma = sequence["noise_sigma"]
    occupied = np.zeros((height, width), dtype=bool)
    for object_truth in frame_truth["objects"]:
        if not object_truth["visible"]:
            continue
        x_min, x_max, y_min, y_max = _clip_bbox(object_truth, width, height)
        assert x_max > x_min and y_max > y_min
        region = pixels[y_min:y_max, x_min:x_max]
        occupied[y_min:y_max, x_min:x_max] = True
        robust_foreground_floor = expected_foreground - max(3.0, 3.0 * noise_sigma)
        assert float(np.median(region)) >= robust_foreground_floor
        assert np.mean(region >= expected_background + 20.0) >= 0.75

    background_values = pixels[~occupied]
    assert background_values.size > 0
    robust_background_tolerance = max(3.0, 3.0 * noise_sigma)
    assert abs(float(np.median(background_values)) - expected_background) <= robust_background_tolerance


def test_same_spec_is_byte_for_byte_reproducible(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _run_generator(first)
    _run_generator(second)

    first_files = sorted(path.relative_to(first) for path in first.rglob("*") if path.is_file())
    second_files = sorted(path.relative_to(second) for path in second.rglob("*") if path.is_file())
    assert first_files == second_files
    for relative_path in first_files:
        assert (first / relative_path).read_bytes() == (second / relative_path).read_bytes()


def test_projection_overlap_without_2d_contact_is_asserted_for_all_frames(tmp_path):
    output_dir = tmp_path / "h3_r2"
    _run_generator(output_dir)
    condition_dir = output_dir / "projection_overlap_noncontact"
    with (condition_dir / "ground_truth.json").open(encoding="utf-8") as handle:
        ground_truth = json.load(handle)

    valid_frame_count = 0
    for frame in ground_truth["frames"]:
        visible = [item for item in frame["objects"] if item["visible"]]
        x_overlap_pairs = []
        for left_index, left in enumerate(visible):
            for right in visible[left_index + 1 :]:
                x_overlaps = left["x_min"] <= right["x_max"] and right["x_min"] <= left["x_max"]
                y_overlaps = left["y_min"] <= right["y_max"] and right["y_min"] <= left["y_max"]
                bbox_intersects = x_overlaps and y_overlaps
                if x_overlaps:
                    x_overlap_pairs.append((left, right))
                    assert not bbox_intersects
        assert x_overlap_pairs
        valid_frame_count += 1
    assert valid_frame_count == len(ground_truth["frames"])
