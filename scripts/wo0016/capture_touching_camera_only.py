"""One finite camera-only capture for touching-nut optical diagnosis.

This does not access the FPGA, DMA, or PYNQ and does not produce PL Binary8.

The hardened session path adds a precheck (disk, memory, parameters, output
directory), an explicit state history, local marker events for the operator,
bounded windows, partial-save on failure and per-frame timing statistics.
The legacy ``capture()`` helper keeps the previous behaviour and return shape.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

SCHEMA = "material-counter.camera-only-capture.v2"
GAP_FLOOR_MS = 33.0
MIN_FORMAL_FRAMES = 600
MAX_NPZ_FRAMES = 300
MAX_COMPACT_FRAMES = 1800
MAX_MEMMAP_FRAMES = 3600
DISK_RESERVE_BYTES = 256 * 1024 * 1024
MEMORY_HEADROOM = 1.5
MEMORY_OVERHEAD_BYTES = 64 * 1024 * 1024
MEMORY_MEMMAP_OVERHEAD_BYTES = 128 * 1024 * 1024
STOP_REQUESTED = {"flag": False}
MAX_ANOMALIES = 64

_disk_usage = shutil.disk_usage
_monotonic_ns = time.monotonic_ns
_wall_ns = time.time_ns
_save_npz = np.savez
_open_memmap = np.lib.format.open_memmap


class CaptureError(RuntimeError):
    pass


class PrecheckError(CaptureError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_mjpeg_packet(camera):
    ok, packet = camera.read()
    if not ok or packet is None:
        raise CaptureError("camera read failed")
    raw = np.asarray(packet)
    if (raw.dtype != np.uint8 or raw.ndim not in (1, 2)
            or (raw.ndim == 2 and raw.shape[0] != 1)):
        raise CaptureError("camera backend did not return raw uint8 MJPEG")
    raw = np.ascontiguousarray(raw).reshape(-1)
    if raw.size < 4 or tuple(raw[:2]) != (0xFF, 0xD8):
        raise CaptureError("camera packet has no JPEG SOI marker")
    return raw


def _decode_gray(raw):
    gray = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
    if gray is None or gray.dtype != np.uint8 or gray.shape != (400, 640):
        shape = None if gray is None else gray.shape
        dtype = None if gray is None else gray.dtype
        raise CaptureError("MJPEG grayscale decode returned %s %s; expected Gray8 640x400"
                           % (dtype, shape))
    return gray


def _read_mjpeg_gray(camera) -> np.ndarray:
    return _decode_gray(_read_mjpeg_packet(camera))


def _read_meminfo(path="/proc/meminfo"):
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def _expected_bytes(frames, compact, output_format):
    per_frame = 320 * 240 + 8 + 8 + 4
    if not compact:
        per_frame += 400 * 640
    return frames * per_frame


def _precheck(output_dir, *, frames, compact, formal, output_format,
              baseline_frames, tail_frames, meminfo_path="/proc/meminfo",
              disk_usage=None):
    if isinstance(frames, bool) or not isinstance(frames, int) or frames < 1:
        raise PrecheckError("frames must be a positive integer")
    if isinstance(baseline_frames, bool) or not isinstance(baseline_frames, int) or baseline_frames < 0:
        raise PrecheckError("baseline_frames must be a non-negative integer")
    if isinstance(tail_frames, bool) or not isinstance(tail_frames, int) or tail_frames < 0:
        raise PrecheckError("tail_frames must be a non-negative integer")
    if baseline_frames and baseline_frames >= frames:
        raise PrecheckError("baseline_frames must be smaller than frames")
    if tail_frames and tail_frames >= frames:
        raise PrecheckError("tail_frames must be smaller than frames")
    if output_format not in ("npz", "memmap"):
        raise PrecheckError("format must be npz or memmap")
    if formal and compact:
        raise PrecheckError("formal material capture must keep 640x400 source frames")
    if output_format == "npz":
        limit = MAX_COMPACT_FRAMES if compact else MAX_NPZ_FRAMES
    else:
        limit = MAX_MEMMAP_FRAMES
    if frames > limit:
        raise PrecheckError(f"frames {frames} exceeds {output_format} limit {limit}")
    if formal and frames < MIN_FORMAL_FRAMES:
        raise PrecheckError(f"formal capture needs at least {MIN_FORMAL_FRAMES} frames")
    if output_dir.exists():
        raise PrecheckError(f"output directory already exists: {output_dir}")
    expected = _expected_bytes(frames, compact, output_format)
    usage = (disk_usage or _disk_usage)(output_dir.parent)
    free = usage.free
    required = expected * 2 + DISK_RESERVE_BYTES
    if free < required:
        raise PrecheckError(f"insufficient disk: free {free} < required {required}")
    available_kib = _read_meminfo(meminfo_path)
    estimate = (expected + MEMORY_OVERHEAD_BYTES if output_format == "npz"
                else MEMORY_MEMMAP_OVERHEAD_BYTES)
    if available_kib is not None:
        if available_kib * 1024 < estimate * MEMORY_HEADROOM:
            raise PrecheckError(
                f"insufficient memory: MemAvailable {available_kib} KiB < "
                f"{int(estimate * MEMORY_HEADROOM / 1024)} KiB")
    return {"expected_bytes": expected, "free_bytes": free,
            "required_bytes": required, "mem_available_kib": available_kib,
            "memory_estimate_bytes": estimate}


def _gap_anomalies(monotonic_ns, diag, captured):
    """Attribute each over-threshold gap to the measured frame stages.

    The frame timestamp is taken after resize and before the data writes, so a
    gap spans the post-timestamp work of the earlier frame plus the
    read/decode/resize of the later frame. Anything left over is unexplained.
    """
    if diag is None or captured < 2:
        return []
    diffs = np.diff(np.asarray(monotonic_ns, dtype=np.int64)) / 1e6
    if not len(diffs):
        return []
    threshold = max(GAP_FLOOR_MS, 2.0 * float(np.median(diffs)))
    records = []
    for index in np.flatnonzero(diffs > threshold)[:MAX_ANOMALIES]:
        index = int(index)
        pre_prev = float(diag["read_ms"][index] + diag["decode_ms"][index]
                         + diag["resize_ms"][index])
        pre_next = float(diag["read_ms"][index + 1] + diag["decode_ms"][index + 1]
                         + diag["resize_ms"][index + 1])
        post_prev = float(diag["frame_total_ms"][index]) - pre_prev
        records.append({
            "after_frame": index, "before_frame": index + 1,
            "gap_ms": round(float(diffs[index]), 3),
            "read_ms_prev": round(float(diag["read_ms"][index]), 3),
            "decode_ms_prev": round(float(diag["decode_ms"][index]), 3),
            "resize_ms_prev": round(float(diag["resize_ms"][index]), 3),
            "source_write_ms_prev": round(float(diag["source_write_ms"][index]), 3),
            "preview_write_ms_prev": round(float(diag["preview_write_ms"][index]), 3),
            "frame_total_ms_prev": round(float(diag["frame_total_ms"][index]), 3),
            "read_ms_next": round(float(diag["read_ms"][index + 1]), 3),
            "decode_ms_next": round(float(diag["decode_ms"][index + 1]), 3),
            "resize_ms_next": round(float(diag["resize_ms"][index + 1]), 3),
            "unexplained_ms": round(float(diffs[index]) - post_prev - pre_next, 3),
        })
    return records


def _gap_stats(monotonic_ns):
    if len(monotonic_ns) < 2:
        return {"median_gap_ms": None, "max_gap_ms": None,
                "gaps_over_threshold": 0, "gap_threshold_ms": GAP_FLOOR_MS,
                "non_positive_gaps": 0}
    diffs = np.diff(np.asarray(monotonic_ns, dtype=np.int64))
    non_positive = int(np.count_nonzero(diffs <= 0))
    gaps_ms = diffs.astype(np.float64) / 1e6
    median = float(np.median(gaps_ms))
    threshold = max(GAP_FLOOR_MS, 2.0 * median)
    return {"median_gap_ms": round(median, 3),
            "max_gap_ms": round(float(gaps_ms.max()), 3),
            "gaps_over_threshold": int(np.count_nonzero(gaps_ms > threshold)),
            "gap_threshold_ms": round(threshold, 3),
            "non_positive_gaps": non_positive}


def _write_json(path, payload):
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("wb") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2,
                                sort_keys=True).encode("utf-8"))
        stream.write(b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _truth_template(output_dir, session_id):
    path = output_dir / "truth-template.json"
    if path.exists():
        return path
    template = {"session_id": session_id, "material": "UNKNOWN",
                "nominal_count": "UNKNOWN", "placement_mode": "UNKNOWN",
                "belt_speed": "UNKNOWN", "lighting_state": "UNKNOWN",
                "camera_settings": "UNKNOWN", "drop_start": "UNKNOWN",
                "drop_end": "UNKNOWN", "operator_count": "UNKNOWN",
                "clear_confirmed": "UNKNOWN", "archive_sha256": "UNKNOWN",
                "notes": "UNKNOWN"}
    _write_json(path, template)
    return path


def _marker(events_dir, name):
    return (events_dir / name).exists()


def _camera_settings(camera):
    return {
        "width": int(camera.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps_reported": camera.get(cv2.CAP_PROP_FPS),
        "fourcc": int(camera.get(cv2.CAP_PROP_FOURCC)),
    }


def _open_camera(path):
    return cv2.VideoCapture(path, cv2.CAP_V4L2)


def capture_session(output_dir, *, session_id=None, frames=300, baseline_frames=0,
                    tail_frames=0, start_delay_s=8.0, compact=False,
                    wait_for_object_s=0.0, formal=False, output_format="npz",
                    camera_factory=None, meminfo_path="/proc/meminfo",
                    camera_path="/dev/video0", diagnostics=False) -> dict:
    if not isinstance(output_dir, Path):
        output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        raise CaptureError("output directory must be absolute")
    output_dir = output_dir.resolve()
    session_id = session_id or output_dir.name
    history = []

    def state(name, **extra):
        entry = {"state": name, "at": _now()}
        entry.update(extra)
        history.append(entry)

    state("PRECHECK")
    try:
        precheck = _precheck(output_dir, frames=frames, compact=compact,
                             formal=formal, output_format=output_format,
                             baseline_frames=baseline_frames,
                             tail_frames=tail_frames,
                             meminfo_path=meminfo_path)
    except PrecheckError as error:
        if output_dir.exists():
            raise
        output_dir.mkdir(parents=True, mode=0o700)
        manifest = {"schema": SCHEMA, "session_id": session_id, "status": "FAILED",
                    "scope": "CAMERA_ONLY_NOT_PL_BINARY8",
                    "state_history": history, "failure_reason": str(error),
                    "frames_requested": frames, "frame_count": 0,
                    "raw_mjpeg_saved": False,
                    "independent_ground_truth": "UNKNOWN"}
        _write_json(output_dir / "manifest.json", manifest)
        raise

    output_dir.mkdir(parents=True, mode=0o700)
    events_dir = output_dir / "events"
    events_dir.mkdir()
    _truth_template(output_dir, session_id)
    state("PRECHECK", precheck=precheck)

    camera = (camera_factory or _open_camera)(camera_path)
    if not camera.isOpened():
        manifest = {"schema": SCHEMA, "session_id": session_id, "status": "FAILED",
                    "scope": "CAMERA_ONLY_NOT_PL_BINARY8",
                    "state_history": history, "failure_reason": "camera could not be opened",
                    "frames_requested": frames, "frame_count": 0,
                    "raw_mjpeg_saved": False,
                    "independent_ground_truth": "UNKNOWN"}
        _write_json(output_dir / "manifest.json", manifest)
        raise CaptureError("camera could not be opened")

    source = resized = monotonic = wall = read_ms = None
    memmap_files = {}
    captured = 0
    mode = None
    waited_s = 0.0
    events = {"recording_started_at": None, "baseline_end": None,
              "drop_start": None, "drop_end": None, "stop": None}
    failure_reason = None
    elapsed_s = 0.0
    started_ns = None
    diag = None
    if diagnostics:
        diag = {name: np.empty(frames, dtype=np.float32) for name in
                ("read_ms", "decode_ms", "resize_ms", "source_write_ms",
                 "preview_write_ms", "frame_total_ms")}

    try:
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 400)
        camera.set(cv2.CAP_PROP_FPS, 60)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not camera.set(cv2.CAP_PROP_CONVERT_RGB, 0):
            raise CaptureError("V4L2 rejected CAP_PROP_CONVERT_RGB=0; raw MJPEG required")
        mode = _camera_settings(camera)
        if (mode["width"], mode["height"]) != (640, 400):
            raise CaptureError(f"unexpected camera dimensions: {mode}")
        if output_format == "npz":
            source = None if compact else np.empty((frames, 400, 640), dtype=np.uint8)
            resized = np.empty((frames, 240, 320), dtype=np.uint8)
            monotonic = np.empty(frames, dtype=np.int64)
            wall = np.empty(frames, dtype=np.int64)
            read_ms = np.empty(frames, dtype=np.float32)
        else:
            if not compact:
                memmap_files["source_gray640x400.npy"] = _open_memmap(
                    output_dir / "source_gray640x400.npy", mode="w+",
                    dtype=np.uint8, shape=(frames, 400, 640))
            memmap_files["input_gray320x240.npy"] = _open_memmap(
                output_dir / "input_gray320x240.npy", mode="w+",
                dtype=np.uint8, shape=(frames, 240, 320))
            memmap_files["monotonic_ns.npy"] = _open_memmap(
                output_dir / "monotonic_ns.npy", mode="w+", dtype=np.int64, shape=(frames,))
            memmap_files["wall_ns.npy"] = _open_memmap(
                output_dir / "wall_ns.npy", mode="w+", dtype=np.int64, shape=(frames,))
            memmap_files["read_ms.npy"] = _open_memmap(
                output_dir / "read_ms.npy", mode="w+", dtype=np.float32, shape=(frames,))

        for _ in range(2):
            _read_mjpeg_gray(camera)
        print(f"CAMERA_READY {mode}; initial delay {start_delay_s:g} seconds", flush=True)
        state("CAMERA_READY", camera=mode)
        time.sleep(start_delay_s)
        if wait_for_object_s:
            print(f"WAITING_FOR_OBJECT up to {wait_for_object_s:g} seconds", flush=True)
            wait_started = time.monotonic()
            consecutive = 0
            while time.monotonic() - wait_started < wait_for_object_s:
                frame = _read_mjpeg_gray(camera)
                sample = cv2.resize(frame, (320, 240), interpolation=cv2.INTER_NEAREST)
                consecutive = (consecutive + 1 if
                               np.count_nonzero(sample[:, :160] >= 128) >= 100
                               else 0)
                if consecutive >= 3:
                    waited_s = time.monotonic() - wait_started
                    print(f"OBJECT_DETECTED after {waited_s:.2f} seconds; recording now",
                          flush=True)
                    break
            else:
                raise CaptureError("no object entered the left half before timeout")

        started_ns = _monotonic_ns()
        events["recording_started_at"] = started_ns
        state("RECORDING")
        for index in range(frames):
            frame_started = time.perf_counter_ns()
            if STOP_REQUESTED["flag"]:
                failure_reason = "signal_stop"
                break
            if _marker(events_dir, "stop"):
                events["stop"] = {"frame": index, "monotonic_ns": _monotonic_ns()}
                failure_reason = "stopped_by_marker"
                break
            read_started = time.perf_counter_ns()
            raw = _read_mjpeg_packet(camera)
            decoded_at = time.perf_counter_ns()
            gray = _decode_gray(raw)
            prepared_at = time.perf_counter_ns()
            small = cv2.resize(gray, (320, 240), interpolation=cv2.INTER_NEAREST)
            resized_at = time.perf_counter_ns()
            read_ms_value = (prepared_at - read_started) / 1e6
            timestamp = _monotonic_ns()
            wall_timestamp = _wall_ns()
            write_started = time.perf_counter_ns()
            if output_format == "npz":
                if source is not None:
                    source[index] = gray
                source_written = time.perf_counter_ns()
                resized[index] = small
                preview_written = time.perf_counter_ns()
                monotonic[index] = timestamp
                wall[index] = wall_timestamp
                read_ms[index] = read_ms_value
            else:
                if not compact:
                    memmap_files["source_gray640x400.npy"][index] = gray
                source_written = time.perf_counter_ns()
                memmap_files["input_gray320x240.npy"][index] = small
                preview_written = time.perf_counter_ns()
                memmap_files["monotonic_ns.npy"][index] = timestamp
                memmap_files["wall_ns.npy"][index] = wall_timestamp
                memmap_files["read_ms.npy"][index] = read_ms_value
            captured = index + 1
            if baseline_frames and captured == baseline_frames:
                events["baseline_end"] = {"frame": index, "monotonic_ns": timestamp}
            if events["drop_start"] is None and _marker(events_dir, "drop_start"):
                events["drop_start"] = {"frame": index, "monotonic_ns": timestamp}
            if events["drop_end"] is None and _marker(events_dir, "drop_end"):
                events["drop_end"] = {"frame": index, "monotonic_ns": timestamp}
            if diag is not None:
                frame_finished = time.perf_counter_ns()
                diag["read_ms"][index] = (decoded_at - read_started) / 1e6
                diag["decode_ms"][index] = (prepared_at - decoded_at) / 1e6
                diag["resize_ms"][index] = (resized_at - prepared_at) / 1e6
                diag["source_write_ms"][index] = (source_written - write_started) / 1e6
                diag["preview_write_ms"][index] = (preview_written - source_written) / 1e6
                diag["frame_total_ms"][index] = (frame_finished - frame_started) / 1e6
        elapsed_s = (_monotonic_ns() - started_ns) / 1e9 if started_ns else 0.0
    except BaseException as error:
        failure_reason = failure_reason or repr(error)
        if elapsed_s == 0.0 and started_ns:
            elapsed_s = (_monotonic_ns() - started_ns) / 1e9
    finally:
        camera.release()
        STOP_REQUESTED["flag"] = False

    state("FINISHING")
    if failure_reason is None and formal:
        if events["drop_end"] is None:
            failure_reason = "drop_end_marker_missing"
        else:
            after = captured - 1 - events["drop_end"]["frame"]
            if after < tail_frames:
                failure_reason = f"tail_frames_insufficient:{after}<{tail_frames}"
    status = "COMPLETE" if failure_reason is None and captured == frames else "INCOMPLETE"

    finishing = {"flush_ms": None, "save_ms": None, "hash_ms": None,
                 "manifest_ms": None}
    data_files = []
    archive_name = None
    if output_format == "npz" and captured:
        archive_name = "camera_gray_only.npz"
        archive_path = output_dir / archive_name
        temp = output_dir / (archive_name + ".tmp")
        contents = {"input_gray320x240": resized[:captured],
                    "monotonic_ns": monotonic[:captured],
                    "wall_ns": wall[:captured],
                    "read_ms": read_ms[:captured]}
        if source is not None:
            contents["source_gray640x400"] = source[:captured]
        started = time.perf_counter_ns()
        try:
            with temp.open("wb") as stream:
                _save_npz(stream, **contents)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, archive_path)
        except BaseException as error:
            finishing["save_ms"] = round((time.perf_counter_ns() - started) / 1e6, 3)
            failure_reason = failure_reason or f"save_failed:{error!r}"
            if status == "COMPLETE":
                status = "INCOMPLETE"
        else:
            finishing["save_ms"] = round((time.perf_counter_ns() - started) / 1e6, 3)
            started = time.perf_counter_ns()
            data_files.append({"name": archive_name,
                               "bytes": archive_path.stat().st_size,
                               "sha256": _sha256(archive_path)})
            finishing["hash_ms"] = round((time.perf_counter_ns() - started) / 1e6, 3)
    elif output_format == "memmap" and captured:
        started = time.perf_counter_ns()
        for array in memmap_files.values():
            array.flush()
        finishing["flush_ms"] = round((time.perf_counter_ns() - started) / 1e6, 3)
        started = time.perf_counter_ns()
        for name in memmap_files:
            path = output_dir / name
            data_files.append({"name": name, "bytes": path.stat().st_size,
                               "sha256": _sha256(path)})
        finishing["hash_ms"] = round((time.perf_counter_ns() - started) / 1e6, 3)

    diagnostics_name = None
    if diag is not None and captured:
        path = output_dir / "diagnostics.npz"
        temp = output_dir / "diagnostics.npz.tmp"
        try:
            with temp.open("wb") as stream:
                _save_npz(stream, **{name: array[:captured]
                                     for name, array in diag.items()})
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
            data_files.append({"name": "diagnostics.npz",
                               "bytes": path.stat().st_size,
                               "sha256": _sha256(path)})
            diagnostics_name = "diagnostics.npz"
        except BaseException as error:
            failure_reason = failure_reason or f"diagnostics_save_failed:{error!r}"
            if status == "COMPLETE":
                status = "INCOMPLETE"

    if output_format == "npz":
        captured_monotonic = (monotonic[:captured] if monotonic is not None
                              else np.empty(0, dtype=np.int64))
    elif captured:
        captured_monotonic = np.asarray(memmap_files["monotonic_ns.npy"][:captured])
    else:
        captured_monotonic = np.empty(0, dtype=np.int64)
    gaps = _gap_stats(captured_monotonic)
    anomalies = _gap_anomalies(captured_monotonic, diag, captured)
    manifest = {
        "schema": SCHEMA, "session_id": session_id, "status": status,
        "scope": "CAMERA_ONLY_NOT_PL_BINARY8", "formal": formal,
        "state_history": history, "failure_reason": failure_reason,
        "format": output_format, "compact": compact,
        "frames_requested": frames, "frame_count": captured,
        "baseline_frames": baseline_frames, "tail_frames": tail_frames,
        "frames_after_drop_end": (None if events["drop_end"] is None
                                  else captured - 1 - events["drop_end"]["frame"]),
        "events": events, "waited_s": waited_s,
        "camera": mode, "resize": "raw MJPEG decoded directly to Gray8; "
                                  "nearest 640x400 to 320x240",
        "elapsed_s": round(elapsed_s, 3),
        "effective_fps": round(captured / elapsed_s, 3) if elapsed_s > 0 else None,
        "timing": gaps, "precheck": precheck,
        "data_files": data_files,
        "archive": archive_name,
        "archive_sha256": data_files[0]["sha256"] if data_files else None,
        "archive_bytes": data_files[0]["bytes"] if data_files else None,
        "raw_mjpeg_saved": False,
        "decoded_frames": captured,
        "saved_frames": captured,
        "binary8_semantics": "offline cv2.THRESH_BINARY compares > threshold; "
                             "PL compares >= threshold; software binary8 is not "
                             "equivalent to PL measurement",
        "truth_template": "truth-template.json",
        "independent_ground_truth": "UNKNOWN",
        "diagnostics": {"enabled": diag is not None,
                        "file": diagnostics_name,
                        "anomalies": anomalies,
                        "anomaly_limit": MAX_ANOMALIES},
        "finishing": finishing,
    }
    manifest_started = time.perf_counter_ns()
    try:
        _write_json(output_dir / "manifest.json", manifest)
    except BaseException as error:
        print("MANIFEST_WRITE_FAILED", repr(error), flush=True)
        raise
    finishing["manifest_ms"] = round((time.perf_counter_ns() - manifest_started) / 1e6, 3)
    manifest["finishing"] = finishing
    state("COMPLETE" if status == "COMPLETE" else "INCOMPLETE")
    manifest["state_history"] = history
    try:
        _write_json(output_dir / "manifest.json", manifest)
    except BaseException:
        pass
    print(json.dumps({k: manifest[k] for k in ("session_id", "status",
                                               "frame_count", "failure_reason")},
                     sort_keys=True))
    if status != "COMPLETE":
        raise CaptureError(f"capture {status}: {failure_reason}")
    return manifest


def capture(output_dir, *, frames: int = 300, start_delay_s: float = 8.0,
            compact: bool = False, wait_for_object_s: float = 0.0) -> dict:
    """Legacy finite capture; returns the v2 manifest with the old keys."""
    return capture_session(output_dir, frames=frames, start_delay_s=start_delay_s,
                           compact=compact, wait_for_object_s=wait_for_object_s,
                           formal=False, output_format="npz")


def _handle_signal(signum, frame):
    STOP_REQUESTED["flag"] = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--frames", type=int, default=None)
    parser.add_argument("--baseline-frames", type=int, default=0)
    parser.add_argument("--tail-frames", type=int, default=0)
    parser.add_argument("--start-delay-s", type=float, default=8.0)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--formal", action="store_true")
    parser.add_argument("--format", choices=("npz", "memmap"), default=None)
    parser.add_argument("--diagnostics", action="store_true",
                        help="record per-stage timings and gap attribution")
    parser.add_argument("--wait-for-object-s", type=float, default=0.0,
                        help="arm until 3 consecutive frames show >=100 bright left-half pixels")
    args = parser.parse_args()
    output_format = args.format or ("memmap" if args.formal else "npz")
    frames = args.frames if args.frames is not None else (1800 if args.formal else 300)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        manifest = capture_session(
            args.output_dir, session_id=args.session_id, frames=frames,
            baseline_frames=args.baseline_frames, tail_frames=args.tail_frames,
            start_delay_s=args.start_delay_s, compact=args.compact,
            wait_for_object_s=args.wait_for_object_s, formal=args.formal,
            output_format=output_format, diagnostics=args.diagnostics)
    except CaptureError as error:
        print(f"CAPTURE_FAILED {error}", flush=True)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())