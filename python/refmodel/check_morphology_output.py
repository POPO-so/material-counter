"""Independently verify morphology stream outputs and report offline image impact."""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
MODES = (0, 1, 2)
CHAINS = (0, 1)
READY_CASES = (0, 1)
THRESHOLD = 128
FOREGROUND = 0xFF
BACKGROUND = 0x00


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_source_path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def read_hex(path: Path, digits: int) -> list[int]:
    values = []
    for line_number, raw in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        token = raw.strip()
        if len(token) != digits:
            raise ValueError(f"{path}:{line_number}: expected {digits} hex digits, got {token!r}")
        try:
            value = int(token, 16)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: invalid hex token {token!r}") from exc
        values.append(value)
    return values


def gray_from_rgb(rgb: int) -> int:
    red = (rgb >> 16) & 0xFF
    green = (rgb >> 8) & 0xFF
    blue = rgb & 0xFF
    return (77 * red + 150 * green + 29 * blue) // 256


def threshold_gray(gray: int) -> int:
    return FOREGROUND if gray >= THRESHOLD else BACKGROUND


def recompute_frame(source: list[int], width: int, height: int, mode: int) -> list[int]:
    """Separate checker implementation of the zero-padded 3x3 operation."""
    if mode not in MODES:
        raise ValueError(f"unsupported morphology mode {mode}")
    if len(source) != width * height:
        raise ValueError("Binary8 frame length differs from width*height")
    if any(value not in (BACKGROUND, FOREGROUND) for value in source):
        raise ValueError("input contains a value other than Binary8 00/ff")
    if mode == 0:
        return source.copy()

    result: list[int] = []
    for center in range(width * height):
        y, x = divmod(center, width)
        hits = 0
        for row_delta in (-1, 0, 1):
            row = y + row_delta
            for col_delta in (-1, 0, 1):
                col = x + col_delta
                if 0 <= row < height and 0 <= col < width:
                    hits += int(source[row * width + col] == FOREGROUND)
        result.append(FOREGROUND if (hits == 9 if mode == 1 else hits > 0) else BACKGROUND)
    return result


def tag_for_pixel(frame_local: int, width: int) -> int:
    x = frame_local % width
    return (int(frame_local == 0) << 9) | (int(x == width - 1) << 8)


def pack_expected(binary_pixels: list[int], width: int, height: int, frames: int,
                  mode: int) -> list[int]:
    frame_size = width * height
    if len(binary_pixels) != frame_size * frames:
        raise ValueError("input count does not match scene frames and geometry")
    result = []
    for frame_index in range(frames):
        begin = frame_index * frame_size
        filtered = recompute_frame(binary_pixels[begin:begin + frame_size], width, height, mode)
        result.extend(tag_for_pixel(index, width) | value
                      for index, value in enumerate(filtered))
    return result


