"""Small, deterministic scene model used by the H3 synthetic data generator."""

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class ObjectSpec:
    """One physical object, whose identity is stable across all frames."""

    object_id: int
    start_x: float
    base_y: float


@dataclass(frozen=True)
class Scene:
    condition_id: str
    width: int
    height: int
    fps: int
    num_frames: int
    background_gray: int
    foreground_gray: int
    object_width: int
    object_height: int
    default_speed_px_per_frame: float
    speed_profile: tuple[tuple[int, float], ...]
    noise_sigma: float
    brightness_offset: float
    drop_frame_probability: float
    explicit_drop_frame_ids: frozenset[int]
    objects: tuple[ObjectSpec, ...]


def speed_at_frame(scene: Scene, frame_id: int) -> float:
    """Return the configured speed active at ``frame_id``."""

    speed = scene.default_speed_px_per_frame
    for start_frame, profile_speed in scene.speed_profile:
        if frame_id < start_frame:
            break
        speed = profile_speed
    return speed


def x_position_at_frame(scene: Scene, object_spec: ObjectSpec, frame_id: int) -> float:
    """Integrate the configured per-frame speed from frame zero."""

    displacement = sum(speed_at_frame(scene, prior_frame) for prior_frame in range(frame_id))
    return object_spec.start_x + displacement


def _integer_bbox(scene: Scene, object_spec: ObjectSpec, frame_id: int) -> tuple[int, int, int, int]:
    x_min = int(round(x_position_at_frame(scene, object_spec, frame_id)))
    y_min = int(round(object_spec.base_y))
    return x_min, x_min + scene.object_width - 1, y_min, y_min + scene.object_height - 1


def ground_truth_object(
    scene: Scene,
    object_spec: ObjectSpec,
    frame_id: int,
    visible: bool,
) -> dict:
    """Build a traceable frame/object annotation."""

    x_min, x_max, y_min, y_max = _integer_bbox(scene, object_spec, frame_id)
    return {
        "frame_id": frame_id,
        "object_id": object_spec.object_id,
        "x_min": x_min,
        "x_max": x_max,
        "y_min": y_min,
        "y_max": y_max,
        "centroid_x": round((x_min + x_max) / 2.0, 6),
        "centroid_y": round((y_min + y_max) / 2.0, 6),
        "speed_px_per_frame": speed_at_frame(scene, frame_id),
        "visible": visible,
    }


def ground_truth_frame(scene: Scene, frame_id: int, visible: bool) -> dict:
    return {
        "frame_id": frame_id,
        "objects": [
            ground_truth_object(scene, object_spec, frame_id, visible)
            for object_spec in scene.objects
        ],
    }


def render_frame(
    scene: Scene,
    frame_id: int,
    rng: np.random.Generator,
    visible: bool = True,
) -> Optional[np.ndarray]:
    """Render Gray8 foreground rectangles and optional additive noise.

    This is intentionally only a primitive scene renderer. Pillow is used by
    the CLI for PNG I/O; the pixel array itself is produced with NumPy.
    """

    if not visible:
        return None

    background = float(scene.background_gray) + scene.brightness_offset
    foreground = float(scene.foreground_gray) + scene.brightness_offset
    frame = np.full((scene.height, scene.width), background, dtype=np.float32)
    if scene.noise_sigma > 0.0:
        frame += rng.normal(0.0, scene.noise_sigma, frame.shape).astype(np.float32)

    foreground_value = np.uint8(np.clip(round(foreground), 0, 255))
    for object_spec in scene.objects:
        x_min, x_max, y_min, y_max = _integer_bbox(scene, object_spec, frame_id)
        if x_min < 0 or y_min < 0 or x_max >= scene.width or y_max >= scene.height:
            raise ValueError(
                f"object {object_spec.object_id} leaves canvas at frame {frame_id}: "
                f"bbox=({x_min},{x_max},{y_min},{y_max})"
            )
        frame[y_min : y_max + 1, x_min : x_max + 1] = foreground_value
    return np.clip(np.rint(frame), 0, 255).astype(np.uint8)


def validate_scene(scene: Scene) -> None:
    if scene.width <= 0 or scene.height <= 0:
        raise ValueError("canvas dimensions must be positive")
    if scene.fps <= 0 or scene.num_frames <= 0:
        raise ValueError("fps and num_frames must be positive")
    if scene.object_width <= 0 or scene.object_height <= 0:
        raise ValueError("object dimensions must be positive")
    if not 0.0 <= scene.drop_frame_probability <= 1.0:
        raise ValueError("drop_frame_probability must be between 0 and 1")
    if any(frame_id < 0 or frame_id >= scene.num_frames for frame_id in scene.explicit_drop_frame_ids):
        raise ValueError("explicit drop frame id is outside the sequence")
    profile_starts = [start_frame for start_frame, _ in scene.speed_profile]
    if profile_starts != sorted(profile_starts) or len(set(profile_starts)) != len(profile_starts):
        raise ValueError("speed_profile start_frame values must be strictly increasing")
    if profile_starts and profile_starts[0] != 0:
        raise ValueError("speed_profile must start at frame zero")
    if any(speed < 0.0 for _, speed in scene.speed_profile):
        raise ValueError("speed values must not be negative")
    if scene.default_speed_px_per_frame < 0.0:
        raise ValueError("default speed must not be negative")
    if not scene.objects:
        raise ValueError("scene must contain at least one physical object")

    for frame_id in range(scene.num_frames):
        for object_spec in scene.objects:
            x_min, x_max, y_min, y_max = _integer_bbox(scene, object_spec, frame_id)
            if x_min < 0 or y_min < 0 or x_max >= scene.width or y_max >= scene.height:
                raise ValueError(
                    f"object {object_spec.object_id} leaves canvas at frame {frame_id}"
                )
