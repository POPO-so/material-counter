from __future__ import annotations

import json
import platform
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "wo0016"))
from mjpeg_preview import MjpegFrameHub, PeriodicMjpegPublisher, render_preview_jpeg
from stream_counter import MaskCounter


def _legacy_project_roi(roi: np.ndarray):
    rows = tuple(int(value) for value in np.count_nonzero(roi, axis=1))
    columns = tuple(int(value) for value in np.count_nonzero(roi, axis=0))
    return rows, columns, int(cv2.countNonZero(roi))


def projection_benchmark() -> dict:
    rng = np.random.default_rng(170017)
    roi = np.where(rng.random((49, 320)) < 0.045, 255, 0).astype(np.uint8)
    expected = _legacy_project_roi(roi)
    actual = MaskCounter._project_roi(roi)
    if actual != expected:
        raise AssertionError("vectorized ROI projection changed outputs")
    loops = 1500
    trials = 7
    legacy = []
    vectorized = []
    for _ in range(trials):
        started = time.perf_counter()
        for _ in range(loops):
            _legacy_project_roi(roi)
        legacy.append(time.perf_counter() - started)
        started = time.perf_counter()
        for _ in range(loops):
            MaskCounter._project_roi(roi)
        vectorized.append(time.perf_counter() - started)
    old_median = statistics.median(legacy)
    new_median = statistics.median(vectorized)
    return {
        "input": "synthetic Binary8 ROI 49x320, foreground density 4.5%",
        "iterations_per_trial": loops,
        "trials": trials,
        "legacy_median_ms": round(old_median * 1000, 3),
        "optimized_median_ms": round(new_median * 1000, 3),
        "legacy_us_per_call": round(old_median * 1e6 / loops, 3),
        "optimized_us_per_call": round(new_median * 1e6 / loops, 3),
        "speedup": round(old_median / new_median, 3) if new_median else None,
        "outputs_identical": True,
    }


def _synthetic_frames(count: int):
    grays = []
    masks = []
    for frame_id in range(count):
        gray = np.full((240, 320), 48 + frame_id % 80, dtype=np.uint8)
        gray[108:132, :,] = np.clip(gray[108:132, :,].astype(np.int16) + 16, 0, 255).astype(np.uint8)
        mask = np.zeros((240, 320), dtype=np.uint8)
        x = 30 + frame_id * 2
        if x < 312:
            mask[108:132, x:min(320, x + 11)] = 255
            mask[108:132, min(320, x + 24):min(320, x + 34)] = 255
        grays.append(gray)
        masks.append(mask)
    return grays, masks


def _new_counter():
    return MaskCounter(count_axis="x", line_x=160, line_y=120, max_step=24,
                       max_lateral_step=10, component_cluster_gap=16,
                       prediction_max_missing=2)


def preview_pipeline_benchmark(frame_count: int = 165, trials: int = 3) -> dict:
    grays, masks = _synthetic_frames(frame_count)
    synchronous_seconds = []
    asynchronous_producer_seconds = []
    asynchronous_total_seconds = []
    encoded_frames = []
    encoder_ms = []
    handoff_ms = []

    for _ in range(trials):
        counter = _new_counter()
        started = time.perf_counter()
        for frame_id, (gray, mask) in enumerate(zip(grays, masks)):
            result = counter.update(frame_id, mask)
            if frame_id % 4 == 0:
                render_preview_jpeg(mask, total_count=result.total_count, line_y=120,
                                    count_axis="x", line_x=160, source_gray=gray,
                                    tracks=result.tracks)
        synchronous_seconds.append(time.perf_counter() - started)

        counter = _new_counter()
        hub = MjpegFrameHub()
        publisher = PeriodicMjpegPublisher(
            hub=hub, line_y=120, count_axis="x", line_x=160, interval=4, quality=70,
        )
        started = time.perf_counter()
        with hub.streaming_client():
            for frame_id, (gray, mask) in enumerate(zip(grays, masks)):
                result = counter.update(frame_id, mask)
                publisher(result, mask, gray)
            producer_elapsed = time.perf_counter() - started
        asynchronous_producer_seconds.append(producer_elapsed)
        publisher.close()
        asynchronous_total_seconds.append(time.perf_counter() - started)
        encoded_frames.append(publisher.encoded_frames)
        encoder_ms.extend(publisher.background_encode_timings_ms)
        handoff_ms.extend(publisher.handoff_timings_ms)
        hub.close()

    sync_median = statistics.median(synchronous_seconds)
    producer_median = statistics.median(asynchronous_producer_seconds)
    total_median = statistics.median(asynchronous_total_seconds)
    return {
        "input": f"{frame_count} synthetic Gray8/Binary8 pairs at 320x240",
        "trials": trials,
        "preview_interval_frames": 4,
        "jpeg_quality": 70,
        "synchronous_main_loop_median_ms": round(sync_median * 1000, 3),
        "asynchronous_producer_loop_median_ms": round(producer_median * 1000, 3),
        "asynchronous_encode_drain_median_ms": round(total_median * 1000, 3),
        "synchronous_loop_fps": round(frame_count / sync_median, 3),
        "asynchronous_producer_fps": round(frame_count / producer_median, 3),
        "producer_loop_speedup": round(sync_median / producer_median, 3)
        if producer_median else None,
        "async_jpeg_frames_encoded_by_trial": encoded_frames,
        "jpeg_encode_mean_ms": round(statistics.mean(encoder_ms), 3) if encoder_ms else None,
        "jpeg_encode_p95_ms": round(float(np.percentile(encoder_ms, 95)), 3) if encoder_ms else None,
        "publisher_handoff_mean_ms": round(statistics.mean(handoff_ms), 4) if handoff_ms else None,
        "publisher_handoff_p95_ms": round(float(np.percentile(handoff_ms, 95)), 4)
        if handoff_ms else None,
        "scope": "Host-only synthetic throughput microbenchmark; no camera, PYNQ, DMA, or board FPS claim",
    }


def main() -> None:
    report = {
        "date": "2026-10-22",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "numpy": np.__version__,
        "opencv": cv2.__version__,
        "board_accessed": False,
        "roi_projection": projection_benchmark(),
        "preview_pipeline": preview_pipeline_benchmark(),
    }
    output = Path(__file__).with_name("benchmark.json")
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