def component_sizes(pixels: list[int], width: int, height: int,
                    foreground: bool, eight_connected: bool) -> list[int]:
    target = FOREGROUND if foreground else BACKGROUND
    visited = bytearray(width * height)
    if eight_connected:
        offsets = tuple((dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                        if (dx, dy) != (0, 0))
    else:
        offsets = ((-1, 0), (1, 0), (0, -1), (0, 1))
    sizes = []
    for start, value in enumerate(pixels):
        if value != target or visited[start]:
            continue
        visited[start] = 1
        stack = [start]
        size = 0
        while stack:
            index = stack.pop()
            size += 1
            y, x = divmod(index, width)
            for dx, dy in offsets:
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    neighbor = ny * width + nx
                    if not visited[neighbor] and pixels[neighbor] == target:
                        visited[neighbor] = 1
                        stack.append(neighbor)
        sizes.append(size)
    return sizes


def hole_sizes(pixels: list[int], width: int, height: int) -> list[int]:
    """Count enclosed background regions using 4-neighbor flood fill."""
    reachable = bytearray(width * height)
    stack = []
    for x in range(width):
        for y in (0, height - 1):
            index = y * width + x
            if pixels[index] == BACKGROUND and not reachable[index]:
                reachable[index] = 1
                stack.append(index)
    for y in range(height):
        for x in (0, width - 1):
            index = y * width + x
            if pixels[index] == BACKGROUND and not reachable[index]:
                reachable[index] = 1
                stack.append(index)

    while stack:
        index = stack.pop()
        y, x = divmod(index, width)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height:
                neighbor = ny * width + nx
                if pixels[neighbor] == BACKGROUND and not reachable[neighbor]:
                    reachable[neighbor] = 1
                    stack.append(neighbor)

    hole_seen = bytearray(width * height)
    sizes = []
    for start, value in enumerate(pixels):
        if value != BACKGROUND or reachable[start] or hole_seen[start]:
            continue
        hole_seen[start] = 1
        stack = [start]
        size = 0
        while stack:
            index = stack.pop()
            size += 1
            y, x = divmod(index, width)
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    neighbor = ny * width + nx
                    if (pixels[neighbor] == BACKGROUND and not reachable[neighbor]
                            and not hole_seen[neighbor]):
                        hole_seen[neighbor] = 1
                        stack.append(neighbor)
        sizes.append(size)
    return sizes


def border_foreground_count(pixels: list[int], width: int, height: int) -> int:
    return sum(
        pixels[y * width + x] == FOREGROUND
        for y in range(height) for x in range(width)
        if x == 0 or x == width - 1 or y == 0 or y == height - 1
    )


def image_stats(pixels: list[int], width: int, height: int) -> dict:
    foreground_components = component_sizes(pixels, width, height, True, True)
    holes = hole_sizes(pixels, width, height)
    return {
        "foreground_pixels": sum(value == FOREGROUND for value in pixels),
        "foreground_components_8_connected": len(foreground_components),
        "foreground_component_sizes": sorted(foreground_components, reverse=True),
        "enclosed_background_regions_4_connected": len(holes),
        "enclosed_background_sizes": sorted(holes, reverse=True),
        "foreground_pixels_on_image_border": border_foreground_count(pixels, width, height),
    }


def verify_sources(vectors: Path, manifest: dict) -> dict:
    source_manifest_record = manifest.get("source_manifest")
    if not isinstance(source_manifest_record, dict):
        raise ValueError("manifest has no source_manifest path/hash record")
    source_manifest_path = resolve_source_path(source_manifest_record["path"])
    if sha256(source_manifest_path) != source_manifest_record.get("sha256"):
        raise AssertionError(f"source H3 manifest hash mismatch: {source_manifest_path}")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))

    source_pngs = manifest.get("source_pngs", [])
    if len(source_pngs) != 3:
        raise AssertionError(f"expected three source H3 PNGs, found {len(source_pngs)}")
    base_entries = source_manifest.get("h3_frames", [])
    if len(base_entries) != len(source_pngs):
        raise AssertionError("source H3 manifest and generated source list differ in length")

    checked = []
    for index, entry in enumerate(source_pngs):
        source_path = resolve_source_path(entry["path"])
        actual_hash = sha256(source_path)
        if actual_hash != entry.get("sha256"):
            raise AssertionError(f"source PNG hash mismatch: {source_path}")
        base_entry = base_entries[index]
        base_path = resolve_source_path(base_entry["path"])
        if (source_path != base_path or actual_hash != base_entry.get("sha256")):
            raise AssertionError(f"source PNG no longer matches original RGB-to-Gray manifest: {source_path}")
        checked.append({"path": entry["path"], "sha256": actual_hash, "verified": True})

    file_hashes = manifest.get("files_sha256")
    if not isinstance(file_hashes, dict) or not file_hashes:
        raise ValueError("manifest has no generated vector file hash records")
    for relative, expected_hash in file_hashes.items():
        path = vectors / relative
        if not path.is_file():
            raise FileNotFoundError(f"manifest vector file is missing: {path}")
        actual_hash = sha256(path)
        if actual_hash != expected_hash:
            raise AssertionError(f"generated vector hash mismatch: {relative}")
    return {
        "source_manifest": {"path": source_manifest_record["path"],
                            "sha256": source_manifest_record["sha256"], "verified": True},
        "source_pngs": checked,
        "generated_vector_files": len(file_hashes),
    }


