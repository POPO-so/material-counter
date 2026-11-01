"""PROVISIONAL offline replay of the 450-frame camera capture through R5 RLE code.

This is a software-only replay. It is NOT a PL-equivalence claim: the binary mask
is derived from recorded camera Gray8 with an explicit global threshold, then
encoded into the same RLE packet format and fed to the committed RLE counter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.rle.protocol import encode_mask, decode_packet, reconstruct, WIDTH, HEIGHT
from scripts.rle.counter import RleCounter
from scripts.standalone.count_controls import FIELD_V1_SETTINGS


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def det_dict(d) -> dict:
    return {"x_min": d.x_min, "x_max": d.x_max, "y_min": d.y_min, "y_max": d.y_max,
            "area": d.area, "cx": round(d.centroid_x, 3), "cy": round(d.centroid_y, 3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", type=Path, required=True)
    ap.add_argument("--library", type=Path, required=True)
    ap.add_argument("--threshold", type=int, required=True)
    ap.add_argument("--max-split-area", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    z = np.load(args.npz, allow_pickle=False)
    gray = z["source_gray640x400"]
    mono = z["monotonic_ns"].astype(np.int64)
    n = int(gray.shape[0])
    assert gray.shape == (n, HEIGHT, WIDTH) and mono.shape == (n,)

    settings = dict(FIELD_V1_SETTINGS)
    settings["max_split_area"] = args.max_split_area
    counter = RleCounter(library=str(args.library), discard_finished_tracks=True,
                         count_axis="x", line_x=320, direction=1, **settings)

    frames = []
    started = time.perf_counter()
    for f in range(n):
        binary = np.where(gray[f] >= args.threshold, 255, 0).astype(np.uint8)
        packet = encode_mask(binary, frame_id=f, params_version=0,
                             roi=(0, 0, WIDTH, HEIGHT), timestamp=int(mono[f]),
                             threshold=args.threshold, intensity_offset=0, morph_mode=0)
        frame = decode_packet(packet, expected_frame_id=f)
        if not np.array_equal(reconstruct(frame), binary):
            raise RuntimeError(f"RLE round-trip mismatch at frame {f}")
        result = counter.update_rle(frame, capture_monotonic_ns=int(mono[f]))
        splits = len(getattr(counter, "_split_origins", {}))
        frames.append({
            "f": f,
            "mono_ns": int(mono[f]),
            "total": int(result.total_count),
            "fg": int(frame.total_fg),
            "runs": int(frame.run_count),
            "status": int(frame.status),
            "raw": [det_dict(d) for d in result.raw_detections],
            "det": [det_dict(d) for d in result.detections],
            "events": [{"track_id": e.track_id, "frame_id": e.frame_id,
                        "cx": round(e.centroid_x, 3), "cy": round(e.centroid_y, 3),
                        "inferred_gap": bool(e.inferred_gap)} for e in result.events],
            "suppressed": [{"track_id": e.track_id, "frame_id": e.frame_id,
                            "cx": round(e.centroid_x, 3), "cy": round(e.centroid_y, 3)}
                           for e in result.suppressed_events],
            "tracks": [{"id": t["track_id"], "cx": round(t["detection"]["centroid_x"], 3),
                        "cy": round(t["detection"]["centroid_y"], 3),
                        "counted": bool(t["counted"]), "armed": bool(t["armed"]),
                        "confirmed": bool(t["confirmed"]), "last": t["last_frame"],
                        "missed": t["missed"], "finished": bool(t["finished"])}
                       for t in result.tracks],
            "split_children": splits,
        })
    elapsed = time.perf_counter() - started

    report = {
        "schema": "material-counter.provisional-offline-replay.v1",
        "status": "PROVISIONAL_NOT_PL_EQUIVALENT",
        "session": "m8-first-10-20261102",
        "source_npz_sha256": sha256_file(args.npz),
        "input": {"width": WIDTH, "height": HEIGHT, "frames": n,
                  "source": "recorded camera Gray8, no PL Binary8"},
        "processing": {"threshold": args.threshold,
                       "comparison": f">= {args.threshold} on recorded Gray8",
                       "intensity_offset": 0, "morph_mode": 0,
                       "roi": [0, 0, WIDTH, HEIGHT],
                       "rle_protocol": "RLE_PACKET_V1 encode_mask -> decode_packet, MAX_RUNS=16384",
                       "max_split_area": args.max_split_area},
        "counter": {"field_v1_settings": FIELD_V1_SETTINGS,
                    "effective_settings": settings,
                    "counter_file": str(Path(sys.modules[RleCounter.__module__].__file__)),
                    "counter_sha256": sha256_file(Path(sys.modules[RleCounter.__module__].__file__)),
                    "split_touching_sha256": sha256_file(
                        Path(sys.modules["scripts.wo0016.split_touching"].__file__)),
                    "native_library": str(args.library),
                    "native_library_sha256": sha256_file(args.library),
                    "tracking_initial_state": "fresh counter, total_count=0, no tracks",
                    "count_axis": "x", "line_x": 320, "direction": 1,
                    "roi_mode": "full"},
        "replay": {"platform": platform.platform(), "python": sys.version.split()[0],
                   "elapsed_s": round(elapsed, 3),
                   "mean_ms_per_frame": round(elapsed * 1000 / n, 3)},
        "total_count": int(counter.total_count),
        "frames": frames,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(json.dumps({"threshold": args.threshold, "max_split_area": args.max_split_area,
                      "total_count": report["total_count"],
                      "events": sum(len(f["events"]) for f in frames),
                      "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())