"""Generate WO-0014 AXI DMA packet and Binary8 golden vectors.

Beat file records are exactly five bytes: TDATA[7:0], TDATA[15:8],
TDATA[23:16], TDATA[31:24], then {3'b0, TLAST, TKEEP[3:0]}.  This is a
compact little-endian encoding of the 37-bit stream input.

Expected output records are 16-bit little-endian values; only bits [9:0] are
used: bit 9 SOF, bit 8 EOL, bits [7:0] Binary8.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image


ROOT = Path(__file__).resolve().parents[4]
H3_ROOT = ROOT / "data/synthetic/h3_full"
H3_WIDTH = 320
H3_HEIGHT = 240
DIRECTED_WIDTH = 5
DIRECTED_HEIGHT = 3
MAGIC = 0x574F3134
TRAILER_MAGIC = 0x454E4434
TRAILER_END = 0xC0DEC014
DIRECTED_THRESHOLDS = (0, 73, 128, 200, 255)
EXPECTED_H3_PNG_COUNT = 487


@dataclass(frozen=True)
class Beat:
    data: int
    keep: int = 0xF
    last: int = 0

    def encode(self) -> bytes:
        if not 0 <= self.data <= 0xFFFFFFFF:
            raise ValueError("TDATA must fit 32 bits")
        if not 0 <= self.keep <= 0xF:
            raise ValueError("TKEEP must fit 4 bits")
        if self.last not in (0, 1):
            raise ValueError("TLAST must be 0 or 1")
        return struct.pack("<IB", self.data, (self.last << 4) | self.keep)


def gray_from_rgb(rgb: Sequence[int]) -> int:
    if len(rgb) != 3 or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 255
                            for v in rgb):
        raise ValueError("RGB pixel must contain three unsigned bytes")
    red, green, blue = rgb
    return (77 * red + 150 * green + 29 * blue) // 256


def binary_pixel(gray: int, threshold: int) -> int:
    if not 0 <= threshold <= 255:
        raise ValueError("threshold must be in [0,255]")
    return 0xFF if gray >= threshold else 0x00


def packed_output(rgb: Sequence[int], width: int, height: int,
                  threshold: int) -> list[int]:
    if width < 1 or height < 1 or len(rgb) != width * height * 3:
        raise ValueError("RGB byte count must equal width*height*3")
    result = []
    for pixel_index in range(width * height):
        start = 3 * pixel_index
        gray = gray_from_rgb(rgb[start:start + 3])
        x = pixel_index % width
        word = ((pixel_index == 0) << 9) | ((x == width - 1) << 8)
        result.append(word | binary_pixel(gray, threshold))
    return result


def expected_bytes(rgb: Sequence[int], width: int, height: int,
                   threshold: int) -> bytes:
    return b"".join(struct.pack("<H", word)
                    for word in packed_output(rgb, width, height, threshold))


def packet_beats(rgb: bytes, width: int, height: int, frame_id: int) -> list[Beat]:
    if width < 1 or height < 1 or len(rgb) != 3 * width * height:
        raise ValueError("RGB payload size must equal 3*width*height")
    if not 0 <= frame_id <= 0xFFFFFFFF:
        raise ValueError("frame_id must fit 32 bits")
    words = [
        Beat(MAGIC),
        Beat(((height & 0xFFFF) << 16) | (width & 0xFFFF)),
        Beat(len(rgb)),
        Beat(frame_id),
    ]
    for offset in range(0, len(rgb), 4):
        chunk = rgb[offset:offset + 4]
        words.append(Beat(int.from_bytes(chunk.ljust(4, b"\0"), "little"),
                          (1 << len(chunk)) - 1, 0))
    checksum = sum(rgb) & 0xFFFFFFFF
    words.extend((Beat(TRAILER_MAGIC), Beat(frame_id), Beat(checksum),
                  Beat(TRAILER_END, 0xF, 1)))
    return words


def packet_bytes(beats: Iterable[Beat]) -> bytes:
    return b"".join(beat.encode() for beat in beats)


def directed_rgb(width: int = DIRECTED_WIDTH, height: int = DIRECTED_HEIGHT) -> bytes:
    """Color/equality vectors for all threshold variants in one small frame."""
    colors = [
        # The 5x3 default uses every sample once: primaries plus equality and
        # adjacent values around thresholds 73, 128, and 200. Black/white cover
        # threshold equality at 0 and 255. Its 45-byte payload ends with TKEEP=1.
        (0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0), (0, 0, 255),
    ] + [(v, v, v) for v in (72, 73, 74, 127, 128, 129, 199, 200, 201, 254)]
    pixels = [colors[i % len(colors)] for i in range(width * height)]
    return bytes(component for pixel in pixels for component in pixel)


def h3_pngs(root: Path = H3_ROOT) -> list[Path]:
    files = sorted(root.glob("*/frames/frame_*.png"))
    if len(files) != EXPECTED_H3_PNG_COUNT:
        raise ValueError(f"expected {EXPECTED_H3_PNG_COUNT} H3 PNGs, found {len(files)} under {root}")
    return files


def read_h3_rgb(path: Path, width: int = H3_WIDTH,
                height: int = H3_HEIGHT) -> tuple[bytes, dict]:
    raw = path.read_bytes()
    with Image.open(path) as image:
        if image.mode != "L" or image.size != (width, height):
            raise ValueError(f"expected Gray8 {width}x{height} H3 PNG: {path}")
        gray = image.tobytes()
    # Gray8 H3 becomes an explicit RGB888 test packet, not a reinterpretation
    # of the separate WO-0013 Binary8+trailer replay fixture.
    rgb = bytes(component for value in gray for component in (value, value, value))
    record = {
        "path": path.resolve().relative_to(ROOT.resolve()).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "width": width,
        "height": height,
        "mode": "L",
    }
    return rgb, record


def _write_case(path: Path, bad: Sequence[Beat], recovery: Sequence[Beat],
                drain: Sequence[Beat] = ()) -> dict:
    stream = list(bad) + list(drain) + list(recovery)
    path.write_bytes(packet_bytes(stream))
    return {
        "file": path.name,
        "bad_records": len(bad),
        "drain_records": len(drain),
        "recovery_records": len(recovery),
        "total_records": len(stream),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def make_negative_cases(out: Path, width: int = DIRECTED_WIDTH,
                        height: int = DIRECTED_HEIGHT) -> dict:
    rgb = directed_rgb(width, height)
    valid = packet_beats(rgb, width, height, 0xA014)
    payload_words = (len(rgb) + 3) // 4
    cases_dir = out / "negative"
    cases_dir.mkdir(parents=True, exist_ok=True)
    cases: dict[str, dict] = {}

    bad = list(valid)
    bad[0] = Beat(MAGIC ^ 1)
    cases["bad_header"] = {"error_code": 1, **_write_case(cases_dir / "bad_header.bin", bad, valid)}

    bad = list(valid)
    bad[2] = Beat(len(rgb) - 1)
    cases["bad_length"] = {"error_code": 2, **_write_case(cases_dir / "bad_length.bin", bad, valid)}

    bad = list(valid)
    bad[4] = Beat(bad[4].data, 0b0101, 0)
    cases["bad_tkeep"] = {"error_code": 3, **_write_case(cases_dir / "bad_tkeep.bin", bad, valid)}

    # End the malformed packet at the first payload beat.  Its accepted TLAST
    # lets the parser leave drain mode before the following valid recovery.
    early = list(valid[:5])
    early[-1] = Beat(early[-1].data, early[-1].keep, 1)
    cases["early_tlast"] = {"error_code": 4,
                            **_write_case(cases_dir / "early_tlast.bin", early, valid)}

    bad = list(valid)
    bad[-1] = Beat(bad[-1].data, bad[-1].keep, 0)
    # With no TLAST at the declared packet end, one following packet acts as
    # the accepted drain delimiter; a second packet proves valid recovery.
    cases["missing_tlast"] = {"error_code": 4,
                              **_write_case(cases_dir / "missing_tlast.bin", bad, valid, valid)}

    bad = list(valid)
    checksum_index = 4 + payload_words + 2
    bad[checksum_index] = Beat(bad[checksum_index].data ^ 1)
    cases["bad_checksum"] = {"error_code": 5,
                             **_write_case(cases_dir / "bad_checksum.bin", bad, valid)}

    # Insert an unexpected payload word where the trailer magic is required.
    extra_index = 4 + payload_words
    bad = list(valid[:extra_index]) + [Beat(0xDEADBEEF)] + list(valid[extra_index:])
    cases["extra_payload"] = {"error_code": 5,
                              **_write_case(cases_dir / "extra_payload.bin", bad, valid)}

    report = {
        "format": "5-byte beat records: TDATA little-endian, control byte bits[4]=TLAST,[3:0]=TKEEP",
        "geometry": {"width": width, "height": height},
        "cases": cases,
    }
    (cases_dir / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def make_partial_keep_fixtures(out: Path, threshold: int = 128) -> dict:
    """Write valid 2x1/TKEEP=3 and 1x1/TKEEP=7 packet/output fixtures."""
    fixtures: dict[str, dict] = {}
    for name, width, height in (("keep3", 2, 1), ("keep7", 1, 1)):
        rgb = directed_rgb(width, height)
        packet_path = out / f"partial_{name}_packets.bin"
        expected_path = out / f"partial_{name}_expected_th{threshold}.bin"
        beats = packet_beats(rgb, width, height, 0xA014)
        packet_data = packet_bytes(beats)
        expected_data = expected_bytes(rgb, width, height, threshold)
        packet_path.write_bytes(packet_data)
        expected_path.write_bytes(expected_data)
        payload_beats = beats[4:-4]
        fixtures[name] = {
            "width": width,
            "height": height,
            "frames": 1,
            "frame_id": 0xA014,
            "payload_bytes": len(rgb),
            "final_payload_tkeep": payload_beats[-1].keep,
            "input_file": packet_path.name,
            "input_sha256": hashlib.sha256(packet_data).hexdigest(),
            "expected_file": expected_path.name,
            "expected_sha256": hashlib.sha256(expected_data).hexdigest(),
        }
    return fixtures


def generate(out: Path, threshold: int = 128, h3_root: Path = H3_ROOT,
             limit_h3_frames: int | None = None) -> dict:
    if not 0 <= threshold <= 255:
        raise ValueError("threshold must be in [0,255]")
    out.mkdir(parents=True, exist_ok=False)
    directed = directed_rgb()
    (out / "directed_packets.bin").write_bytes(
        packet_bytes(packet_beats(directed, DIRECTED_WIDTH, DIRECTED_HEIGHT, 0xA014)))
    directed_expected = {}
    for directed_threshold in DIRECTED_THRESHOLDS:
        directed_path = out / f"directed_expected_th{directed_threshold}.bin"
        directed_path.write_bytes(expected_bytes(directed, DIRECTED_WIDTH,
                                                 DIRECTED_HEIGHT, directed_threshold))
        directed_expected[str(directed_threshold)] = directed_path.name

    pngs = h3_pngs(h3_root)
    if limit_h3_frames is not None:
        if limit_h3_frames < 1 or limit_h3_frames > len(pngs):
            raise ValueError("limit_h3_frames must be in [1,487]")
        pngs = pngs[:limit_h3_frames]

    packet_path = out / "h3_packets.bin"
    expected_path = out / f"h3_expected_th{threshold}.bin"
    source_records = []
    packet_hash = hashlib.sha256()
    expected_hash = hashlib.sha256()
    with packet_path.open("wb") as packet_file, expected_path.open("wb") as expected_file:
        for frame_id, path in enumerate(pngs):
            rgb, source = read_h3_rgb(path)
            encoded = packet_bytes(packet_beats(rgb, H3_WIDTH, H3_HEIGHT, frame_id))
            golden = expected_bytes(rgb, H3_WIDTH, H3_HEIGHT, threshold)
            packet_file.write(encoded)
            expected_file.write(golden)
            packet_hash.update(encoded)
            expected_hash.update(golden)
            binary_checksum = sum(
                word & 0xFF for word in packed_output(rgb, H3_WIDTH, H3_HEIGHT, threshold)
            ) & 0xFFFFFFFF
            source_records.append({
                **source,
                "frame_id": frame_id,
                "payload_bytes": len(rgb),
                "packet_beats": len(encoded) // 5,
                "binary_checksum": binary_checksum,
            })

    negative = make_negative_cases(out)
    partial_keep = make_partial_keep_fixtures(out, threshold)
    manifest = {
        "schema": 1,
        "record_format": "5 bytes/beat: TDATA little-endian bytes, control byte {3'b0,TLAST,TKEEP}",
        "expected_format": "16-bit little-endian; bits[9]=SOF,[8]=EOL,[7:0]=Binary8",
        "header_words": [f"0x{MAGIC:08x}", "(HEIGHT<<16)|WIDTH", "3*WIDTH*HEIGHT", "frame_id"],
        "trailer_words": [f"0x{TRAILER_MAGIC:08x}", "frame_id", "sum(payload bytes) mod 2^32",
                          f"0x{TRAILER_END:08x}"],
        "payload_byte_order": "row-major R,G,B bytes across ascending TDATA byte lanes",
        "threshold": threshold,
        "gray_formula": "(77*R+150*G+29*B)//256",
        "binary_rule": "0xff if gray>=threshold else 0x00",
        "directed": {
            "width": DIRECTED_WIDTH, "height": DIRECTED_HEIGHT, "frames": 1,
            "input_file": "directed_packets.bin",
            "expected_files_by_threshold": directed_expected,
            "packet_sha256": hashlib.sha256((out / "directed_packets.bin").read_bytes()).hexdigest(),
        },
        "h3": {
            "width": H3_WIDTH, "height": H3_HEIGHT, "frames": len(source_records),
            "png_count_in_repository": EXPECTED_H3_PNG_COUNT,
            "input_file": "h3_packets.bin", "input_sha256": packet_hash.hexdigest(),
            "expected_file": expected_path.name, "expected_sha256": expected_hash.hexdigest(),
            "source_pngs": source_records,
        },
        "negative_manifest": "negative/manifest.json",
        "negative_case_count": len(negative["cases"]),
        "partial_keep_fixtures": partial_keep,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(out), "directed_frames": 1,
        "h3_frames": len(source_records), "h3_expected_pngs": EXPECTED_H3_PNG_COUNT,
        "h3_packet_bytes": (out / "h3_packets.bin").stat().st_size,
        "h3_expected_bytes": expected_path.stat().st_size,
        "negative_cases": len(negative["cases"]),
    }, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--threshold", type=int, default=128)
    parser.add_argument("--h3-root", type=Path, default=H3_ROOT)
    parser.add_argument("--limit-h3-frames", type=int,
                        help="small diagnostic output only; default generates all 487 PNGs")
    args = parser.parse_args()
    generate(args.out.resolve(), args.threshold, args.h3_root.resolve(), args.limit_h3_frames)