def load_scene(vectors: Path, scene: dict) -> tuple[list[int], list[int], list[int]]:
    name = scene["name"]
    width, height, frames = scene["width"], scene["height"], scene["frames"]
    beat_count = width * height * frames
    if scene.get("beats") != beat_count:
        raise AssertionError(f"{name}: manifest beats disagree with geometry")
    rgb = read_hex(vectors / scene["rgb"], 6)
    binary = read_hex(vectors / scene["binary"], 2)
    if len(rgb) != beat_count or len(binary) != beat_count:
        raise AssertionError(f"{name}: RGB/Binary input counts do not match manifest")
    for index, pixel in enumerate(rgb):
        red, green, blue = (pixel >> 16) & 0xFF, (pixel >> 8) & 0xFF, pixel & 0xFF
        if red != green or green != blue:
            raise AssertionError(f"{name}: RGB input is not a repeated grayscale value at beat {index}")
        if name != "h3_threshold128" and red not in (BACKGROUND, FOREGROUND):
            raise AssertionError(f"{name}: directed RGB input is not 0/255 at beat {index}")
    modeled_binary = [threshold_gray(gray_from_rgb(pixel)) for pixel in rgb]
    if modeled_binary != binary:
        mismatch = next(i for i, pair in enumerate(zip(modeled_binary, binary)) if pair[0] != pair[1])
        raise AssertionError(f"{name}: RGB->Gray->Binary mismatch at beat {mismatch}")
    return rgb, binary, modeled_binary


def verify_h3_pixels(vectors: Path, manifest: dict,
                     loaded: dict[str, tuple[list[int], list[int], list[int]]]) -> int:
    h3_scenes = [scene for scene in manifest["scenes"] if scene["name"] == "h3_threshold128"]
    if len(h3_scenes) != 1:
        raise AssertionError("manifest must have exactly one h3_threshold128 scene")
    scene = h3_scenes[0]
    rgb, binary, _ = loaded[scene["name"]]
    width, height = scene["width"], scene["height"]
    sources = manifest.get("source_pngs", [])
    if len(sources) != scene["frames"]:
        raise AssertionError("H3 source PNG count differs from the H3 frame count")
    checked_pixels = 0
    for frame_index, source in enumerate(sources):
        if source.get("scene") != scene["name"] or source.get("frame_index") != frame_index:
            raise AssertionError("H3 source records are not aligned with H3 frame order")
        source_path = resolve_source_path(source["path"])
        with Image.open(source_path) as image:
            if image.mode != "L" or image.size != (width, height):
                raise AssertionError(f"unexpected H3 source image shape or mode: {source_path}")
            source_gray = list(image.tobytes())
        begin = frame_index * width * height
        end = begin + width * height
        rgb_frame = rgb[begin:end]
        rgb_gray = [gray_from_rgb(value) for value in rgb_frame]
        if any(((value >> 16) & 0xFF) != ((value >> 8) & 0xFF)
               or ((value >> 8) & 0xFF) != (value & 0xFF) for value in rgb_frame):
            raise AssertionError(f"H3 RGB input is not Gray repeated across RGB channels: {source_path}")
        binary_slice = binary[begin:end]
        if rgb_gray != source_gray:
            raise AssertionError(f"RGB input does not reproduce H3 Gray PNG frame: {source_path}")
        if [threshold_gray(value) for value in source_gray] != binary_slice:
            raise AssertionError(f"H3 threshold-128 Binary8 input mismatch: {source_path}")
        checked_pixels += len(source_gray)
    return checked_pixels


