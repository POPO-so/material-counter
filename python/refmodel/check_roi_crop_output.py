"""Independently compare an ROI simulation dump with its coordinate oracle."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
THRESHOLD = 128


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_hex(path: Path, digits: int) -> list[int]:
    values = []
    for line_number, raw in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        token = raw.strip()
        if len(token) != digits:
            raise ValueError(f"{path}:{line_number}: expected {digits} hex digits, got {token!r}")
        try:
            values.append(int(token, 16))
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: invalid hexadecimal value {token!r}") from exc
    return values


def gray_from_rgb(rgb: int) -> int:
    red = (rgb >> 16) & 0xFF
    green = (rgb >> 8) & 0xFF
    blue = rgb & 0xFF
    return (77 * red + 150 * green + 29 * blue) // 256


def coordinate_gray(frame: int, x: int, y: int) -> int:
    return (frame * 67 + y * 29 + x * 11 + x * y * 3 + (x ^ y) * 5) & 0xFF


def verify_source_frames(manifest: dict, rgb_values: list[int], gray_values: list[int]) -> dict:
    width = manifest["frame_width"]
    height = manifest["frame_height"]
    area = width * height
    frame_count = manifest["frame_count"]
    profile = manifest["source_profile"]
    checked = {"h3_frames": [], "coordinate_frames": 0, "random_color_frame": False}

    if profile == "coordinate":
        if frame_count != 3:
            raise AssertionError(f"coordinate profile should contain 3 frames, got {frame_count}")
        for frame in range(frame_count):
            start = frame * area
            for local in range(area):
                y, x = divmod(local, width)
                expected = coordinate_gray(frame, x, y)
                if rgb_values[start + local] != expected * 0x010101 or gray_values[start + local] != expected:
                    raise AssertionError(
                        f"coordinate source mismatch at frame={frame} x={x} y={y}"
                    )
        checked["coordinate_frames"] = frame_count
        return checked

    if profile != "h3":
        raise AssertionError(f"unknown source profile: {profile}")
    if (width, height) != (320, 240):
        raise AssertionError(f"H3 profile has unexpected dimensions: {width}x{height}")
    h3_frames = manifest.get("h3_frames", [])
    if len(h3_frames) != 3:
        raise AssertionError(f"expected the unchanged three H3 sources, got {len(h3_frames)}")

    for frame_index, source in enumerate(h3_frames):
        source_path = ROOT / Path(source["path"])
        if sha256(source_path) != source["sha256"]:
            raise AssertionError(f"H3 source changed after vector generation: {source_path}")
        with Image.open(source_path) as image:
            if image.mode != "L" or image.size != (width, height):
                raise AssertionError(f"unexpected H3 source format: {source_path}")
            pixels = image.tobytes()
        start = frame_index * area
        if len(pixels) != area:
            raise AssertionError(f"H3 source pixel count mismatch: {source_path}")
        for local, expected in enumerate(pixels):
            if rgb_values[start + local] != expected * 0x010101 or gray_values[start + local] != expected:
                raise AssertionError(
                    f"serialized H3 source differs at frame={frame_index} pixel={local}: {source_path}"
                )
        checked["h3_frames"].append({
            "path": source["path"], "sha256": source["sha256"], "pixels": area,
            "source_match": True,
        })

    random_info = manifest.get("random_color_frame", {})
    if random_info.get("included"):
        if frame_count != 4:
            raise AssertionError("random H3 color frame should follow the three unchanged H3 frames")
        rng = random.Random(random_info["seed"])
        start = 3 * area
        for local in range(area):
            red, green, blue = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            rgb = (red << 16) | (green << 8) | blue
            if rgb_values[start + local] != rgb or gray_values[start + local] != gray_from_rgb(rgb):
                raise AssertionError(f"seeded random-color frame mismatch at pixel={local}")
        checked["random_color_frame"] = True
    elif frame_count != 3:
        raise AssertionError(f"H3 profile has {frame_count} frames without its optional color frame")
    return checked


def check(vectors: Path, run: Path, chain: int, mode: int,
          output: Path | None = None) -> dict:
    vectors = vectors.resolve()
    run = run.resolve()
    manifest_path = vectors / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "material-counter-roi-crop-v1":
        raise AssertionError(f"unexpected vector schema in {manifest_path}")
    if chain not in (0, 1) or mode not in (0, 1):
        raise ValueError("chain and mode must each be 0 or 1")

    files = manifest["files"]
    loaded = {}
    for name, digits in (("rgb", 6), ("gray", 2), ("input_tags", 3),
                         ("expected_chain0", 3), ("expected_chain1", 3)):
        path = vectors / files[name]["path"]
        if sha256(path) != files[name]["sha256"]:
            raise AssertionError(f"vector file hash mismatch: {path}")
        loaded[name] = read_hex(path, digits)
    beat_count = manifest["input_beats"]
    if any(len(values) != beat_count for key, values in loaded.items()
           if key in ("rgb", "gray", "input_tags")):
        raise AssertionError("an input vector file differs from manifest input_beats")

    width = manifest["frame_width"]
    height = manifest["frame_height"]
    frame_area = width * height
    frame_count = manifest["frame_count"]
    roi = manifest["roi"]
    x0, y0, x1, y1 = roi["x0"], roi["y0"], roi["x1"], roi["y1"]
    if beat_count != frame_count * frame_area:
        raise AssertionError("input beat count is not an integer number of full raster frames")
    if (roi["width"], roi["height"]) != (x1 - x0, y1 - y0):
        raise AssertionError("ROI dimensions do not match its exclusive-bound coordinates")

    rgb_values = loaded["rgb"]
    gray_values = loaded["gray"]
    input_tags = loaded["input_tags"]
    for index, (rgb, gray) in enumerate(zip(rgb_values, gray_values)):
        modeled = gray_from_rgb(rgb)
        if gray != modeled:
            raise AssertionError(
                f"Gray input disagrees with independent RGB model at beat {index}: "
                f"gray={gray:02x}, rgb_model={modeled:02x}"
            )
        local = index % frame_area
        x = local % width
        expected_tag = ((1 if local == 0 else 0) << 9) | ((1 if x == width - 1 else 0) << 8)
        if input_tags[index] != expected_tag:
            raise AssertionError(
                f"input SOF/EOL mismatch at beat {index}: got={input_tags[index]:03x}, "
                f"expected={expected_tag:03x}"
            )

    source_checks = verify_source_frames(manifest, rgb_values, gray_values)
    expected_chain0 = []
    expected_chain1 = []
    for frame_index in range(frame_count):
        frame_start = frame_index * frame_area
        for y in range(y0, y1):
            for x in range(x0, x1):
                index = frame_start + y * width + x
                crop_sof = int(x == x0 and y == y0)
                crop_eol = int(x == x1 - 1)
                tag = (crop_sof << 9) | (crop_eol << 8)
                gray = gray_from_rgb(rgb_values[index])
                expected_chain0.append(tag | gray)
                expected_chain1.append(tag | (0xFF if gray >= THRESHOLD else 0x00))

    if loaded["expected_chain0"] != expected_chain0:
        raise AssertionError("generated direct-Gray golden differs from independent coordinate model")
    if loaded["expected_chain1"] != expected_chain1:
        raise AssertionError("generated Binary128 golden differs from independent coordinate model")
    expected = expected_chain0 if chain == 0 else expected_chain1
    if len(expected) != manifest["output_beats"]:
        raise AssertionError("golden output beat count differs from manifest")

    output_path = output.resolve() if output is not None else run / "output_roi.hex"
    actual = read_hex(output_path, 3)
    data_mismatches = 0
    sideband_mismatches = 0
    first_mismatch = None
    common = min(len(expected), len(actual))
    for index in range(common):
        data_bad = (actual[index] & 0xFF) != (expected[index] & 0xFF)
        sideband_bad = (actual[index] & 0x300) != (expected[index] & 0x300)
        if data_bad:
            data_mismatches += 1
        if sideband_bad:
            sideband_mismatches += 1
        if (data_bad or sideband_bad) and first_mismatch is None:
            first_mismatch = {
                "beat": index,
                "expected": f"{expected[index]:03x}",
                "actual": f"{actual[index]:03x}",
            }
    length_mismatch = len(actual) != len(expected)
    if length_mismatch and first_mismatch is None:
        first_mismatch = {"beat": common, "expected_count": len(expected),
                          "actual_count": len(actual)}
    if length_mismatch or data_mismatches or sideband_mismatches:
        raise AssertionError(
            f"chain={chain} mode={mode}: count={len(actual)}/{len(expected)} "
            f"data_mismatches={data_mismatches} sideband_mismatches={sideband_mismatches} "
            f"first={first_mismatch}"
        )

    result = {
        "status": "PASS",
        "source_profile": manifest["source_profile"],
        "frame_dimensions": [width, height],
        "frame_count": frame_count,
        "roi": roi,
        "chain": chain,
        "mode": mode,
        "threshold": THRESHOLD if chain == 1 else None,
        "beats": len(actual),
        "data_mismatches": data_mismatches,
        "sideband_mismatches": sideband_mismatches,
        "input_beats": beat_count,
        "source_checks": source_checks,
        "output_path": str(output_path),
        "output_sha256": sha256(output_path),
    }
    (run / "pixel-comparison.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("ROI_PIXEL_COMPARISON_PASS " + json.dumps(result, separators=(",", ":")))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--chain", type=int, choices=(0, 1), required=True)
    parser.add_argument("--mode", type=int, choices=(0, 1), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    check(args.vectors, args.run, args.chain, args.mode, args.output)


if __name__ == "__main__":
    main()
