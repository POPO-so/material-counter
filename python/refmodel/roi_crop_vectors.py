"""Build reproducible full-frame inputs and cropped ROI golden vectors."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
from typing import Iterable, Iterator

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
H3_FRAMES = (
    "backbone_slow_small_01/frames/frame_0000.png",
    "backbone_nominal_medium_02/frames/frame_0000.png",
    "projection_overlap_noncontact/frames/frame_0000.png",
)
H3_WIDTH = 320
H3_HEIGHT = 240
THRESHOLD = 128


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gray_from_rgb(red: int, green: int, blue: int) -> int:
    return (77 * red + 150 * green + 29 * blue) // 256


def validate_geometry(width: int, height: int, roi: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = roi
    if width <= 0 or height <= 0:
        raise ValueError("frame width and height must be positive")
    if x0 < 0 or y0 < 0 or x0 > x1 or y0 > y1 or x1 > width or y1 > height:
        raise ValueError(
            "ROI must satisfy 0 <= x0 <= x1 <= frame width and "
            "0 <= y0 <= y1 <= frame height"
        )


def coordinate_frame(width: int, height: int, frame_index: int) -> Iterator[tuple[int, int, int]]:
    """Yield a small nonuniform Gray pattern that identifies pixel position."""
    for y in range(height):
        for x in range(width):
            value = (frame_index * 67 + y * 29 + x * 11 + x * y * 3 + (x ^ y) * 5) & 0xFF
            yield value, value, value


def h3_frame(path: Path, width: int, height: int) -> Iterator[tuple[int, int, int]]:
    with Image.open(path) as image:
        if image.mode != "L" or image.size != (width, height):
            raise ValueError(f"unexpected H3 image mode/size: {path} {image.mode} {image.size}")
        pixels = image.tobytes()
    for value in pixels:
        yield value, value, value


def random_color_frame(width: int, height: int, seed: int) -> Iterator[tuple[int, int, int]]:
    rng = random.Random(seed)
    for _ in range(width * height):
        yield rng.randrange(256), rng.randrange(256), rng.randrange(256)


def frame_sources(profile: str, width: int, height: int,
                  include_random_color_frame: bool,
                  random_seed: int) -> tuple[list[tuple[str, Path | None]], int]:
    if profile == "coordinate":
        return [(f"coordinate-frame-{index}", None) for index in range(3)], random_seed
    if width != H3_WIDTH or height != H3_HEIGHT:
        raise ValueError(f"H3 profile requires {H3_WIDTH}x{H3_HEIGHT}, got {width}x{height}")
    sources = [(rel, ROOT / "data" / "synthetic" / "h3_full" / rel) for rel in H3_FRAMES]
    if include_random_color_frame:
        sources.append(("seeded-random-color-frame", None))
    return sources, random_seed


def generate(out: Path, width: int, height: int,
             roi: tuple[int, int, int, int], profile: str,
             include_random_color_frame: bool = True,
             random_seed: int = 20261012) -> dict:
    validate_geometry(width, height, roi)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    x0, y0, x1, y1 = roi
    sources, random_seed = frame_sources(
        profile, width, height, include_random_color_frame, random_seed
    )
    file_paths = {
        "rgb": out / "vectors_rgb.hex",
        "gray": out / "vectors_gray.hex",
        "input_tags": out / "input_tags.hex",
        "expected_chain0": out / "expected_chain0.hex",
        "expected_chain1": out / "expected_chain1.hex",
    }
    beats = 0
    expected_count = 0
    h3_manifest = []

    with (file_paths["rgb"].open("w", encoding="ascii", newline="\n") as rgb_file,
          file_paths["gray"].open("w", encoding="ascii", newline="\n") as gray_file,
          file_paths["input_tags"].open("w", encoding="ascii", newline="\n") as tag_file,
          file_paths["expected_chain0"].open("w", encoding="ascii", newline="\n") as chain0_file,
          file_paths["expected_chain1"].open("w", encoding="ascii", newline="\n") as chain1_file):
        for frame_index, (source_name, source_path) in enumerate(sources):
            if source_name == "seeded-random-color-frame":
                pixels: Iterable[tuple[int, int, int]] = random_color_frame(
                    width, height, random_seed
                )
            elif profile == "coordinate":
                pixels = coordinate_frame(width, height, frame_index)
            else:
                assert source_path is not None
                if not source_path.is_file():
                    raise FileNotFoundError(source_path)
                pixels = h3_frame(source_path, width, height)
                h3_manifest.append({
                    "path": str(source_path.relative_to(ROOT)).replace("/", "\\"),
                    "sha256": sha256(source_path),
                })

            pixel_index = 0
            for pixel_index, (red, green, blue) in enumerate(pixels):
                y, x = divmod(pixel_index, width)
                if y >= height:
                    raise ValueError(f"frame source has more than {width * height} pixels: {source_name}")
                gray = gray_from_rgb(red, green, blue)
                sof = int(x == 0 and y == 0)
                eol = int(x == width - 1)
                rgb_file.write(f"{red:02x}{green:02x}{blue:02x}\n")
                gray_file.write(f"{gray:02x}\n")
                tag_file.write(f"{(sof << 9) | (eol << 8):03x}\n")
                if x0 <= x < x1 and y0 <= y < y1:
                    crop_sof = int(x == x0 and y == y0)
                    crop_eol = int(x == x1 - 1)
                    prefix = (crop_sof << 9) | (crop_eol << 8)
                    chain0_file.write(f"{prefix | gray:03x}\n")
                    binary = 0xFF if gray >= THRESHOLD else 0x00
                    chain1_file.write(f"{prefix | binary:03x}\n")
                    expected_count += 1
                beats += 1
            if pixel_index + 1 != width * height:
                raise ValueError(
                    f"frame source yielded {pixel_index + 1} pixels, expected {width * height}: {source_name}"
                )

    files = {
        name: {"path": path.name, "sha256": sha256(path), "bytes": path.stat().st_size}
        for name, path in file_paths.items()
    }
    manifest = {
        "schema": "material-counter-roi-crop-v1",
        "source_profile": profile,
        "frame_width": width,
        "frame_height": height,
        "frame_count": len(sources),
        "input_beats": beats,
        "roi": {"x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "width": x1 - x0, "height": y1 - y0},
        "output_beats": expected_count,
        "threshold": THRESHOLD,
        "gray_formula": "(77*R + 150*G + 29*B) // 256",
        "sideband_encoding": "packed10 = {SOF, EOL, data[7:0]}",
        "random_color_frame": {
            "included": profile == "h3" and include_random_color_frame,
            "seed": random_seed if profile == "h3" and include_random_color_frame else None,
        },
        "h3_frames": h3_manifest,
        "files": files,
    }
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--profile", choices=("coordinate", "h3"), required=True)
    parser.add_argument("--frame-width", type=int, required=True)
    parser.add_argument("--frame-height", type=int, required=True)
    parser.add_argument("--roi-x0", type=int, required=True)
    parser.add_argument("--roi-y0", type=int, required=True)
    parser.add_argument("--roi-x1", type=int, required=True)
    parser.add_argument("--roi-y1", type=int, required=True)
    parser.add_argument("--random-seed", type=int, default=20261012)
    parser.add_argument("--no-random-color-frame", action="store_true")
    args = parser.parse_args()
    generate(
        args.out,
        args.frame_width,
        args.frame_height,
        (args.roi_x0, args.roi_y0, args.roi_x1, args.roi_y1),
        args.profile,
        include_random_color_frame=not args.no_random_color_frame,
        random_seed=args.random_seed,
    )


if __name__ == "__main__":
    main()
