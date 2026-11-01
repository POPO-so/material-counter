#!/usr/bin/env python3
"""Create valid JPEG tail-alignment variants without changing captured inputs."""
import argparse
from pathlib import Path


def insert_app15_after_soi(jpeg: bytes, payload_size: int) -> bytes:
    if len(jpeg) < 4 or jpeg[:2] != b"\xff\xd8" or jpeg[-2:] != b"\xff\xd9":
        raise ValueError("expected a JPEG with SOI at start and EOI at end")
    if not 0 <= payload_size <= 3:
        raise ValueError("payload_size must be between 0 and 3")
    # APP15 length includes its two length bytes; APP markers are legal here
    # and ignored by the decoder, leaving the scan and entropy bytes untouched.
    segment = b"\xff\xef" + (payload_size + 2).to_bytes(2, "big") + bytes(payload_size)
    return jpeg[:2] + segment + jpeg[2:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--frame-id", type=int, default=1)
    args = ap.parse_args()
    original = args.input.read_bytes()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for residue in range(4):
        payload_size = (residue - len(original) % 4) % 4
        data = insert_app15_after_soi(original, payload_size)
        if data[2:4] != b"\xff\xef" or int.from_bytes(data[4:6], "big") != payload_size + 2:
            raise AssertionError("invalid APP15 marker or length")
        if data[6 + payload_size :] != original[2:]:
            raise AssertionError("bytes after APP15 differ from original JPEG")
        if len(data) % 4 != residue:
            raise AssertionError("failed to produce requested length residue")
        out = args.output_dir / f"frame_{args.frame_id:06d}_tailmod{residue}.jpg"
        out.write_bytes(data)
        print(f"{out} bytes={len(data)} length_mod4={len(data) % 4} app15_payload_bytes={payload_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