def compare_output(actual: list[int], expected: list[int]) -> dict:
    data_mismatches = 0
    sideband_mismatches = 0
    first_mismatches = []
    common = min(len(actual), len(expected))
    for index in range(common):
        data_bad = (actual[index] & 0xFF) != (expected[index] & 0xFF)
        sideband_bad = (actual[index] & 0x300) != (expected[index] & 0x300)
        data_mismatches += int(data_bad)
        sideband_mismatches += int(sideband_bad)
        if (data_bad or sideband_bad) and len(first_mismatches) < 8:
            first_mismatches.append({
                "beat": index,
                "expected": f"{expected[index]:03x}",
                "actual": f"{actual[index]:03x}",
                "data_mismatch": data_bad,
                "sideband_mismatch": sideband_bad,
            })
    count_mismatch = len(actual) != len(expected)
    return {
        "expected_count": len(expected),
        "actual_count": len(actual),
        "count_mismatch": count_mismatch,
        "data_mismatches": data_mismatches,
        "sideband_mismatches": sideband_mismatches,
        "first_mismatches": first_mismatches,
        "passed": not count_mismatch and not data_mismatches and not sideband_mismatches,
    }


def verify_all_goldens(vectors: Path, scenes: list[dict],
                       loaded: dict[str, tuple[list[int], list[int], list[int]]]) -> tuple[dict, dict]:
    modeled_by_scene = {}
    verified_beats = 0
    mode_vectors = 0
    for scene in scenes:
        name = scene["name"]
        _, binary, _ = loaded[name]
        modeled_by_scene[name] = {}
        for mode in MODES:
            modeled = pack_expected(binary, scene["width"], scene["height"], scene["frames"], mode)
            saved = read_hex(vectors / scene["expected"][str(mode)], 3)
            if saved != modeled:
                mismatch = next((index for index, pair in enumerate(zip(saved, modeled))
                                if pair[0] != pair[1]), min(len(saved), len(modeled)))
                raise AssertionError(
                    f"{name} mode={mode}: stored golden differs from independent model at beat {mismatch}"
                )
            modeled_by_scene[name][mode] = modeled
            verified_beats += len(modeled)
            mode_vectors += 1
    return modeled_by_scene, {
        "scenes": len(scenes),
        "mode_vectors": mode_vectors,
        "beats_verified": verified_beats,
        "data_and_sidebands_verified": True,
    }


def build_feature_impact(vectors: Path, manifest: dict,
                         loaded: dict[str, tuple[list[int], list[int], list[int]]]) -> dict:
    scene_reports = []
    for scene in manifest["scenes"]:
        name = scene["name"]
        width, height = scene["width"], scene["height"]
        frame_size = width * height
        _, binary, _ = loaded[name]
        frame_reports = []
        for frame_index, label in enumerate(scene.get("frame_labels", [])):
            begin = frame_index * frame_size
            source = binary[begin:begin + frame_size]
            source_stats = image_stats(source, width, height)
            modes = {}
            for mode in MODES:
                filtered = recompute_frame(source, width, height, mode)
                stats = image_stats(filtered, width, height)
                modes[str(mode)] = {
                    "name": {"0": "bypass", "1": "erosion", "2": "dilation"}[str(mode)],
                    **stats,
                    "foreground_delta": stats["foreground_pixels"] - source_stats["foreground_pixels"],
                    "hole_regions_reduced": max(
                        0, source_stats["enclosed_background_regions_4_connected"]
                        - stats["enclosed_background_regions_4_connected"]
                    ),
                    "foreground_component_count_reduction": max(
                        0, source_stats["foreground_components_8_connected"]
                        - stats["foreground_components_8_connected"]
                    ),
                    "holes_filled_by_dilation": (
                        max(0, source_stats["enclosed_background_regions_4_connected"]
                            - stats["enclosed_background_regions_4_connected"])
                        if mode == 2 else None
                    ),
                    "possible_neighbor_adhesion_by_dilation": (
                        stats["foreground_components_8_connected"]
                        < source_stats["foreground_components_8_connected"]
                        if mode == 2 else None
                    ),
                }
            frame_reports.append({
                "frame_index": frame_index,
                "label": label,
                "input": source_stats,
                "modes": modes,
            })
        scene_reports.append({
            "name": name,
            "width": width,
            "height": height,
            "frames": frame_reports,
        })
    return {
        "classification": "offline synthetic/reference-image morphology effect only; not object-count accuracy",
        "note": "component and hole values are Python flood-fill diagnostics; they are not PL connected-component results",
        "statistics": {
            "foreground_connectivity": 8,
            "background_hole_connectivity": 4,
            "boundary_padding": "constant zero outside each frame",
        },
        "scenes": scene_reports,
    }


