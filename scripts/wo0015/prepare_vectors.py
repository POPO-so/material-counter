"""Build bounded WO-0015 Gray8 packet and independent Binary8 vectors.

The script only reads the H3 PNG set and its WO-0014 source-hash manifest.
All generated files go under the required --out directory; the source data is
never modified. Each beats.hex line is a 40-bit record:
{3'b000, TLAST, TKEEP[3:0], TDATA[31:0]}.
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


REPO_ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_ROOT = Path("C:/fpga/material-counter")
WIDTH = 320
HEIGHT = 240
PIXELS = WIDTH * HEIGHT
THRESHOLD = 128
EXPECTED_PNGS = 487
MAGIC = 0x47525938
TRAILER_MAGIC = 0x454E4438
TRAILER_END = 0xC0DEC018
DEFAULT_MANIFEST_REL = Path("scripts/wo0015/fixtures/wo0014-h3-source-manifest.json")
NORMAL_CASES = {
    "A": (0,),
    "B": (0, 1, 2),
    "C": (19, 121, 486),
}


@dataclass(frozen=True)
class Beat:
    data: int
    keep: int = 0xF
    last: int = 0

    def encode_hex(self) -> str:
        if not 0 <= self.data <= 0xFFFFFFFF:
            raise ValueError("TDATA must fit 32 bits")
        if not 0 <= self.keep <= 0xF or self.last not in (0, 1):
            raise ValueError("invalid TKEEP/TLAST")
        value = ((self.last << 4) | self.keep) << 32 | self.data
        return f"{value:010x}\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def binary8_from_gray(gray: bytes, threshold: int = THRESHOLD) -> bytes:
    if not 0 <= threshold <= 255:
        raise ValueError("threshold must be in [0,255]")
    return bytes(0xFF if value >= threshold else 0x00 for value in gray)


def make_packet(gray: bytes, width: int = WIDTH, height: int = HEIGHT,
                frame_id: int = 0) -> list[Beat]:
    if width < 1 or height < 1 or len(gray) != width * height:
        raise ValueError("Gray8 payload size must equal width*height")
    if not 0 <= frame_id <= 0xFFFFFFFF:
        raise ValueError("frame_id must fit 32 bits")
    words = [
        Beat(MAGIC),
        Beat(((height & 0xFFFF) << 16) | (width & 0xFFFF)),
        Beat(len(gray)),
        Beat(frame_id),
    ]
    for offset in range(0, len(gray), 4):
        chunk = gray[offset:offset + 4]
        words.append(Beat(int.from_bytes(chunk.ljust(4, b"\0"), "little"),
                          (1 << len(chunk)) - 1))
    words.extend((Beat(TRAILER_MAGIC), Beat(frame_id),
                  Beat(sum(gray) & 0xFFFFFFFF), Beat(TRAILER_END, 0xF, 1)))
    return words


def packets_hex(beats: Iterable[Beat]) -> str:
    return "".join(beat.encode_hex() for beat in beats)


def resolve_inputs(root: Path, h3_root_arg: Path | None,
                   manifest_arg: Path | None) -> tuple[Path, Path]:
    if h3_root_arg is not None:
        h3_root = h3_root_arg.resolve()
    else:
        h3_root = (root / "data/synthetic/h3_full").resolve()
        if not list(h3_root.glob("*/frames/frame_*.png")):
            h3_root = (ORIGINAL_ROOT / "data/synthetic/h3_full").resolve()

    if manifest_arg is not None:
        manifest_path = manifest_arg.resolve()
    else:
        manifest_path = (root / DEFAULT_MANIFEST_REL).resolve()
    if not h3_root.is_dir():
        raise FileNotFoundError(f"H3 directory not found: {h3_root}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"WO-0014 source manifest not found: {manifest_path}")
    return h3_root, manifest_path


def manifest_source_path(h3_root: Path, record_path: str) -> Path:
    parts = Path(record_path).parts
    if len(parts) < 4 or tuple(parts[:3]) != ("data", "synthetic", "h3_full"):
        raise ValueError(f"unexpected H3 source path in manifest: {record_path}")
    return h3_root.joinpath(*parts[3:])


def validate_source_manifest(h3_root: Path, manifest_path: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    h3 = manifest.get("h3", {})
    records = h3.get("source_pngs", [])
    if h3.get("width") != WIDTH or h3.get("height") != HEIGHT:
        raise ValueError("source manifest geometry is not 320x240")
    if h3.get("frames") != EXPECTED_PNGS or h3.get("png_count_in_repository") != EXPECTED_PNGS:
        raise ValueError("source manifest does not identify the complete 487-frame H3 set")
    if len(records) != EXPECTED_PNGS:
        raise ValueError(f"source manifest has {len(records)} PNG records, expected {EXPECTED_PNGS}")

    files = sorted(h3_root.glob("*/frames/frame_*.png"))
    if len(files) != EXPECTED_PNGS:
        raise ValueError(f"found {len(files)} H3 PNGs under {h3_root}, expected {EXPECTED_PNGS}")
    actual_paths = {
        "data/synthetic/h3_full/" + path.relative_to(h3_root).as_posix()
        for path in files
    }
    manifest_paths = {record.get("path") for record in records}
    if actual_paths != manifest_paths:
        missing = sorted(manifest_paths - actual_paths)[:3]
        extra = sorted(actual_paths - manifest_paths)[:3]
        raise ValueError(f"H3 manifest path set mismatch missing={missing} extra={extra}")

    by_path: dict[str, dict] = {}
    for record in records:
        path = manifest_source_path(h3_root, record["path"])
        raw = path.read_bytes()
        actual_hash = sha256_bytes(raw)
        if actual_hash != record.get("sha256"):
            raise ValueError(f"H3 PNG hash mismatch for {record['path']}")
        if record.get("width") != WIDTH or record.get("height") != HEIGHT or record.get("mode") != "L":
            raise ValueError(f"H3 manifest image metadata mismatch for {record['path']}")
        by_path[record["path"]] = {**record, "resolved_path": str(path)}
    ordered_records = [by_path[
        "data/synthetic/h3_full/" + path.relative_to(h3_root).as_posix()
    ] for path in files]
    return manifest, ordered_records


def read_gray_png(record: dict) -> bytes:
    path = Path(record["resolved_path"])
    with Image.open(path) as image:
        if image.mode != "L" or image.size != (WIDTH, HEIGHT):
            raise ValueError(f"expected Gray8 320x240 image: {path}")
        return image.tobytes()


def frame_vectors(gray: bytes, frame_id: int) -> tuple[list[Beat], bytes, dict]:
    if len(gray) != PIXELS:
        raise ValueError(f"expected {PIXELS} Gray8 pixels, found {len(gray)}")
    golden = binary8_from_gray(gray)
    packet = make_packet(gray, WIDTH, HEIGHT, frame_id)
    stats = {
        "frame_id": frame_id,
        "source_pixels_sha256": sha256_bytes(gray),
        "gray8_payload_sha256": sha256_bytes(gray),
        "golden_sha256": sha256_bytes(golden),
        "foreground_pixels": golden.count(0xFF),
        "background_pixels": golden.count(0x00),
        "payload_bytes": len(gray),
        "packet_beats": len(packet),
    }
    return packet, golden, stats


def write_case(out_root: Path, name: str, selected: Sequence[tuple[int, dict]],
               kind: str, *, malformed: str | None = None) -> dict:
    case_dir = out_root / name
    case_dir.mkdir(parents=True, exist_ok=False)
    all_beats: list[Beat] = []
    all_golden: list[bytes] = []
    frame_records: list[dict] = []
    for frame_id, (source_index, source) in enumerate(selected):
        gray = read_gray_png(source)
        packet, golden, frame_stats = frame_vectors(gray, frame_id)
        if malformed:
            if len(selected) != 1:
                raise ValueError("malformed cases contain exactly one frame")
            payload_words = (PIXELS + 3) // 4
            payload_start = 4
            if malformed == "short":
                cutoff = payload_start + payload_words // 2
                all_beats = packet[:cutoff]
                last = all_beats[-1]
                all_beats[-1] = Beat(last.data, last.keep, 1)
            elif malformed == "long":
                insertion = payload_start + payload_words
                all_beats = packet[:insertion] + [Beat(0x7EADCAFE)] + packet[insertion:]
            elif malformed == "checksum":
                all_beats = list(packet)
                checksum_index = payload_start + payload_words + 2
                original = all_beats[checksum_index]
                all_beats[checksum_index] = Beat(original.data ^ 1, original.keep, original.last)
            else:
                raise ValueError(f"unknown malformed packet kind {malformed}")
        else:
            all_beats.extend(packet)
        all_golden.append(golden)
        frame_records.append({"source_index": source_index, **source,
                              "resolved_path": source["resolved_path"], **frame_stats})

    beats_path = case_dir / "beats.hex"
    golden_bin_path = case_dir / "golden.bin"
    golden_hex_path = case_dir / "golden.hex"
    with beats_path.open("w", encoding="ascii", newline="\n") as stream:
        stream.write(packets_hex(all_beats))
    golden_bytes = b"".join(all_golden)
    golden_bin_path.write_bytes(golden_bytes)
    with golden_hex_path.open("w", encoding="ascii", newline="\n") as stream:
        stream.write("".join(f"{value:02x}\n" for value in golden_bytes))
    case_manifest = {
        "schema": 1,
        "case": name,
        "kind": kind,
        "mode": {"normal": 0, "random": 1, "fault": 2, "reset": 3}[kind],
        "geometry": {"width": WIDTH, "height": HEIGHT, "pixels": PIXELS},
        "threshold": THRESHOLD,
        "expected_frames": len(selected) if kind in ("normal", "random") else 1,
        "expected_eofs": len(selected) if kind in ("normal", "random") else 0,
        "expected_fault": kind == "fault",
        "expected_reset_clear": kind == "reset",
        "malformed_kind": malformed,
        "input_beat_count": len(all_beats),
        "input_records_sha256": sha256_bytes(beats_path.read_bytes()),
        "golden_bytes": len(golden_bytes),
        "golden_sha256": sha256_bytes(golden_bytes),
        "source_frames": frame_records,
        "record_format": "10 hex digits: {3'b000,TLAST,TKEEP[3:0],TDATA[31:0]}",
        "golden_format": "one Binary8 byte per line in golden.hex; packed bytes in golden.bin",
    }
    (case_dir / "case.json").write_text(json.dumps(case_manifest, indent=2) + "\n",
                                        encoding="utf-8")
    return case_manifest


def generate(out_dir: Path, root: Path = REPO_ROOT,
             h3_root_arg: Path | None = None,
             source_manifest_arg: Path | None = None,
             case_names: Sequence[str] = ("A", "B", "C", "D_short", "D_long",
                                          "D_checksum", "D_reset")) -> dict:
    if out_dir.exists():
        if any(out_dir.iterdir()):
            raise FileExistsError(f"output directory must be new/empty: {out_dir}")
    else:
        out_dir.mkdir(parents=True, exist_ok=False)
    h3_root, source_manifest_path = resolve_inputs(root.resolve(), h3_root_arg,
                                                    source_manifest_arg)
    source_manifest, sources = validate_source_manifest(h3_root, source_manifest_path)
    requested = set(case_names)
    valid_names = set(NORMAL_CASES) | {"D_short", "D_long", "D_checksum", "D_reset"}
    unknown = requested - valid_names
    if unknown:
        raise ValueError(f"unknown case names: {sorted(unknown)}")

    generated_cases: dict[str, dict] = {}
    for name, indexes in NORMAL_CASES.items():
        if name in requested:
            generated_cases[name] = write_case(
                out_dir, name, [(index, sources[index]) for index in indexes],
                "random" if name == "C" else "normal")
    for name, malformed in (("D_short", "short"), ("D_long", "long"),
                            ("D_checksum", "checksum")):
        if name in requested:
            generated_cases[name] = write_case(out_dir, name, [(0, sources[0])],
                                                "fault", malformed=malformed)
    if "D_reset" in requested:
        generated_cases["D_reset"] = write_case(out_dir, "D_reset", [(0, sources[0])],
                                                  "reset")

    if "B" in generated_cases and "C" in generated_cases:
        b_hashes = [record["golden_sha256"] for record in generated_cases["B"]["source_frames"]]
        c_hashes = [record["golden_sha256"] for record in generated_cases["C"]["source_frames"]]
        if len(set(b_hashes)) != 3 or len(set(c_hashes)) != 3:
            raise ValueError("B/C test frames must have three distinct Binary8 goldens")

    source_manifest_sha = sha256_bytes(source_manifest_path.read_bytes())
    for name, case_manifest in generated_cases.items():
        case_manifest["source_manifest_sha256"] = source_manifest_sha
        (out_dir / name / "case.json").write_text(
            json.dumps(case_manifest, indent=2) + "\n", encoding="utf-8")

    summary = {
        "schema": 1,
        "source_h3_root": str(h3_root),
        "source_manifest": str(source_manifest_path.resolve()),
        "source_manifest_sha256": source_manifest_sha,
        "source_png_count": len(sources),
        "source_manifest_h3_packet_sha256": source_manifest["h3"]["input_sha256"],
        "source_manifest_h3_binary_sha256": source_manifest["h3"]["expected_sha256"],
        "geometry": {"width": WIDTH, "height": HEIGHT, "pixels": PIXELS},
        "input_format": "row-major Gray8, one byte per pixel",
        "binary_rule": "0xff if gray>=128 else 0x00",
        "cases": generated_cases,
    }
    (out_dir / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n",
                                            encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT,
                        help="project root used to locate H3 data and the WO-0014 manifest")
    parser.add_argument("--out", type=Path, required=True,
                        help="new output directory; original H3 data is read-only")
    parser.add_argument("--h3-root", type=Path,
                        help="override the source directory containing H3 condition folders")
    parser.add_argument("--source-manifest", type=Path,
                        help="override the WO-0014 vectors/manifest.json source hash list")
    parser.add_argument("--cases", nargs="+", default=["A", "B", "C", "D_short",
                                                         "D_long", "D_checksum", "D_reset"],
                        choices=["A", "B", "C", "D_short", "D_long",
                                 "D_checksum", "D_reset"])
    args = parser.parse_args()
    summary = generate(args.out.resolve(), args.root.resolve(),
                       args.h3_root, args.source_manifest, args.cases)
    print(json.dumps({"output": str(args.out.resolve()),
                      "source_pngs_validated": summary["source_png_count"],
                      "cases": {name: {"beats": case["input_beat_count"],
                                        "golden_bytes": case["golden_bytes"],
                                        "golden_sha256": case["golden_sha256"]}
                                for name, case in summary["cases"].items()}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
