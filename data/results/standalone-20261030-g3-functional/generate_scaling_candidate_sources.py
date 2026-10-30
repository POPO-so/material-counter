"""Generate fixed G3 scaling inputs and unexecuted candidate reference arrays."""

import argparse
from fractions import Fraction
from pathlib import Path


WIDTH = 640
HEIGHT = 400
TARGET_WIDTH = 576
TARGET_HEIGHT = 360


def build_sources():
    constant = bytes([73]) * (WIDTH * HEIGHT)
    ramp = bytes(min(12 * x, 255) for _y in range(HEIGHT) for x in range(WIDTH))

    binary = bytearray(WIDTH * HEIGHT)
    for y in range(20, 23):
        for x in range(10, 13):
            binary[y * WIDTH + x] = 255
    binary[0 * WIDTH + 0] = 255
    binary[398 * WIDTH + 638] = 255

    return {
        "constant-gray-73.raw": constant,
        "gray-ramp-12.raw": ramp,
        "binary-nearest-rect-points.raw": bytes(binary),
    }


def build_binary_nearest_candidate(source):
    """Apply the recorded inverse index mapping to a C row-major source array."""
    output = bytearray(TARGET_WIDTH * TARGET_HEIGHT)
    for dst_y in range(TARGET_HEIGHT):
        src_y = (10 * dst_y) // 9
        for dst_x in range(TARGET_WIDTH):
            src_x = (10 * dst_x) // 9
            output[dst_y * TARGET_WIDTH + dst_x] = source[src_y * WIDTH + src_x]
    return bytes(output)


def build_constant_gray_candidate(source):
    """Expand the constant source value; this result is invariant to gray interpolation."""
    if len(source) != WIDTH * HEIGHT or any(value != source[0] for value in source):
        raise ValueError("constant-gray source must be a full-size constant array")
    return bytes([source[0]]) * (TARGET_WIDTH * TARGET_HEIGHT)


def round_fraction_ties_even(value):
    """Round a nonnegative Fraction to the nearest integer, with ties to even."""
    quotient, remainder = divmod(value.numerator, value.denominator)
    doubled_remainder = 2 * remainder
    if doubled_remainder > value.denominator or (
        doubled_remainder == value.denominator and quotient % 2 == 1
    ):
        quotient += 1
    return quotient


def build_gray_ramp_area_candidate(source):
    """Average exact overlap of each source pixel with every target column."""
    if len(source) != WIDTH * HEIGHT:
        raise ValueError("gray-ramp source must be a full-size array")
    output = bytearray(TARGET_WIDTH * TARGET_HEIGHT)
    for dst_x in range(TARGET_WIDTH):
        left = Fraction(10 * dst_x, 9)
        right = Fraction(10 * (dst_x + 1), 9)
        weighted_sum = Fraction(0)
        for src_x in range(left.numerator // left.denominator,
                            (right.numerator + right.denominator - 1) // right.denominator):
            overlap = min(right, Fraction(src_x + 1)) - max(left, Fraction(src_x))
            weighted_sum += overlap * source[src_x]
        value = round_fraction_ties_even(weighted_sum / (right - left))
        for dst_y in range(TARGET_HEIGHT):
            output[dst_y * TARGET_WIDTH + dst_x] = value
    return bytes(output)


def build_gray_ramp_linear_candidate(source):
    """Linearly interpolate at the specified exact horizontal sample coordinate."""
    if len(source) != WIDTH * HEIGHT:
        raise ValueError("gray-ramp source must be a full-size array")
    output = bytearray(TARGET_WIDTH * TARGET_HEIGHT)
    for dst_x in range(TARGET_WIDTH):
        coordinate = Fraction(20 * dst_x + 1, 18)
        left_x = coordinate.numerator // coordinate.denominator
        fraction = coordinate - left_x
        value = round_fraction_ties_even(
            (1 - fraction) * source[left_x] + fraction * source[left_x + 1]
        )
        for dst_y in range(TARGET_HEIGHT):
            output[dst_y * TARGET_WIDTH + dst_x] = value
    return bytes(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "scaling-inputs",
        help="directory for the three raw source arrays",
    )
    parser.add_argument(
        "--expected-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "scaling-expected-candidates",
        help="directory for derived candidate reference arrays",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    sources = build_sources()
    for filename, data in sources.items():
        (args.output_dir / filename).write_bytes(data)
        print(f"{filename}: {len(data)} bytes")

    binary_source_path = args.output_dir / "binary-nearest-rect-points.raw"
    binary_expected = build_binary_nearest_candidate(binary_source_path.read_bytes())
    args.expected_dir.mkdir(parents=True, exist_ok=True)
    expected_path = args.expected_dir / "binary-inter-nearest-576x360.raw"
    expected_path.write_bytes(binary_expected)
    print(f"{expected_path.name}: {len(binary_expected)} bytes")

    constant_source_path = args.output_dir / "constant-gray-73.raw"
    constant_expected = build_constant_gray_candidate(constant_source_path.read_bytes())
    constant_expected_path = args.expected_dir / "constant-gray-576x360.raw"
    constant_expected_path.write_bytes(constant_expected)
    print(f"{constant_expected_path.name}: {len(constant_expected)} bytes")

    ramp_source_path = args.output_dir / "gray-ramp-12.raw"
    ramp_source = ramp_source_path.read_bytes()
    for filename, builder in (
        ("gray-ramp-inter-area-576x360.raw", build_gray_ramp_area_candidate),
        ("gray-ramp-inter-linear-576x360.raw", build_gray_ramp_linear_candidate),
    ):
        expected_path = args.expected_dir / filename
        expected_path.write_bytes(builder(ramp_source))
        print(f"{expected_path.name}: {expected_path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
