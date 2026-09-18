"""CLI for deterministic H3 synthetic sequences with frame-level ground truth."""

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .scene import ObjectSpec, Scene, ground_truth_frame, render_frame, validate_scene


GROUND_TRUTH_FIELDS = [
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
]


def _merged_condition(spec: dict[str, Any], condition: dict[str, Any]) -> dict[str, Any]:
    merged = dict(spec)
    merged.update(condition)
    pattern = dict(spec.get("object_pattern", {}))
    pattern.update(condition.get("object_pattern", {}))
    merged["object_pattern"] = pattern
    return merged


def _build_scene(spec: dict[str, Any], condition: dict[str, Any]) -> tuple[Scene, dict[str, Any]]:
    merged = _merged_condition(spec, condition)
    canvas = merged["canvas"]
    pattern = merged["object_pattern"]
    count = int(pattern["count"])
    spacing = float(merged["spacing_px"])
    start_x = float(pattern["start_x"])
    y_cycle = tuple(float(value) for value in merged.get("y_cycle", pattern["y_cycle"]))
    if count <= 0 or not y_cycle:
        raise ValueError("object_pattern count and y_cycle must be non-empty")

    objects = tuple(
        ObjectSpec(
            object_id=object_id,
            start_x=start_x + object_id * spacing,
            base_y=y_cycle[object_id % len(y_cycle)],
        )
        for object_id in range(count)
    )
    profile = tuple(
        (int(item["start_frame"]), float(item["speed_px_per_frame"]))
        for item in merged.get("speed_profile", [])
    )
    explicit_drop_ids = frozenset(int(frame_id) for frame_id in merged.get("drop_frame_ids", []))
    scene = Scene(
        condition_id=str(condition["id"]),
        width=int(canvas["width"]),
        height=int(canvas["height"]),
        fps=int(merged["fps"]),
        num_frames=int(merged["num_frames"]),
        background_gray=int(merged["background_gray"]),
        foreground_gray=int(merged["foreground_gray"]),
        object_width=int(merged["object_width"]),
        object_height=int(merged["object_height"]),
        default_speed_px_per_frame=float(merged["speed_px_per_frame"]),
        speed_profile=profile,
        noise_sigma=float(merged["noise_sigma"]),
        brightness_offset=float(merged["brightness_offset"]),
        drop_frame_probability=float(merged["drop_frame_probability"]),
        explicit_drop_frame_ids=explicit_drop_ids,
        objects=objects,
    )
    validate_scene(scene)
    effective = {
        "condition_id": scene.condition_id,
        "fps": scene.fps,
        "num_frames": scene.num_frames,
        "width": scene.width,
        "height": scene.height,
        "background_gray": scene.background_gray,
        "foreground_gray": scene.foreground_gray,
        "object_width": scene.object_width,
        "object_height": scene.object_height,
        "speed_px_per_frame": scene.default_speed_px_per_frame,
        "speed_profile": [
            {"start_frame": start_frame, "speed_px_per_frame": speed}
            for start_frame, speed in scene.speed_profile
        ],
        "spacing_px": spacing,
        "noise_sigma": scene.noise_sigma,
        "brightness_offset": scene.brightness_offset,
        "drop_frame_probability": scene.drop_frame_probability,
        "requested_drop_frame_ids": sorted(scene.explicit_drop_frame_ids),
        "object_pattern": {
            "count": count,
            "start_x": start_x,
            "y_cycle": list(y_cycle),
        },
        "objects": [
            {
                "object_id": object_spec.object_id,
                "start_x": object_spec.start_x,
                "start_y": object_spec.base_y,
            }
            for object_spec in scene.objects
        ],
        "physical_object_count": len(scene.objects),
    }
    return scene, effective


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_sequence(
    output_dir: Path,
    scene: Scene,
    effective: dict[str, Any],
    sequence_seed: int,
    dataset_id: str,
) -> dict[str, Any]:
    sequence_id = "sequence_000"
    sequence_dir = output_dir / scene.condition_id
    frames_dir = sequence_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(sequence_seed)
    dropped_frame_ids = []
    truth_frames = []

    for frame_id in range(scene.num_frames):
        dropped = frame_id in scene.explicit_drop_frame_ids
        if not dropped and scene.drop_frame_probability > 0.0:
            dropped = bool(rng.random() < scene.drop_frame_probability)
        visible = not dropped
        if dropped:
            dropped_frame_ids.append(frame_id)
        else:
            frame = render_frame(scene, frame_id, rng, visible=True)
            assert frame is not None
            Image.fromarray(frame, mode="L").save(
                frames_dir / f"frame_{frame_id:04d}.png", format="PNG"
            )
        truth_frames.append(ground_truth_frame(scene, frame_id, visible))

    sequence_config = dict(effective)
    sequence_config.update(
        {
            "sequence_id": sequence_id,
            "seed": sequence_seed,
            "dropped_frame_ids": dropped_frame_ids,
            "image_format": "PNG",
            "image_mode": "L",
            "image_dtype": "uint8",
            "ground_truth_fields": GROUND_TRUTH_FIELDS,
        }
    )
    condition_config = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "id": scene.condition_id,
        "condition_id": scene.condition_id,
        "category": effective["category"],
        "speed_px_per_frame": effective["speed_px_per_frame"],
        "spacing_px": effective["spacing_px"],
        "noise_sigma": effective["noise_sigma"],
        "brightness_offset": effective["brightness_offset"],
        "sequences": [sequence_config],
    }
    ground_truth = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "condition_id": scene.condition_id,
        "sequence_id": sequence_id,
        "frame_width": scene.width,
        "frame_height": scene.height,
        "num_frames": scene.num_frames,
        "physical_object_count": len(scene.objects),
        "frames": truth_frames,
    }
    _write_json(sequence_dir / "config.json", condition_config)
    _write_json(sequence_dir / "ground_truth.json", ground_truth)
    sequence_config["dropped_frame_ids"] = dropped_frame_ids
    return sequence_config


