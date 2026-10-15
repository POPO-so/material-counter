"""Generate Binary8 morphology vectors and RGB888 inputs for WO-0008."""

import argparse
import hashlib
import json
import random
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_MANIFEST = ROOT / "data/results/rgb2gray-vectors-20261012/manifest.json"
THRESHOLD = 128
BACKGROUND = 0x00
FOREGROUND = 0xFF
MODES = (0, 1, 2)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frame(width: int, height: int, points: set[tuple[int, int]] | list[tuple[int, int]]) -> list[int]:
    pixels = [BACKGROUND] * (width * height)
    for x, y in points:
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"point {(x, y)} is outside {width}x{height}")
        pixels[y * width + x] = FOREGROUND
    return pixels


def solid(width: int, height: int, value: int) -> list[int]:
    return [value] * (width * height)


def apply_mode(pixels: list[int], width: int, height: int, mode: int) -> list[int]:
    """Apply a 3x3 square operation with constant-zero padding per frame."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode}")
    if len(pixels) != width * height:
        raise ValueError("frame pixel count does not match width*height")
    if any(value not in (BACKGROUND, FOREGROUND) for value in pixels):
        raise ValueError("Binary8 input pixels must be 0x00 or 0xff")
    if mode == 0:
        return list(pixels)

    output = []
    for y in range(height):
        for x in range(width):
            neighbors = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    xx, yy = x + dx, y + dy
                    neighbors.append(
                        pixels[yy * width + xx]
                        if 0 <= xx < width and 0 <= yy < height
                        else BACKGROUND
                    )
            hit = all(value == FOREGROUND for value in neighbors) if mode == 1 else any(
                value == FOREGROUND for value in neighbors
            )
            output.append(FOREGROUND if hit else BACKGROUND)
    return output


def validate_model() -> None:
    """Run small independent checks for padding, window warmup, and edge retention."""
    one_black = [BACKGROUND]
    one_white = [FOREGROUND]
    assert apply_mode(one_black, 1, 1, 1) == [BACKGROUND]
    assert apply_mode(one_white, 1, 1, 1) == [BACKGROUND]
    assert apply_mode(one_white, 1, 1, 2) == [FOREGROUND]
    assert apply_mode(one_white, 1, 1, 0) == one_white

    all_white = [FOREGROUND] * 9
    expected_erode = [BACKGROUND] * 9
    expected_erode[4] = FOREGROUND
    assert apply_mode(all_white, 3, 3, 1) == expected_erode

    single_center = [BACKGROUND] * 9
    single_center[4] = FOREGROUND
    assert apply_mode(single_center, 3, 3, 2) == all_white

    # The output shape is never cropped, even before a full 3x3 window exists.
    for width, height in ((1, 1), (1, 9), (9, 1), (2, 2)):
        for mode in MODES:
            assert len(apply_mode([FOREGROUND] * (width * height), width, height, mode)) == width * height


def make_scenes() -> list[dict]:
    rng = random.Random(20261013)

    col_frames = [
        ("single_top", frame(1, 9, {(0, 0)})),
        ("single_middle", frame(1, 9, {(0, 4)})),
        ("single_bottom", frame(1, 9, {(0, 8)})),
        ("separated_points", frame(1, 9, {(0, 1), (0, 4), (0, 7)})),
    ]
    row_frames = [
        ("single_left", frame(9, 1, {(0, 0)})),
        ("single_middle", frame(9, 1, {(4, 0)})),
        ("single_right", frame(9, 1, {(8, 0)})),
        ("separated_points", frame(9, 1, {(1, 0), (4, 0), (7, 0)})),
    ]

    noise = []
    for index, probability in enumerate((0.08, 0.23, 0.51)):
        noise.append((f"seeded_salt_pepper_{index}", [
            FOREGROUND if rng.random() < probability else BACKGROUND for _ in range(9 * 7)
        ]))

    targets = [
        ("one_pixel", frame(17, 11, {(8, 5)})),
        ("two_by_two", frame(17, 11, {(8, 5), (9, 5), (8, 6), (9, 6)})),
        ("three_by_three", frame(17, 11, {(x, y) for y in range(4, 7) for x in range(7, 10)})),
        ("edge_strip", frame(17, 11, {(x, y) for y in range(0, 3) for x in range(0, 4)})),
    ]

    one_pixel_hole = {(x, y) for y in range(3, 8) for x in range(6, 11)} - {(8, 5)}
    three_by_three_hole = {(x, y) for y in range(2, 9) for x in range(5, 12)}
    three_by_three_hole -= {(x, y) for y in range(4, 7) for x in range(7, 10)}
    thin_ring = {(x, y) for y in range(2, 9) for x in range(5, 12)
                 if x in (5, 11) or y in (2, 8)}
    ring_frames = [
        ("single_pixel_hole", frame(17, 11, one_pixel_hole)),
        ("three_by_three_hole", frame(17, 11, three_by_three_hole)),
        ("thin_ring", frame(17, 11, thin_ring)),
    ]

    separated = {(x, y) for y in range(4, 7) for x in range(3, 6)}
    separated |= {(x, y) for y in range(4, 7) for x in range(7, 10)}
    touching = {(x, y) for y in range(4, 7) for x in range(3, 10)}
    near_pair = {(x, y) for y in range(4, 7) for x in range(2, 5)}
    near_pair |= {(x, y) for y in range(4, 7) for x in range(7, 10)}
    adjacent_frames = [
        ("one_pixel_gap", frame(17, 11, separated)),
        ("two_pixel_gap", frame(17, 11, near_pair)),
        ("already_touching", frame(17, 11, touching)),
    ]

    boundary_frames = []
    for index, (x, y) in enumerate(((0, 0), (8, 0), (0, 6), (8, 6))):
        boundary_frames.append((f"corner_{index}", frame(9, 7, {(x, y)})))
    boundary_frames.append(("top_and_left_edges", frame(9, 7,
        {(x, 0) for x in range(9)} | {(0, y) for y in range(7)})))
    boundary_frames.extend([
        ("thick_top_target", frame(9, 7, {(x, y) for y in range(3) for x in range(3, 6)})),
        ("thick_bottom_target", frame(9, 7, {(x, y) for y in range(4, 7) for x in range(3, 6)})),
        ("thick_left_target", frame(9, 7, {(x, y) for y in range(2, 5) for x in range(3)})),
        ("thick_right_target", frame(9, 7, {(x, y) for y in range(2, 5) for x in range(6, 9)})),
    ])

    checker = [FOREGROUND if (x + y) % 2 == 0 else BACKGROUND
               for y in range(7) for x in range(9)]
    random_frames = []
    for index, probability in enumerate((0.12, 0.48, 0.78)):
        random_frames.append((f"seeded_density_{index}", [
            FOREGROUND if rng.random() < probability else BACKGROUND
            for _ in range(17 * 11)
        ]))

    original_scenes = [
        {"name": "tiny_1x1", "width": 1, "height": 1,
         "frames_data": [("black", [BACKGROUND]), ("white", [FOREGROUND])]},
        {"name": "column_1x9", "width": 1, "height": 9, "frames_data": col_frames},
        {"name": "row_9x1", "width": 9, "height": 1, "frames_data": row_frames},
        {"name": "tiny_2x2", "width": 2, "height": 2, "frames_data": [
            ("all_white", solid(2, 2, FOREGROUND)),
            ("one_corner", frame(2, 2, {(0, 0)})),
            ("diagonal_pair", frame(2, 2, {(0, 0), (1, 1)})),
        ]},
        {"name": "isolated_noise_9x7", "width": 9, "height": 7, "frames_data": noise},
        {"name": "small_targets_17x11", "width": 17, "height": 11, "frames_data": targets},
        {"name": "ring_holes_17x11", "width": 17, "height": 11, "frames_data": ring_frames},
        {"name": "adjacent_targets_17x11", "width": 17, "height": 11,
         "frames_data": adjacent_frames},
        {"name": "image_boundaries_9x7", "width": 9, "height": 7,
         "frames_data": boundary_frames},
        {"name": "uniform_checker_9x7", "width": 9, "height": 7, "frames_data": [
            ("all_black", solid(9, 7, BACKGROUND)),
            ("all_white", solid(9, 7, FOREGROUND)),
            ("checkerboard", checker),
        ]},
        {"name": "random_nonuniform_17x11", "width": 17, "height": 11,
         "frames_data": random_frames},
    ]
    result = [scene for scene in original_scenes
              if (scene["width"], scene["height"]) not in ((9, 7), (17, 11))]
    for width, height, merged_name in (
        (9, 7, "directed_9x7"),
        (17, 11, "directed_17x11"),
    ):
        merged_frames = []
        for scene in original_scenes:
            if (scene["width"], scene["height"]) != (width, height):
                continue
            merged_frames.extend(
                (f"{scene['name']}::{label}", pixels)
                for label, pixels in scene["frames_data"]
            )
        result.append({
            "name": merged_name,
            "width": width,
            "height": height,
            "frames_data": merged_frames,
        })
    return result


def read_source_frames(source_manifest_path: Path) -> tuple[dict, list[tuple[list[int], dict, int, int]], dict]:
    source_manifest_path = source_manifest_path.resolve()
    manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_frames = manifest.get("h3_frames")
    if not isinstance(source_frames, list) or len(source_frames) != 3:
        raise ValueError("the RGB-to-Gray manifest must identify exactly three H3 frames")

    h3_frames = []
    for frame_index, entry in enumerate(source_frames):
        source_path = (ROOT / entry["path"]).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        actual_hash = sha256(source_path)
        if actual_hash != entry.get("sha256"):
            raise ValueError(f"H3 PNG hash differs from RGB-to-Gray manifest: {source_path}")
        with Image.open(source_path) as image:
            if image.mode != "L":
                raise ValueError(f"expected Gray8 PNG, got mode={image.mode}: {source_path}")
            width, height = image.size
            pixels = list(image.tobytes())
        h3_frames.append((pixels, {
            "path": str(source_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": actual_hash,
            "frame_index": frame_index,
        }, width, height))
    if len({(width, height) for _, _, width, height in h3_frames}) != 1:
        raise ValueError("all H3 frames in this scene must share dimensions")
    try:
        source_manifest_path_record = str(source_manifest_path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        source_manifest_path_record = str(source_manifest_path)
    return manifest, h3_frames, {
        "path": source_manifest_path_record,
        "sha256": sha256(source_manifest_path),
    }


def write_hex(path: Path, values: list[int], digits: int) -> None:
    path.write_text("".join(f"{value:0{digits}x}\n" for value in values), encoding="ascii")


def generate(out: Path, source_manifest_path: Path = DEFAULT_SOURCE_MANIFEST) -> dict:
    validate_model()
    source_manifest, h3_frames, source_manifest_record = read_source_frames(source_manifest_path)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)

    scenes = make_scenes()
    h3_width = h3_frames[0][2]
    h3_height = h3_frames[0][3]
    h3_scene = {
        "name": "h3_threshold128",
        "width": h3_width,
        "height": h3_height,
        "frames_data": [],
        "rgb_frames_data": [],
    }
    source_pngs = []
    for index, (gray_pixels, source, width, height) in enumerate(h3_frames):
        if (width, height) != (h3_width, h3_height):
            raise AssertionError("H3 frame dimensions changed while reading")
        h3_scene["frames_data"].append((f"source_frame_{index}", [
            FOREGROUND if value >= THRESHOLD else BACKGROUND for value in gray_pixels
        ]))
        h3_scene["rgb_frames_data"].append(gray_pixels)
        source_pngs.append({
            "scene": h3_scene["name"],
            "frame_index": index,
            "path": source["path"],
            "sha256": source["sha256"],
        })
    scenes.append(h3_scene)

    vector_files: dict[str, str] = {}
    scene_records = []
    for scene in scenes:
        name = scene["name"]
        width = scene["width"]
        height = scene["height"]
        frames_data = scene["frames_data"]
        if not frames_data or len({len(pixels) for _, pixels in frames_data}) != 1:
            raise ValueError(f"scene {name} has empty or inconsistent frames")
        if any(len(pixels) != width * height for _, pixels in frames_data):
            raise ValueError(f"scene {name} contains a frame with the wrong geometry")
        if any(value not in (BACKGROUND, FOREGROUND)
               for _, pixels in frames_data for value in pixels):
            raise ValueError(f"scene {name} contains non-binary input")

        scene_dir = out / name
        scene_dir.mkdir()
        binary_values = [value for _, pixels in frames_data for value in pixels]
        gray_frames_data = scene.get("rgb_frames_data")
        if gray_frames_data is not None:
            if len(gray_frames_data) != len(frames_data) or any(
                len(gray_pixels) != width * height for gray_pixels in gray_frames_data
            ):
                raise ValueError(f"scene {name} has inconsistent RGB/grayscale frame data")
            rgb_gray_values = [value for gray_pixels in gray_frames_data for value in gray_pixels]
            rgb_values = [value * 0x010101 for value in rgb_gray_values]
            if [FOREGROUND if value >= THRESHOLD else BACKGROUND for value in rgb_gray_values] != binary_values:
                raise AssertionError(f"scene {name}: RGB grayscale does not reproduce Binary8 input")
        else:
            # Tiny directed scenes use grayscale values 0/255, so RGB->Gray->threshold
            # is exactly the same pixel stream as the standalone Binary8 input.
            rgb_values = [value * 0x010101 for value in binary_values]
        rgb_rel = f"{name}/input_rgb.hex"
        binary_rel = f"{name}/input_binary.hex"
        write_hex(out / rgb_rel, rgb_values, 6)
        write_hex(out / binary_rel, binary_values, 2)
        vector_files[rgb_rel] = sha256(out / rgb_rel)
        vector_files[binary_rel] = sha256(out / binary_rel)

        expected_paths = {}
        expected_tags = []
        pixels_per_frame = width * height
        for frame_index in range(len(frames_data)):
            for local in range(pixels_per_frame):
                y, x = divmod(local, width)
                expected_tags.append((int(local == 0) << 9) | (int(x == width - 1) << 8))

        for mode in MODES:
            all_output = []
            for _, frame_pixels in frames_data:
                all_output.extend(apply_mode(frame_pixels, width, height, mode))
            packed = [tag | value for tag, value in zip(expected_tags, all_output)]
            expected_rel = f"{name}/expected-mode{mode}.hex"
            write_hex(out / expected_rel, packed, 3)
            vector_files[expected_rel] = sha256(out / expected_rel)
            expected_paths[str(mode)] = expected_rel

        scene_records.append({
            "name": name,
            "width": width,
            "height": height,
            "frames": len(frames_data),
            "frame_labels": [label for label, _ in frames_data],
            "beats": len(binary_values),
            "rgb": rgb_rel,
            "binary": binary_rel,
            "expected": expected_paths,
        })

    manifest = {
        "schema_version": 1,
        "threshold": THRESHOLD,
        "foreground": FOREGROUND,
        "background": BACKGROUND,
        "morphology": {
            "kernel": "3x3 square",
            "mode": {"0": "bypass", "1": "erosion", "2": "dilation"},
            "boundary_padding": "constant zero outside each frame",
            "coordinates": "same shape; each output pixel corresponds to the same input coordinate",
        },
        "hex_format": {
            "rgb": "24-bit RGB888, six hex digits per beat",
            "binary": "Binary8, two hex digits per beat",
            "expected": "10-bit word in three hex digits; bit9=SOF, bit8=EOL, bits7:0=Binary8",
            "frame_order": "frames concatenated; SOF on each frame's first pixel; EOL on each row's last pixel",
        },
        "source_manifest": source_manifest_record,
        "source_manifest_metadata": {
            "formula": source_manifest.get("formula"),
            "source_frame_count": len(source_manifest.get("h3_frames", [])),
        },
        "source_pngs": source_pngs,
        "files_sha256": vector_files,
        "scenes": scene_records,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({
        "manifest": str(out / "manifest.json"),
        "scenes": len(scene_records),
        "beats": sum(item["beats"] for item in scene_records),
        "source_pngs": len(source_pngs),
        "model_self_check": "PASS",
    }, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True,
                        help="new vectors directory; it must not already exist")
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST,
                        help="existing rgb2gray vector manifest containing the three H3 PNGs")
    args = parser.parse_args()
    generate(args.out, args.source_manifest)
