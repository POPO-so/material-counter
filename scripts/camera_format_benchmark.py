#!/usr/bin/env python3
"""Linux camera-only MJPEG/YUV benchmark. No FPGA, DMA or counter access.

Enumeration is read-only. Benchmarking changes capture format temporarily;
the original format and frame rate are restored in a finally block.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time

import cv2
import numpy as np

FORMATS = {"MJPG", "JPEG", "YUYV", "YUY2", "UYVY", "GREY"}


def parse_modes(text):
    """Keep only explicitly enumerated discrete format/size/interval tuples."""
    modes, fourcc, size = [], None, None
    for line in text.splitlines():
        match = re.search(r"\[\d+\]:\s*'([^']{4})'", line)
        if match:
            fourcc, size = match[1], None
        if "Size:" in line:
            match = re.search(r"Size:\s*Discrete\s+(\d+)x(\d+)", line)
            size = (int(match[1]), int(match[2])) if match else None
        match = re.search(r"Interval:\s*Discrete.*?\(([\d.]+)\s+fps\)", line)
        if match and fourcc and size:
            mode = dict(fourcc=fourcc, width=size[0], height=size[1], fps=float(match[1]))
            if mode not in modes:
                modes.append(mode)
    return modes


def raw_to_gray(raw, fourcc, width, height):
    """Decode JPEG as mono, or extract Y without a BGR intermediate.

    Reject layouts with row padding rather than silently measuring wrong data.
    OpenCV owns the buffer until the next read; consume the view immediately.
    """
    if isinstance(raw, np.ndarray):
        if raw.dtype != np.uint8 or (raw.ndim == 3 and raw.shape[2] != 2):
            raise ValueError("Expected raw uint8 bytes, not a decoded color frame")
        data = np.ascontiguousarray(raw).reshape(-1)
    else:
        data = np.frombuffer(raw, dtype=np.uint8)
    if fourcc in ("MJPG", "JPEG"):
        if data.size < 4 or tuple(data[:2]) != (255, 216):
            raise ValueError("MJPEG backend did not return a JPEG packet")
        gray = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    elif fourcc in ("YUYV", "YUY2", "UYVY"):
        if width % 2 or data.size != width * height * 2:
            raise ValueError("YUV422 payload size/stride is not supported")
        gray = data.reshape(height, width * 2)[:, (1 if fourcc == "UYVY" else 0)::2]
    elif fourcc == "GREY":
        if data.size != width * height:
            raise ValueError("GREY payload size/stride is not supported")
        gray = data.reshape(height, width)
    else:
        raise ValueError("Unsupported raw format: " + fourcc)
    if gray is None or gray.shape != (height, width) or gray.dtype != np.uint8:
        raise ValueError("Decoded image geometry does not match negotiated mode")
    return gray


def _mode_matches(requested, negotiated):
    return (all(requested.get(k) == negotiated.get(k) for k in ("fourcc", "width", "height"))
            and math.isclose(requested.get("fps", 0), negotiated.get("fps", -1), rel_tol=.01))


def compare_results(base, candidate):
    result = dict(comparability="unavailable", fps_gain_percent=None,
                  cpu_reduction_percent=None)
    for row in (base, candidate):
        if row.get("status") != "ok" or not _mode_matches(row["requested"], row.get("negotiated", {})):
            result["reason"] = "failed run or negotiated mode mismatch"
            return result
    if any(base["requested"][k] != candidate["requested"][k] for k in ("width", "height", "fps")):
        result["reason"] = "different resolution or requested frame rate"
        return result
    if base.get("controls_before") != candidate.get("controls_before"):
        result["reason"] = "camera controls changed between runs"
        return result
    if (base.get("cpu_ms_per_frame", 0) <= 0 or base.get("pipeline_fps", 0) <= 0
            or candidate.get("cpu_ms_per_frame", 0) < 0 or candidate.get("pipeline_fps", 0) <= 0):
        result["reason"] = "missing/zero timing baseline"
        return result
    result.update(comparability="matched",
                  fps_gain_percent=100 * (candidate["pipeline_fps"] / base["pipeline_fps"] - 1),
                  cpu_reduction_percent=100 * (1 - candidate["cpu_ms_per_frame"] / base["cpu_ms_per_frame"]))
    return result


def _ctl(device, *args):
    proc = subprocess.run(["v4l2-ctl", "-d", device, *args], capture_output=True,
                          text=True, timeout=10)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "v4l2-ctl failed")
    return proc.stdout


def _controls(device):
    text = _ctl(device, "--list-ctrls")
    return {match[1]: int(match[2]) for match in
            re.finditer(r"^\s*(\w+).*?\bvalue=(-?\d+)", text, re.MULTILINE)}


def _original_mode(text, parm):
    size = re.search(r"Width/Height\s*:\s*(\d+)/(\d+)", text)
    fmt = re.search(r"Pixel Format\s*:\s*'(.{4})'", text)
    fps = re.search(r"Frames per second:\s*([\d.]+)", parm)
    if not (size and fmt and fps):
        raise RuntimeError("Cannot save original format/frame rate for restoration")
    return dict(width=int(size[1]), height=int(size[2]), fourcc=fmt[1], fps=float(fps[1]))


def _summary(values):
    return dict(mean=statistics.mean(values), median=statistics.median(values),
                p95=float(np.percentile(values, 95))) if values else None


def _resize(gray):
    """Same 320x240 output for all modes; nearest-neighbor with letterboxing."""
    h, w = gray.shape
    scale = min(320 / w, 240 / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    image = cv2.resize(gray, (nw, nh), interpolation=cv2.INTER_NEAREST)
    output = np.zeros((240, 320), dtype=np.uint8)
    y, x = (240 - nh) // 2, (320 - nw) // 2
    output[y:y + nh, x:x + nw] = image
    return output


def _worker(job):
    """Run in a disposable process so a blocked camera read has a hard timeout."""
    cv2.setNumThreads(1)
    mode, device = job["mode"], job["device"]
    processing_path = job.get("processing_path", "raw-gray")
    row = dict(status="error", requested=mode, negotiated={}, purpose="CAMERA_ONLY",
               processing_path=processing_path)
    cap = None
    try:
        row["controls_before"] = _controls(device)
        cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise RuntimeError("Cannot open camera")
        for key, value in ((cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*mode["fourcc"])),
                           (cv2.CAP_PROP_FRAME_WIDTH, mode["width"]),
                           (cv2.CAP_PROP_FRAME_HEIGHT, mode["height"]),
                           (cv2.CAP_PROP_FPS, mode["fps"]),
                           (cv2.CAP_PROP_CONVERT_RGB, int(processing_path == "opencv-bgr")),
                           (cv2.CAP_PROP_BUFFERSIZE, 1)):
            if not cap.set(key, value):
                raise RuntimeError("Backend rejected capture property %s=%s" % (key, value))
        code = int(round(cap.get(cv2.CAP_PROP_FOURCC)))
        row["negotiated"] = dict(fourcc="".join(chr((code >> (8 * i)) & 255) for i in range(4)),
                                  width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                  height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                                  fps=cap.get(cv2.CAP_PROP_FPS))
        if not _mode_matches(mode, row["negotiated"]):
            raise RuntimeError("Driver negotiated a different mode; excluded from comparison")
        row["driver_format_readback"] = _ctl(device, "--get-fmt-video", "--get-parm")
        def to_gray(raw):
            if processing_path == "opencv-bgr":
                if (raw.dtype != np.uint8 or raw.shape != (mode["height"], mode["width"], 3)):
                    raise ValueError("Current-path baseline expected a decoded BGR frame")
                return raw[:, :, 0]
            return raw_to_gray(raw, mode["fourcc"], mode["width"], mode["height"])

        for _ in range(job["warmup"]):
            ok, raw = cap.read()
            if not ok:
                raise RuntimeError("Warmup read failed")
            to_gray(raw)
        read_times, decode_times, resize_times = [], [], []
        decode_cpu, resize_cpu, payload_bytes = [], [], []
        started, cpu_started = time.perf_counter(), time.process_time()
        frames = 0
        first_gray = None
        while time.perf_counter() - started < job["seconds"]:
            tick = time.perf_counter()
            ok, raw = cap.read()
            after_read, cpu_tick = time.perf_counter(), time.process_time()
            if not ok or raw is None:
                raise RuntimeError("Camera read failed")
            gray = to_gray(raw)
            after_decode, cpu_decode = time.perf_counter(), time.process_time()
            output = _resize(gray)
            after_resize, cpu_resize = time.perf_counter(), time.process_time()
            read_times.append((after_read - tick) * 1000)
            decode_times.append((after_decode - after_read) * 1000)
            resize_times.append((after_resize - after_decode) * 1000)
            decode_cpu.append((cpu_decode - cpu_tick) * 1000)
            resize_cpu.append((cpu_resize - cpu_decode) * 1000)
            payload_bytes.append(raw.nbytes)
            if first_gray is None:
                row["raw_layout"] = dict(shape=list(raw.shape), strides=list(raw.strides), dtype=str(raw.dtype))
                first_gray = gray.copy()
            frames += 1
        elapsed, cpu_elapsed = time.perf_counter() - started, time.process_time() - cpu_started
        row.update(status="ok", frames=frames, elapsed_s=elapsed,
                   pipeline_fps=frames / elapsed, cpu_ms_per_frame=cpu_elapsed * 1000 / frames,
                   process_cpu_percent=100 * cpu_elapsed / elapsed,
                   read_wall_ms=_summary(read_times), decode_or_luma_wall_ms=_summary(decode_times),
                   resize_wall_ms=_summary(resize_times), decode_or_luma_cpu_ms=_summary(decode_cpu),
                   resize_cpu_ms=_summary(resize_cpu), mean_payload_bytes=statistics.mean(payload_bytes),
                   image_mean=float(first_gray.mean()), image_std=float(first_gray.std()))
        row["controls_after"] = _controls(device)
        if row["controls_before"] != row["controls_after"]:
            raise RuntimeError("Camera controls changed during capture; run excluded")
    except Exception as exc:
        row.update(status="error", error=str(exc))
    finally:
        if cap is not None:
            cap.release()
    return row


def _run_child(job):
    timeout = job["seconds"] + job["warmup"] / max(job["mode"]["fps"], 1) + 25
    try:
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_worker", json.dumps(job)],
                              capture_output=True, text=True, timeout=timeout)
        if proc.returncode:
            raise RuntimeError(proc.stderr[-2000:] or "Worker failed")
        row = json.loads(proc.stdout)
        row["backend_stderr"] = proc.stderr[-2000:]
        return row
    except (subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
        return dict(status="error", requested=job["mode"], negotiated={}, error=str(exc))


def _print_result(row):
    mode = row["requested"]
    label = "%s %dx%d @ %g [%s]" % (mode["fourcc"], mode["width"], mode["height"], mode["fps"],
                                     row.get("processing_path", "raw-gray"))
    if row["status"] != "ok":
        print(label + ": ERROR " + row.get("error", "unknown"), flush=True)
    else:
        print(label + ": %.2f processed FPS, %.3f CPU ms/frame, decode/Y %.3f ms, resize %.3f ms" %
              (row["pipeline_fps"], row["cpu_ms_per_frame"], row["decode_or_luma_wall_ms"]["mean"],
               row["resize_wall_ms"]["mean"]), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=400)
    parser.add_argument("--fps", type=float, default=60)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--warmup", type=int, default=15)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--sweep-yuv", action="store_true", help="Also test every enumerated YUV discrete mode once")
    parser.add_argument("--include-current-mjpeg", action="store_true",
                        help="Also measure OpenCV BGR decoding then first-channel extraction, like the existing capture path")
    parser.add_argument("--compare-available", action="store_true",
                        help="Also test YUV's highest enumerated FPS <= requested FPS at this size; label source-FPS mismatch")
    parser.add_argument("--output", type=Path, default=Path("camera-format-benchmark.json"))
    parser.add_argument("--_worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._worker:
        print(json.dumps(_worker(json.loads(args._worker)), allow_nan=False))
        return 0
    if sys.platform != "linux":
        parser.error("Run this script on the PYNQ/Linux board")
    if not shutil.which("v4l2-ctl"):
        parser.error("v4l2-ctl is required (install v4l-utils)")
    if (not math.isfinite(args.seconds) or args.seconds < 1 or args.repeats < 1
            or args.warmup < 1 or not math.isfinite(args.fps) or args.fps <= 0):
        parser.error("seconds>=1, repeats>=1, warmup>=1 and fps>0 required")
    if args.output.exists():
        parser.error("Output already exists; use a new path")
    listing = _ctl(args.device, "--list-formats-ext")
    modes = parse_modes(listing)
    report = dict(schema_version=1, evidence="CAMERA_ONLY_NOT_COUNTER_FPS",
                  utc=datetime.now(timezone.utc).isoformat(), host=platform.uname()._asdict(),
                  python=sys.version, opencv=cv2.__version__, enumeration=listing, modes=modes,
                  results=[], comparisons=[], limitations=[
                      "Processed-frame FPS is not physical sensor FPS or verified unique-frame FPS.",
                      "read wall time includes waiting, USB, driver and backend overhead.",
                      "CPU time covers this single-threaded process, not kernel/system CPU.",
                      "No FPGA/DMA/tracker/preview; percentages are not whole-counter gains.",
                      "Same controls do not prove identical JPEG/YUV image quality."])
    print(listing, flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.list_only:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 0
    if not shutil.which("fuser"):
        parser.error("fuser is required to check for another camera owner")
    owners = subprocess.run(["fuser", args.device], capture_output=True, text=True, timeout=5)
    if owners.returncode != 1 or owners.stdout.strip():
        parser.error("Camera may be in use; close preview/counter before benchmarking")
    original = _original_mode(_ctl(args.device, "--get-fmt-video"), _ctl(args.device, "--get-parm"))
    report["original_mode"], report["controls_initial"] = original, _controls(args.device)
    selected = [m for m in modes if m["fourcc"] in FORMATS and m["width"] == args.width
                and m["height"] == args.height and math.isclose(m["fps"], args.fps, rel_tol=.001)]
    if args.compare_available:
        for fmt in ("YUYV", "YUY2", "UYVY", "GREY"):
            options = [m for m in modes if m["fourcc"] == fmt and m["width"] == args.width
                       and m["height"] == args.height and m["fps"] <= args.fps]
            if options:
                candidate = max(options, key=lambda m: m["fps"])
                if candidate not in selected:
                    selected.append(candidate)
    paths = [(mode, "raw-gray") for mode in selected]
    if args.include_current_mjpeg:
        paths += [(mode, "opencv-bgr") for mode in selected if mode["fourcc"] in ("MJPG", "JPEG")]
    jobs = []
    for repeat in range(args.repeats):
        order = paths if repeat % 2 == 0 else list(reversed(paths))
        jobs += [(mode, repeat, "matched", path) for mode, path in order]
    if args.sweep_yuv:
        jobs += [(m, 0, "sweep", "raw-gray") for m in modes if m["fourcc"] in ("YUYV", "YUY2", "UYVY", "GREY")]
    try:
        for mode, repeat, purpose, path in jobs:
            row = _run_child(dict(device=args.device, mode=mode, seconds=args.seconds, warmup=args.warmup,
                                  processing_path=path))
            row.update(repeat=repeat, test_kind=purpose)
            report["results"].append(row)
            _print_result(row)
        matched = [r for r in report["results"] if r["test_kind"] == "matched"]
        report["mjpeg_decode_comparisons"] = []
        for base in matched:
            if base.get("processing_path") != "opencv-bgr":
                continue
            for candidate in matched:
                if (candidate.get("processing_path") == "raw-gray" and candidate["repeat"] == base["repeat"]
                        and candidate["requested"] == base["requested"]):
                    row = compare_results(base, candidate)
                    row.update(repeat=base["repeat"], interpretation="SAME_MJPEG_MODE_GRAY_DECODE_VS_BGR_DECODE")
                    report["mjpeg_decode_comparisons"].append(row)
        improvements = [r for r in report["mjpeg_decode_comparisons"] if r["comparability"] == "matched"]
        if improvements:
            print("MJPEG gray decode vs current BGR path: median FPS gain %+.2f%%, CPU/frame reduction %+.2f%% (%d pairs)" %
                  (statistics.median(r["fps_gain_percent"] for r in improvements),
                   statistics.median(r["cpu_reduction_percent"] for r in improvements), len(improvements)), flush=True)
        report["mode_change_comparisons"] = []
        for base in matched:
            if base["requested"]["fourcc"] not in ("MJPG", "JPEG"):
                continue
            for candidate in matched:
                if candidate["repeat"] == base["repeat"] and candidate["requested"]["fourcc"] not in ("MJPG", "JPEG"):
                    comparison = compare_results(base, candidate)
                    comparison.update(repeat=base["repeat"], baseline=base["requested"], candidate=candidate["requested"],
                                      baseline_path=base.get("processing_path", "raw-gray"))
                    report["comparisons"].append(comparison)
                    if (base["status"] == candidate["status"] == "ok"
                            and base["requested"]["fps"] != candidate["requested"]["fps"]
                            and base.get("controls_before") == candidate.get("controls_before")
                            and base["pipeline_fps"] > 0 and base["cpu_ms_per_frame"] > 0):
                        report["mode_change_comparisons"].append(dict(
                            interpretation="AVAILABLE_MODE_CHANGE_NOT_ISOLATED_FORMAT_EFFECT",
                            repeat=base["repeat"], baseline=base["requested"], candidate=candidate["requested"],
                            baseline_path=base.get("processing_path", "raw-gray"),
                            fps_change_percent=100 * (candidate["pipeline_fps"] / base["pipeline_fps"] - 1),
                            cpu_per_frame_reduction_percent=100 * (1 - candidate["cpu_ms_per_frame"] / base["cpu_ms_per_frame"])))
        for fmt, path in sorted({(c["candidate"]["fourcc"], c["baseline_path"]) for c in report["comparisons"]}):
            rows = [c for c in report["comparisons"] if c["candidate"]["fourcc"] == fmt
                    and c["baseline_path"] == path and c["comparability"] == "matched"]
            if rows:
                print("%s vs JPEG[%s] matched median: FPS gain %+.2f%%, CPU/frame reduction %+.2f%% (%d pairs)" %
                      (fmt, path, statistics.median(c["fps_gain_percent"] for c in rows),
                       statistics.median(c["cpu_reduction_percent"] for c in rows), len(rows)), flush=True)
        if not report["comparisons"]:
            print("No matched JPEG/YUV pair at requested resolution/FPS; no gain percentage claimed.", flush=True)
    finally:
        try:
            _ctl(args.device, "--set-fmt-video=width=%d,height=%d,pixelformat=%s" %
                 (original["width"], original["height"], original["fourcc"]), "--set-parm=%g" % original["fps"])
            restored = _original_mode(_ctl(args.device, "--get-fmt-video"), _ctl(args.device, "--get-parm"))
            report["restored_mode"] = restored
            report["restoration_ok"] = _mode_matches(original, restored)
            report["controls_final"] = _controls(args.device)
        except Exception as exc:
            report["restoration_ok"], report["restoration_error"] = False, str(exc)
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        print("Report: " + str(args.output), flush=True)
    return 0 if report.get("restoration_ok") and any(r["status"] == "ok" for r in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