def check(vectors: Path, run: Path, selected_scenes: tuple[str, ...] | None = None) -> dict:
    vectors = vectors.resolve()
    run = run.resolve()
    manifest_path = vectors / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("threshold") != THRESHOLD:
        raise AssertionError(f"expected Gray threshold {THRESHOLD}")
    if manifest.get("foreground") != FOREGROUND or manifest.get("background") != BACKGROUND:
        raise AssertionError("manifest foreground/background encoding differs from Binary8 contract")
    if manifest.get("morphology", {}).get("boundary_padding") != "constant zero outside each frame":
        raise AssertionError("manifest boundary padding is not explicit constant zero")

    hash_report = verify_sources(vectors, manifest)
    all_scenes = manifest.get("scenes", [])
    if not all_scenes:
        raise AssertionError("manifest contains no scenes")
    scene_names = [scene["name"] for scene in all_scenes]
    if len(scene_names) != len(set(scene_names)):
        raise AssertionError("scene names are not unique")
    if selected_scenes is None:
        scenes = all_scenes
    else:
        unknown = set(selected_scenes) - set(scene_names)
        if unknown:
            raise ValueError("unknown scenes: " + ", ".join(sorted(unknown)))
        scenes = [scene for scene in all_scenes if scene["name"] in set(selected_scenes)]

    loaded = {}
    for scene in all_scenes:
        loaded[scene["name"]] = load_scene(vectors, scene)
    h3_pixels_checked = verify_h3_pixels(vectors, manifest, loaded)
    modeled_goldens, golden_verification = verify_all_goldens(vectors, all_scenes, loaded)

    results = []
    for scene in scenes:
        name = scene["name"]
        for mode in MODES:
            for chain in CHAINS:
                for ready in READY_CASES:
                    output_path = run / f"output-{name}-mode{mode}-chain{chain}-ready{ready}.hex"
                    actual = read_hex(output_path, 3)
                    comparison = compare_output(actual, modeled_goldens[name][mode])
                    results.append({
                        "scene": name,
                        "mode": mode,
                        "chain": chain,
                        "ready": ready,
                        "output": output_path.name,
                        "output_sha256": sha256(output_path),
                        **comparison,
                    })

    passed = all(result["passed"] for result in results)
    report = {
        "status": "PASS" if passed else "FAIL",
        "vectors_manifest_sha256": sha256(manifest_path),
        "source_hashes": hash_report,
        "h3_threshold_pixels_checked": h3_pixels_checked,
        "golden_vector_verification": golden_verification,
        "scene_count": len(scenes),
        "output_combinations": len(results),
        "passed_combinations": sum(result["passed"] for result in results),
        "failed_combinations": sum(not result["passed"] for result in results),
        "results": results,
    }
    run.mkdir(parents=True, exist_ok=True)
    (run / "pixel-comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    feature_impact = build_feature_impact(vectors, manifest, loaded)
    (run / "feature-impact.json").write_text(json.dumps(feature_impact, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "scenes": len(scenes),
        "output_combinations": len(results),
        "passed_combinations": report["passed_combinations"],
        "failed_combinations": report["failed_combinations"],
        "h3_threshold_pixels_checked": h3_pixels_checked,
        "pixel_comparison": str(run / "pixel-comparison.json"),
        "feature_impact": str(run / "feature-impact.json"),
    }, indent=2))
    if not passed:
        raise SystemExit("morphology output comparison failed; see pixel-comparison.json")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vectors", type=Path, required=True,
                        help="directory containing morphology manifest.json and vector files")
    parser.add_argument("--run", type=Path, required=True,
                        help="directory containing runner output-*.hex files")
    parser.add_argument("--scenes", type=str, default="all",
                        help="comma-separated scene names to inspect, or all")
    args = parser.parse_args()
    selected = None if args.scenes.strip().lower() == "all" else tuple(
        item.strip() for item in args.scenes.split(",") if item.strip()
    )
    check(args.vectors, args.run, selected)
