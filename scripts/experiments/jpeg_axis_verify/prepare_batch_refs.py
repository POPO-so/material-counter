#!/usr/bin/env python3
"""Create disposable OpenCV Gray8 references for the captured 120 JPEGs."""
import argparse
from pathlib import Path

import cv2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--last", type=int, default=120)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for frame_id in range(args.first, args.last + 1):
        stem = f"frame_{frame_id:06d}"
        src = args.input_dir / f"{stem}.jpg"
        image = cv2.imread(str(src), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(f"OpenCV could not decode {src}")
        if image.shape[:2] != (400, 640):
            raise RuntimeError(f"unexpected dimensions for {src}: {image.shape[1]}x{image.shape[0]}")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        out = args.output_dir / f"{stem}.gray8"
        out.write_bytes(gray.tobytes())
        print(f"{stem}: {out.stat().st_size} gray8 bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
