import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image


SYNTH_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = SYNTH_ROOT / "specs" / "h3_matrix.json"
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


def _load_dataset(output_dir: Path):
    with (output_dir / "config.json").open(encoding="utf-8") as handle:
        config = json.load(handle)
    return config


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

    saw_valid_projection_overlap = False
    for frame in ground_truth["frames"]:
        visible = [item for item in frame["objects"] if item["visible"]]
        for left_index, left in enumerate(visible):
            for right in visible[left_index + 1 :]:
                x_overlaps = left["x_min"] <= right["x_max"] and right["x_min"] <= left["x_max"]
                y_overlaps = left["y_min"] <= right["y_max"] and right["y_min"] <= left["y_max"]
                bbox_intersects = x_overlaps and y_overlaps
                if x_overlaps:
                    saw_valid_projection_overlap = True
                    assert not bbox_intersects
    assert saw_valid_projection_overlap
