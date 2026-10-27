"""One finite camera-only capture for touching-nut optical diagnosis.

This does not access the FPGA, DMA, or PYNQ and does not produce PL Binary8.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

import cv2
import numpy as np


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_mjpeg_gray(camera: cv2.VideoCapture) -> np.ndarray:
    ok, packet = camera.read()
    if not ok or packet is None:
        raise RuntimeError("camera read failed")
    raw = np.asarray(packet)
    if (raw.dtype != np.uint8 or raw.ndim not in (1, 2)
            or (raw.ndim == 2 and raw.shape[0] != 1)):
        raise RuntimeError("camera backend did not return raw uint8 MJPEG")
    raw = np.ascontiguousarray(raw).reshape(-1)
    if raw.size < 4 or tuple(raw[:2]) != (0xFF, 0xD8):
        raise RuntimeError("camera packet has no JPEG SOI marker")
    gray = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
    if gray is None or gray.dtype != np.uint8 or gray.shape != (400, 640):
        shape = None if gray is None else gray.shape
        dtype = None if gray is None else gray.dtype
        raise RuntimeError("MJPEG grayscale decode returned %s %s; expected Gray8 640x400"
                           % (dtype, shape))
    return gray


def capture(output_dir: Path, *, frames: int = 300, start_delay_s: float = 8.0,
            compact: bool = False, wait_for_object_s: float = 0.0) -> dict:
    limit = 1800 if compact else 300
    if frames < 1 or frames > limit or start_delay_s < 0 or not 0 <= wait_for_object_s <= 180:
        raise ValueError(f"frames must be 1..{limit}, delay nonnegative, wait 0..180")
    if not output_dir.is_absolute():
        raise ValueError("output directory must be absolute")
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise ValueError("output directory must be a new absolute path")
    if not Path("/dev/video0").exists():
        raise RuntimeError("/dev/video0 is unavailable")
    camera = cv2.VideoCapture("/dev/video0", cv2.CAP_V4L2)
    if not camera.isOpened():
        raise RuntimeError("camera could not be opened")
    try:
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 400)
        camera.set(cv2.CAP_PROP_FPS, 60)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not camera.set(cv2.CAP_PROP_CONVERT_RGB, 0):
            raise RuntimeError("V4L2 rejected CAP_PROP_CONVERT_RGB=0; raw MJPEG required")
        mode = {
            "width": int(camera.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps_reported": camera.get(cv2.CAP_PROP_FPS),
            "fourcc": int(camera.get(cv2.CAP_PROP_FOURCC)),
        }
        if (mode["width"], mode["height"]) != (640, 400):
            raise RuntimeError(f"unexpected camera dimensions: {mode}")
        source = None if compact else np.empty((frames, 400, 640), dtype=np.uint8)
        resized = np.empty((frames, 240, 320), dtype=np.uint8)
        monotonic_ns = np.empty(frames, dtype=np.int64)
        wall_ns = np.empty(frames, dtype=np.int64)
        read_ms = np.empty(frames, dtype=np.float32)
        for _ in range(2):
            _read_mjpeg_gray(camera)
        print(f"CAMERA_READY {mode}; initial delay {start_delay_s:g} seconds", flush=True)
        time.sleep(start_delay_s)
        waited_s = 0.0
        if wait_for_object_s:
            print(f"WAITING_FOR_OBJECT up to {wait_for_object_s:g} seconds", flush=True)
            wait_started = time.monotonic()
            consecutive = 0
            while time.monotonic() - wait_started < wait_for_object_s:
                frame = _read_mjpeg_gray(camera)
                sample = cv2.resize(frame, (320, 240),
                                    interpolation=cv2.INTER_NEAREST)
                consecutive = (consecutive + 1 if
                               np.count_nonzero(sample[:, :160] >= 128) >= 100
                               else 0)
                if consecutive >= 3:
                    waited_s = time.monotonic() - wait_started
                    print(f"OBJECT_DETECTED after {waited_s:.2f} seconds; recording now",
                          flush=True)
                    break
            else:
                raise RuntimeError("no object entered the left half before timeout")
        started_ns = time.monotonic_ns()
        for index in range(frames):
            read_started = time.perf_counter_ns()
            gray = _read_mjpeg_gray(camera)
            if gray.shape != (400, 640):
                raise RuntimeError(f"camera read failed at frame {index}")
            read_ms[index] = (time.perf_counter_ns() - read_started) / 1e6
            monotonic_ns[index] = time.monotonic_ns()
            wall_ns[index] = time.time_ns()
            if source is not None:
                source[index] = gray
            resized[index] = cv2.resize(gray, (320, 240),
                                         interpolation=cv2.INTER_NEAREST)
        elapsed_s = (time.monotonic_ns() - started_ns) / 1e9
    finally:
        camera.release()
    output_dir.mkdir(parents=True, mode=0o700)
    archive = output_dir / "camera_gray_only.npz"
    temp = output_dir / "camera_gray_only.npz.tmp"
    with temp.open("wb") as stream:
        contents = {"input_gray320x240": resized, "monotonic_ns": monotonic_ns,
                    "wall_ns": wall_ns, "read_ms": read_ms}
        if source is not None:
            contents["source_gray640x400"] = source
        np.savez(stream, **contents)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, archive)
    manifest = {
        "status": "COMPLETE", "scope": "CAMERA_ONLY_NOT_PL_BINARY8",
        "frame_count": frames, "mode": mode,
        "compact": compact,
        "wait_for_object_s": wait_for_object_s,
        "waited_s": waited_s,
        "resize": "raw MJPEG decoded directly to Gray8; nearest 640x400 to 320x240",
        "elapsed_s": elapsed_s, "effective_fps": frames / elapsed_s,
        "archive": archive.name, "archive_sha256": _sha256(archive),
        "archive_bytes": archive.stat().st_size,
        "independent_ground_truth": "NOT_RECORDED",
    }
    with (output_dir / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--start-delay-s", type=float, default=8.0)
    parser.add_argument("--compact", action="store_true",
                        help="save only 320x240 Gray8; permits up to 1800 frames")
    parser.add_argument("--wait-for-object-s", type=float, default=0.0,
                        help="arm until 3 consecutive frames show >=100 bright left-half pixels")
    args = parser.parse_args()
    print(json.dumps(capture(args.output_dir, frames=args.frames,
                             start_delay_s=args.start_delay_s,
                             compact=args.compact,
                             wait_for_object_s=args.wait_for_object_s), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
