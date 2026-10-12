"""Compare XSim AXI beats with an independent Gray8 threshold oracle."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
FRAME_WIDTH = 320
FRAME_HEIGHT = 240
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT
DIRECT_GRAY_BEATS = 256
DIRECTED_RGB_BEATS = 512


def threshold_pixel(gray_value: int, threshold: int) -> int:
    if threshold < 0 or threshold > 255:
        raise ValueError(f"THRESHOLD must be in [0,255], got {threshold}")
    return 0xFF if gray_value >= threshold else 0x00


def gray_from_rgb(rgb: int) -> int:
    """Independent WO-0006 reference used to validate the serialized chain input."""
    red = (rgb >> 16) & 0xFF
    green = (rgb >> 8) & 0xFF
    blue = rgb & 0xFF
    return (77 * red + 150 * green + 29 * blue) // 256


def expected_tag(index: int) -> int:
    if index < DIRECT_GRAY_BEATS:
        local = index
        sof = local == 0
        eol = local % 32 == 31
    elif index < DIRECT_GRAY_BEATS + DIRECTED_RGB_BEATS:
        local = index - DIRECT_GRAY_BEATS
        sof = local == 0
        eol = local % 32 == 31
    else:
        local = (index - DIRECT_GRAY_BEATS - DIRECTED_RGB_BEATS) % FRAME_BYTES
        sof = local == 0
        eol = local % FRAME_WIDTH == FRAME_WIDTH - 1
    return (int(sof) << 9) | (int(eol) << 8)


def read_hex(path: Path, width: int) -> list[int]:
    values = []
    for line_number, raw in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        token = raw.strip()
        if len(token) != width:
            raise ValueError(f"{path}:{line_number}: expected {width} hex digits, got {token!r}")
        try:
            values.append(int(token, 16))
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: invalid hexadecimal value {token!r}") from exc
    return values


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(vectors: Path, run: Path, thresholds: tuple[int, ...] | None = None) -> list[dict]:
    vectors = vectors.resolve()
    run = run.resolve()
    manifest = json.loads((vectors / "manifest.json").read_text(encoding="utf-8"))
    beat_count = manifest["beats"]
    gray_path = vectors / manifest["gray_input"]["path"]
    rgb_path = vectors / manifest["rgb_input"]["path"]
    gray_inputs = read_hex(gray_path, 2)
    rgb_inputs = read_hex(rgb_path, 6)
    if len(gray_inputs) != beat_count or len(rgb_inputs) != beat_count:
        raise AssertionError("input vector length differs from the manifest")
    if sha256(gray_path) != manifest["gray_input"]["sha256"]:
        raise AssertionError("Gray input vectors changed after manifest creation")
    if sha256(rgb_path) != manifest["rgb_input"]["sha256"]:
        raise AssertionError("RGB input vectors changed after manifest creation")
    if manifest.get("direct_gray_beats") != DIRECT_GRAY_BEATS:
        raise AssertionError("the direct sweep must contain all 256 Gray8 values")
    expected_count = DIRECT_GRAY_BEATS + DIRECTED_RGB_BEATS + 3 * FRAME_BYTES
    if beat_count != expected_count:
        raise AssertionError(f"expected {expected_count} beats, manifest has {beat_count}")
    computed_gray = [gray_from_rgb(rgb) for rgb in rgb_inputs]
    for index, (standalone_gray, chain_gray) in enumerate(zip(gray_inputs, computed_gray)):
        if standalone_gray != chain_gray:
            raise AssertionError(
                f"standalone Gray input disagrees with RGB reference at beat {index}: "
                f"gray={standalone_gray}, rgb_model={chain_gray}"
            )

    selected_thresholds = tuple(thresholds or manifest["thresholds"])
    if selected_thresholds != tuple(manifest["thresholds"]):
        raise ValueError("checker thresholds must match the vector manifest")

    # Assert the five requested values explicitly before inspecting simulator output.
    boundary_values = (0, 127, 128, 129, 255)
    results = []
    for threshold in selected_thresholds:
        expected_path = vectors / f"threshold-{threshold}" / "expected_binary.hex"
        expected = read_hex(expected_path, 3)
        if len(expected) != beat_count:
            raise AssertionError(f"threshold={threshold}: expected vector count mismatch")
        for gray_value in boundary_values:
            want = threshold_pixel(gray_value, threshold)
            if (expected[gray_value] & 0xFF) != want:
                raise AssertionError(
                    f"threshold={threshold}: boundary gray={gray_value} has "
                    f"0x{expected[gray_value] & 0xff:02x}, expected 0x{want:02x}"
                )
        for index, gray_value in enumerate(computed_gray):
            golden_word = expected_tag(index) | threshold_pixel(gray_value, threshold)
            if expected[index] != golden_word:
                raise AssertionError(
                    f"threshold={threshold}: golden beat mismatch at {index}: "
                    f"gray={gray_value}, got={expected[index]:03x}, expected={golden_word:03x}"
                )
        for chain in (0, 1):
            for mode in (0, 1):
                output_path = run / f"output-th{threshold}-chain{chain}-mode{mode}.hex"
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
                        f"threshold={threshold} chain={chain} mode={mode}: "
                        f"count={len(actual)}/{len(expected)} data_mismatches={data_mismatches} "
                        f"sideband_mismatches={sideband_mismatches} first={first_mismatch}"
                    )

                frame_results = []
                h3_start = DIRECT_GRAY_BEATS + DIRECTED_RGB_BEATS
                for frame_index, source in enumerate(manifest["h3_frames"]):
                    source_path = ROOT / source["path"]
                    if sha256(source_path) != source["sha256"]:
                        raise AssertionError("H3 source changed since WO-0006 vector generation: " + str(source_path))
                    with Image.open(source_path) as image:
                        if image.mode != "L" or image.size != (FRAME_WIDTH, FRAME_HEIGHT):
                            raise AssertionError("unexpected H3 frame format: " + str(source_path))
                        gray_frame = image.tobytes()
                    start = h3_start + frame_index * FRAME_BYTES
                    modeled_gray_frame = bytes(computed_gray[start:start + FRAME_BYTES])
                    if modeled_gray_frame != gray_frame:
                        raise AssertionError(
                            f"RGB-to-Gray reference does not reconstruct H3 source: {source['path']}"
                        )
                    binary_frame = bytes(value & 0xFF for value in actual[start:start + FRAME_BYTES])
                    model_frame = bytes(threshold_pixel(value, threshold) for value in modeled_gray_frame)
                    if len(binary_frame) != FRAME_BYTES or binary_frame != model_frame:
                        raise AssertionError(
                            f"threshold={threshold} chain={chain} mode={mode}: "
                            f"reconstructed H3 frame mismatch: {source['path']}"
                        )
                    frame_output = run / f"binary-th{threshold}-chain{chain}-mode{mode}-frame{frame_index}.png"
                    Image.frombytes("L", (FRAME_WIDTH, FRAME_HEIGHT), binary_frame).save(frame_output)
                    frame_results.append({"source": source["path"], "pixels": FRAME_BYTES,
                                          "reference_match": True, "output": frame_output.name})

                results.append({
                    "threshold": threshold,
                    "chain": chain,
                    "mode": mode,
                    "beats": len(actual),
                    "data_mismatches": data_mismatches,
                    "sideband_mismatches": sideband_mismatches,
                    "h3_frames_reference_match": len(frame_results),
                    "output_sha256": sha256(output_path),
                    "frames": frame_results,
                })

    result_path = run / "pixel-comparison.json"
    result_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("PIXEL_COMPARISON_PASS " + json.dumps(results, separators=(",", ":")))
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    check(args.vectors, args.run)
