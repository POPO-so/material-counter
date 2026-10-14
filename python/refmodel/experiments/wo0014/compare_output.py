"""Compare testbench Binary8 output against independent Python frame models.

Files are consumed one frame at a time so the complete 487-frame H3 run does
not require loading its roughly 75 MiB output into Python memory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from image_vectors import H3_ROOT, directed_rgb, expected_bytes, read_h3_rgb


def _frame_metrics(data: bytes, golden: bytes, frame_index: int,
                   width: int, height: int) -> dict:
    if len(data) != len(golden) or len(data) % 2:
        raise AssertionError("frame output and golden sizes must match even byte counts")
    pixels = width * height
    if len(data) != 2 * pixels:
        raise AssertionError(f"frame byte count {len(data)} != expected {2 * pixels}")

    checksum = 0
    sofs = 0
    eols = 0
    mismatch_count = 0
    first_mismatch = None
    for index in range(pixels):
        offset = 2 * index
        word = data[offset] | (data[offset + 1] << 8)
        expected = golden[offset] | (golden[offset + 1] << 8)
        if word != expected:
            mismatch_count += 1
            if first_mismatch is None:
                first_mismatch = {
                    "frame": frame_index,
                    "pixel": index,
                    "actual": f"0x{word:03x}",
                    "expected": f"0x{expected:03x}",
                }
        if word & 0xFC00:
            raise AssertionError(f"reserved output bits set at frame={frame_index}, pixel={index}")
        pixel = word & 0xFF
        if pixel not in (0x00, 0xFF):
            raise AssertionError(f"non-Binary8 output at frame={frame_index}, pixel={index}")
        x = index % width
        sof = (word >> 9) & 1
        eol = (word >> 8) & 1
        sofs += sof
        eols += eol
        if bool(sof) != (index == 0):
            raise AssertionError(f"SOF location mismatch at frame={frame_index}, pixel={index}")
        if bool(eol) != (x == width - 1):
            raise AssertionError(f"EOL location mismatch at frame={frame_index}, pixel={index}")
        checksum = (checksum + pixel) & 0xFFFFFFFF

    if mismatch_count:
        raise AssertionError(f"{mismatch_count} word mismatches; first={first_mismatch}")
    return {
        "frame": frame_index,
        "pixels": pixels,
        "sofs": sofs,
        "eols": eols,
        "binary_checksum": checksum,
        "word_mismatches": mismatch_count,
    }


def compare_frames(actual_path: Path, expected_path: Path, width: int, height: int,
                   frame_sources: list[dict] | None = None) -> dict:
    """Compare streams in bounded per-frame chunks and report whole-file hashes."""
    actual_size = actual_path.stat().st_size
    expected_size = expected_path.stat().st_size
    if actual_size != expected_size:
        raise AssertionError(f"output length {actual_size} != golden length {expected_size} bytes")
    frame_bytes = 2 * width * height
    if width <= 0 or height <= 0 or actual_size % frame_bytes:
        raise AssertionError("output byte count does not contain complete positive-size frames")
    frame_count = actual_size // frame_bytes
    if frame_sources is not None and len(frame_sources) != frame_count:
        raise AssertionError(f"source frame count {len(frame_sources)} != output frames {frame_count}")

    actual_hash = hashlib.sha256()
    expected_hash = hashlib.sha256()
    reports = []
    with actual_path.open("rb") as actual_file, expected_path.open("rb") as expected_file:
        for frame_index in range(frame_count):
            actual_frame = actual_file.read(frame_bytes)
            expected_frame = expected_file.read(frame_bytes)
            if len(actual_frame) != frame_bytes or len(expected_frame) != frame_bytes:
                raise AssertionError(f"short read in frame {frame_index}")
            actual_hash.update(actual_frame)
            expected_hash.update(expected_frame)
            reports.append(_frame_metrics(actual_frame, expected_frame, frame_index, width, height))

    return {
        "status": "PASS",
        "width": width,
        "height": height,
        "frames": frame_count,
        "pixels": frame_count * width * height,
        "word_mismatches": 0,
        "actual_sha256": actual_hash.hexdigest(),
        "expected_sha256": expected_hash.hexdigest(),
        "frames_report": reports,
    }


def verify_against_inputs(actual_path: Path, expected_path: Path,
                          manifest_path: Path, kind: str,
                          threshold: int | None = None) -> dict:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if kind == "directed":
        record = manifest["directed"]
        selected_threshold = threshold if threshold is not None else manifest["threshold"]
        rgb = directed_rgb(record["width"], record["height"])
        independent = expected_bytes(rgb, record["width"], record["height"], selected_threshold)
        if expected_path.read_bytes() != independent:
            raise AssertionError("directed expected stream differs from independent Python RGB/threshold model")
        return compare_frames(actual_path, expected_path, record["width"], record["height"])
    if kind != "h3":
        raise ValueError("kind must be 'directed' or 'h3'")

    record = manifest["h3"]
    sources = record["source_pngs"]
    result = compare_frames(actual_path, expected_path, record["width"], record["height"], sources)
    if record["frames"] != result["frames"]:
        raise AssertionError("manifest H3 frame count differs from comparison")
    if result["expected_sha256"] != record["expected_sha256"]:
        raise AssertionError("H3 expected stream hash differs from manifest")

    selected_threshold = threshold if threshold is not None else manifest["threshold"]
    if selected_threshold != manifest["threshold"]:
        raise ValueError("H3 expected file threshold does not match the manifest threshold")

    # Recompute every source hash and golden a frame at a time.
    with expected_path.open("rb") as expected_file:
        for frame_index, source in enumerate(sources):
            source_name = Path(source["path"]).as_posix()
            h3_prefix = "data/synthetic/h3_full/"
            if not source_name.startswith(h3_prefix):
                raise AssertionError(f"unexpected H3 source path in manifest: {source_name}")
            source_path = H3_ROOT / source_name[len(h3_prefix):]
            raw_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if raw_hash != source["sha256"]:
                raise AssertionError(f"H3 source hash changed: {source_path}")
            rgb, _ = read_h3_rgb(source_path, record["width"], record["height"])
            modeled = expected_bytes(rgb, record["width"], record["height"], selected_threshold)
            stored = expected_file.read(len(modeled))
            if stored != modeled:
                raise AssertionError(f"Python golden mismatch in H3 frame {frame_index}: {source['path']}")
        if expected_file.read(1):
            raise AssertionError("H3 expected stream has trailing bytes")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual", type=Path, required=True,
                        help="binary 16-bit little-endian output records written by the testbench")
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--kind", choices=("directed", "h3"), required=True)
    parser.add_argument("--threshold", type=int)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = verify_against_inputs(args.actual.resolve(), args.expected.resolve(),
                                   args.manifest.resolve(), args.kind, args.threshold)
    encoded = json.dumps(report, indent=2)
    if args.report:
        args.report.write_text(encoded, encoding="utf-8")
    print(encoded)
