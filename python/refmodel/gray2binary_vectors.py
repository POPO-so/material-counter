"""Build threshold vectors from the existing, unchanged RGB-to-Gray corpus."""
import argparse
import hashlib
import json
from pathlib import Path

# Include multi-bit comparator cases; 0/128/255 alone simplify heavily in RTL.
THRESHOLDS = (0, 73, 128, 200, 255)
DIRECT_GRAY = tuple(range(256))


def binary(gray_value: int, threshold: int) -> int:
    """Return the contract result: Gray >= threshold is white (0xff)."""
    if isinstance(threshold, bool) or not isinstance(threshold, int):
        raise TypeError("threshold must be an integer")
    if threshold < 0 or threshold > 255:
        raise ValueError(f"THRESHOLD must be in [0,255], got {threshold}")
    if isinstance(gray_value, bool) or not isinstance(gray_value, int):
        raise TypeError("gray_value must be an integer")
    if gray_value < 0 or gray_value > 255:
        raise ValueError(f"Gray8 must be in [0,255], got {gray_value}")
    return 0xFF if gray_value >= threshold else 0x00


def validate_threshold_contract() -> None:
    """Pin the comparator meaning below, at and above selected thresholds."""
    expected = {
        0: {0: 0xFF, 127: 0xFF, 128: 0xFF, 129: 0xFF, 255: 0xFF},
        73: {0: 0x00, 72: 0x00, 73: 0xFF, 74: 0xFF, 255: 0xFF},
        128: {0: 0x00, 127: 0x00, 128: 0xFF, 129: 0xFF, 255: 0xFF},
        200: {0: 0x00, 199: 0x00, 200: 0xFF, 201: 0xFF, 255: 0xFF},
        255: {0: 0x00, 127: 0x00, 128: 0x00, 129: 0x00, 255: 0xFF},
    }
    for threshold, cases in expected.items():
        for gray_value, want in cases.items():
            got = binary(gray_value, threshold)
            if got != want:
                raise AssertionError(
                    f"threshold contract error: threshold={threshold}, "
                    f"gray={gray_value}, got=0x{got:02x}, expected=0x{want:02x}"
                )
        if any(binary(value, threshold) not in (0x00, 0xFF) for value in range(256)):
            raise AssertionError(f"non-binary output for threshold={threshold}")

    for invalid in (-1, 256):
        try:
            binary(128, invalid)
        except ValueError:
            continue
        raise AssertionError(f"invalid threshold {invalid} was accepted by the reference")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gray_from_rgb(rgb: int) -> int:
    """WO-0006 integer Gray model, evaluated independently from its output file."""
    red = (rgb >> 16) & 0xFF
    green = (rgb >> 8) & 0xFF
    blue = rgb & 0xFF
    return (77 * red + 150 * green + 29 * blue) // 256


def expected_tag(index: int) -> int:
    """Rebuild SOF/EOL positions for the explicit sweep, directed colors, and H3."""
    if index < len(DIRECT_GRAY):
        local = index
        sof = local == 0
        eol = local % 32 == 31
    elif index < len(DIRECT_GRAY) + 512:
        local = index - len(DIRECT_GRAY)
        sof = local == 0
        eol = local % 32 == 31
    else:
        local = (index - len(DIRECT_GRAY) - 512) % (320 * 240)
        sof = local == 0
        eol = local % 320 == 319
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


def generate(rgb_vectors: Path, out: Path) -> dict:
    validate_threshold_contract()
    rgb_vectors = rgb_vectors.resolve()
    out = out.resolve()
    if not rgb_vectors.is_dir():
        raise FileNotFoundError(f"RGB vector directory does not exist: {rgb_vectors}")
    out.mkdir(parents=True, exist_ok=False)

    base_manifest = json.loads((rgb_vectors / "manifest.json").read_text(encoding="utf-8"))
    base_rgb = read_hex(rgb_vectors / "vectors_rgb.hex", 6)
    base_gray_expected = read_hex(rgb_vectors / "expected_gray.hex", 3)
    if len(base_rgb) != base_manifest["beats"] or len(base_gray_expected) != len(base_rgb):
        raise ValueError("RGB/Gray vector count does not match the existing manifest")
    if base_manifest.get("directed_color_beats") != 512 or len(base_manifest.get("h3_frames", [])) != 3:
        raise ValueError("Expected the unchanged 512-color plus three-frame H3 vector corpus")

    direct_rgb = [value * 0x010101 for value in DIRECT_GRAY]
    rgb_inputs = direct_rgb + base_rgb
    gray_inputs = [gray_from_rgb(rgb) for rgb in rgb_inputs]
    tags = [expected_tag(i) for i in range(len(rgb_inputs))]

    if len(gray_inputs) != len(rgb_inputs) or len(tags) != len(rgb_inputs):
        raise AssertionError("Gray and RGB datasets must have matching beat counts")
    for index, word in enumerate(base_gray_expected):
        dataset_index = index + len(DIRECT_GRAY)
        if (word & 0xFF) != gray_inputs[dataset_index]:
            raise AssertionError(f"WO-0006 Gray golden differs from independent RGB model at beat {index}")
        if (word & 0x300) != tags[dataset_index]:
            raise AssertionError(f"WO-0006 sideband differs from vector contract at beat {index}")
    for value in DIRECT_GRAY:
        if rgb_inputs[value] != value * 0x010101 or gray_inputs[value] != value:
            raise AssertionError(f"direct Gray8 coverage missing at value {value}")

    gray_path = out / "vectors_gray.hex"
    rgb_path = out / "vectors_rgb2binary.hex"
    gray_path.write_text("".join(f"{value:02x}\n" for value in gray_inputs), encoding="ascii")
    rgb_path.write_text("".join(f"{value:06x}\n" for value in rgb_inputs), encoding="ascii")

    for threshold in THRESHOLDS:
        directory = out / f"threshold-{threshold}"
        directory.mkdir()
        expected_path = directory / "expected_binary.hex"
        packed = [tag | binary(value, threshold) for tag, value in zip(tags, gray_inputs)]
        if len(packed) != len(rgb_inputs):
            raise AssertionError("Expected output count mismatch")
        expected_path.write_text("".join(f"{word:03x}\n" for word in packed), encoding="ascii")
        for gray_value, want in ((0, binary(0, threshold)), (127, binary(127, threshold)),
                                 (128, binary(128, threshold)), (129, binary(129, threshold)),
                                 (255, binary(255, threshold))):
            if (packed[gray_value] & 0xFF) != want:
                raise AssertionError(f"boundary vector mismatch at threshold={threshold}, gray={gray_value}")

    manifest = {
        "beats": len(rgb_inputs),
        "direct_gray_beats": len(DIRECT_GRAY),
        "directed_color_beats": base_manifest["directed_color_beats"],
        "h3_frames": base_manifest["h3_frames"],
        "gray_input": {"path": gray_path.name, "sha256": sha256(gray_path)},
        "rgb_input": {"path": rgb_path.name, "sha256": sha256(rgb_path)},
        "thresholds": list(THRESHOLDS),
        "output_rule": "0xff if Gray >= THRESHOLD else 0x00",
        "sideband_bits": "expected_binary[9]=SOF, expected_binary[8]=EOL",
        "source_rgb_manifest_sha256": sha256(rgb_vectors / "manifest.json"),
        "source_rgb_manifest": base_manifest,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rgb-vectors", type=Path, required=True,
                        help="directory produced by python/refmodel/rgb2gray_vectors.py")
    parser.add_argument("--out", type=Path, required=True,
                        help="new output directory")
    args = parser.parse_args()
    generate(args.rgb_vectors, args.out)