def generate_dataset(spec_path: Path, output_dir: Path) -> dict[str, Any]:
    with spec_path.open(encoding="utf-8") as handle:
        spec = json.load(handle)
    conditions = spec.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise ValueError("spec must contain a non-empty conditions list")

    output_dir.mkdir(parents=True, exist_ok=True)
    condition_configs = []
    for condition_index, condition in enumerate(conditions):
        if not isinstance(condition, dict) or "id" not in condition or "category" not in condition:
            raise ValueError("each condition needs id and category")
        scene, effective = _build_scene(spec, condition)
        effective["category"] = condition["category"]
        sequence_seed = int(spec["seed"]) + condition_index
        sequence_config = _write_sequence(
            output_dir, scene, effective, sequence_seed, str(spec["dataset_id"])
        )
        condition_config = {
            "schema_version": 1,
            "dataset_id": spec["dataset_id"],
            "id": scene.condition_id,
            "condition_id": scene.condition_id,
            "category": condition["category"],
            "speed_px_per_frame": sequence_config["speed_px_per_frame"],
            "spacing_px": sequence_config["spacing_px"],
            "noise_sigma": sequence_config["noise_sigma"],
            "brightness_offset": sequence_config["brightness_offset"],
            "sequences": [sequence_config],
        }
        condition_configs.append(condition_config)

    root_config = {
        "schema_version": int(spec["schema_version"]),
        "dataset_id": spec["dataset_id"],
        "generator": "synth.generate",
        "seed": int(spec["seed"]),
        "spec_file": spec_path.name,
        "canvas": {
            "width": int(spec["canvas"]["width"]),
            "height": int(spec["canvas"]["height"]),
        },
        "fps": int(spec["fps"]),
        "num_frames": int(spec["num_frames"]),
        "background_gray": int(spec["background_gray"]),
        "foreground_gray": int(spec["foreground_gray"]),
        "object_width": int(spec["object_width"]),
        "object_height": int(spec["object_height"]),
        "speed_px_per_frame": float(spec["speed_px_per_frame"]),
        "noise_sigma": float(spec["noise_sigma"]),
        "brightness_offset": float(spec["brightness_offset"]),
        "drop_frame_probability": float(spec["drop_frame_probability"]),
        "image_format": "PNG",
        "image_mode": "L",
        "image_dtype": "uint8",
        "ground_truth_fields": GROUND_TRUTH_FIELDS,
        "conditions": condition_configs,
    }
    _write_json(output_dir / "config.json", root_config)
    return root_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    config = generate_dataset(args.spec, args.out)
    sequence_count = sum(len(condition["sequences"]) for condition in config["conditions"])
    physical_object_count = sum(
        sequence["physical_object_count"]
        for condition in config["conditions"]
        for sequence in condition["sequences"]
    )
    print(
        f"Generated {sequence_count} sequences, "
        f"{physical_object_count} unique physical objects at "
        f"{config['canvas']['width']}x{config['canvas']['height']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
