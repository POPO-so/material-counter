"""Generate fixed raw input arrays for the unexecuted G3 scaling candidates."""

import argparse
from pathlib import Path


WIDTH = 640
HEIGHT = 400


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "scaling-inputs",
        help="directory for the three raw source arrays",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename, data in build_sources().items():
        (args.output_dir / filename).write_bytes(data)
        print(f"{filename}: {len(data)} bytes")


if __name__ == "__main__":
    main()
