"""Offline calibration and audit helpers for future WO-0016 Stage B wiring.

This module does not acquire camera frames, start the preview server, or touch
PYNQ/DMA hardware. Its CLI only reads paired PNG sequences when --offline is
explicitly supplied.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping

import cv2
import numpy as np

WIDTH = 320
HEIGHT = 240


@dataclass(frozen=True)
class FrameAnalysis:
    background_mean: float | None
    background_rms: float | None
    foreground_peak: int | None
    foreground_pixels: int
    component_areas: tuple[int, ...]
    roi_background_mean: float | None
    roi_background_rms: float | None
    roi_foreground_peak: int | None
    roi_foreground_pixels: int
    roi_component_count: int
    overlay_bgr: np.ndarray

    @property
    def component_count(self) -> int:
        return len(self.component_areas)


def _finite_number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number in {low}..{high}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number in {low}..{high}") from exc
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{name} must be a finite number in {low}..{high}")
    return number


def _validate_images(gray8: np.ndarray, binary8: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    gray = np.asarray(gray8)
    mask = np.asarray(binary8)
    expected = (HEIGHT, WIDTH)
    if gray.dtype != np.uint8 or gray.ndim != 2 or gray.shape != expected:
        raise ValueError(f"gray8 must be uint8 with shape {expected}")
    if mask.dtype != np.uint8 or mask.ndim != 2 or mask.shape != expected:
        raise ValueError(f"binary8 must be uint8 with shape {expected}")
    if np.any((mask != 0) & (mask != 0xFF)):
        raise ValueError("binary8 values must be 0x00 or 0xFF")
    return gray, mask


def _validate_roi(roi: tuple[int, int, int, int] | None) -> tuple[int, int, int, int]:
    if roi is None:
        return (0, 0, WIDTH, HEIGHT)
    if len(roi) != 4 or any(isinstance(value, bool) or not isinstance(value, (int, np.integer)) for value in roi):
        raise ValueError("roi must be integer coordinates (x0, y0, x1, y1)")
    x0, y0, x1, y1 = (int(value) for value in roi)
    if not (0 <= x0 < x1 <= WIDTH and 0 <= y0 < y1 <= HEIGHT):
        raise ValueError("roi coordinates must be inside the frame and have positive size")
    return x0, y0, x1, y1


def _validate_dma_status(value: Any) -> int | dict[str, int] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        if set(value) != {"mm2s", "s2mm"}:
            raise ValueError("DMA status mapping must contain exactly mm2s and s2mm")
        normalized: dict[str, int] = {}
        for key in ("mm2s", "s2mm"):
            item = value[key]
            if isinstance(item, bool) or not isinstance(item, (int, np.integer)) or not 0 <= item <= 0xFFFFFFFF:
                raise ValueError("DMA status values must be observed 32-bit unsigned integers")
            normalized[key] = int(item)
        return normalized
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("dmasr must be an observed 32-bit unsigned integer or None")
    return int(value)


def analyze_frame(gray8: np.ndarray, binary8: np.ndarray, *, threshold: float,
                  roi: tuple[int, int, int, int] | None = None,
                  fps: float | None = None, total_count: int | None = None,
                  count_line_y: int | None = None,
                  dma_status: Mapping[str, int] | None = None) -> FrameAnalysis:
    """Analyze separate Gray8 and Binary8 inputs and render a BGR calibration view.

    The mask identifies foreground/material pixels; it is never derived from
    the gray frame here. ROI uses exclusive right/bottom coordinates.
    """
    gray, mask = _validate_images(gray8, binary8)
    threshold_value = _finite_number(threshold, "threshold", 0, 255)
    fps_value = None if fps is None else _finite_number(fps, "fps", 0, 1_000_000)
    x0, y0, x1, y1 = _validate_roi(roi)
    if total_count is not None and (isinstance(total_count, bool) or not isinstance(total_count, (int, np.integer)) or total_count < 0):
        raise ValueError("total_count must be a nonnegative integer or None")
    if count_line_y is not None and (isinstance(count_line_y, bool) or not isinstance(count_line_y, (int, np.integer)) or not 0 <= count_line_y < HEIGHT):
        raise ValueError("count_line_y must be inside the frame or None")
    normalized_dma = _validate_dma_status(dma_status)
    if normalized_dma is not None and not isinstance(normalized_dma, dict):
        raise ValueError("dma_status must map both MM2S and S2MM status registers")

    foreground = mask == 0xFF
    background = ~foreground
    background_mean = float(np.mean(gray[background], dtype=np.float64)) if np.any(background) else None
    background_rms = (float(np.sqrt(np.mean(np.square(gray[background], dtype=np.float64))))
                      if np.any(background) else None)
    foreground_peak = int(np.max(gray[foreground])) if np.any(foreground) else None
    roi_gray = gray[y0:y1, x0:x1]
    roi_mask = mask[y0:y1, x0:x1]
    roi_foreground = roi_mask == 0xFF
    roi_background = ~roi_foreground
    roi_background_mean = (float(np.mean(roi_gray[roi_background], dtype=np.float64))
                           if np.any(roi_background) else None)
    roi_background_rms = (float(np.sqrt(np.mean(np.square(roi_gray[roi_background], dtype=np.float64))))
                          if np.any(roi_background) else None)
    roi_foreground_peak = int(np.max(roi_gray[roi_foreground])) if np.any(roi_foreground) else None
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8, ltype=cv2.CV_32S)
    areas = tuple(int(stats[label, cv2.CC_STAT_AREA]) for label in range(1, count))
    roi_count, _, _, _ = cv2.connectedComponentsWithStats(
        roi_mask, connectivity=8, ltype=cv2.CV_32S
    )

    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    text = [
        f"T={threshold_value:g} count={total_count if total_count is not None else 'None'} FPS={fps_value:.1f}" if fps_value is not None else f"T={threshold_value:g} count={total_count if total_count is not None else 'None'} FPS=None",
        f"BG mean/RMS={background_mean:.1f}/{background_rms:.1f}" if background_mean is not None and background_rms is not None else "BG mean/RMS=None",
        f"ROI BG mean/RMS={roi_background_mean:.1f}/{roi_background_rms:.1f}" if roi_background_mean is not None and roi_background_rms is not None else "ROI BG mean/RMS=None",
        f"FG peak={foreground_peak} pixels={int(np.count_nonzero(foreground))}",
        f"ROI FG peak={roi_foreground_peak} pixels={int(np.count_nonzero(roi_foreground))}",
        f"CCL full/ROI={len(areas)}/{roi_count - 1}",
        (f"DMA MM2S={normalized_dma['mm2s']:04x} S2MM={normalized_dma['s2mm']:04x}"
         if normalized_dma is not None else "DMA MM2S/S2MM=None"),
    ]
    cv2.rectangle(overlay, (2, 2), (WIDTH - 3, 16 + 15 * len(text)), (0, 0, 0), -1)
    for index, line in enumerate(text):
        y = 14 + index * 15
        cv2.putText(overlay, line, (5, y), cv2.FONT_HERSHEY_SIMPLEX, 0.36,
                    (255, 255, 255), 1, cv2.LINE_AA)
    cv2.rectangle(overlay, (x0, y0), (x1 - 1, y1 - 1), (0, 255, 0), 1, cv2.LINE_8)
    if count_line_y is not None:
        cv2.line(overlay, (0, int(count_line_y)), (WIDTH - 1, int(count_line_y)),
                 (0, 0, 255), 1, cv2.LINE_8)
    return FrameAnalysis(
        background_mean=background_mean,
        background_rms=background_rms,
        foreground_peak=foreground_peak,
        foreground_pixels=int(np.count_nonzero(foreground)),
        component_areas=areas,
        roi_background_mean=roi_background_mean,
        roi_background_rms=roi_background_rms,
        roi_foreground_peak=roi_foreground_peak,
        roi_foreground_pixels=int(np.count_nonzero(roi_foreground)),
        roi_component_count=roi_count - 1,
        overlay_bgr=overlay,
    )


class BatchAuditRecorder:
    """Collect timestamped analysis; event times mean observation, not crossing."""

    def __init__(self, *, threshold: float, roi: tuple[int, int, int, int] | None = None,
                 fps: float | None = None, count_line_y: int = HEIGHT // 2):
        self.threshold = _finite_number(threshold, "threshold", 0, 255)
        self.roi = _validate_roi(roi)
        self.fps = None if fps is None else _finite_number(fps, "fps", 0, 1_000_000)
        if isinstance(count_line_y, bool) or not isinstance(count_line_y, int) or not 0 <= count_line_y < HEIGHT:
            raise ValueError("count_line_y must be inside the frame")
        self.count_line_y = count_line_y
        self.frames: list[dict[str, Any]] = []
        self.count_event_observations: list[dict[str, Any]] = []

    def record_frame(self, *, frame_id: int, gray8: np.ndarray, binary8: np.ndarray,
                     monotonic_s: float, frame_result: Any | None = None,
                     dmasr: int | Mapping[str, int] | None = None,
                     fps: float | None = None,
                     empty_belt_assumption: bool = False) -> FrameAnalysis:
        if isinstance(frame_id, bool) or not isinstance(frame_id, (int, np.integer)) or frame_id < 0:
            raise ValueError("frame_id must be a nonnegative integer")
        observed = _finite_number(monotonic_s, "monotonic_s", 0, float("inf"))
        if self.frames and int(frame_id) <= self.frames[-1]["frame_id"]:
            raise ValueError("frame_id values must strictly increase")
        if self.frames and observed <= self.frames[-1]["monotonic_s"]:
            raise ValueError("monotonic_s values must strictly increase")
        if not isinstance(empty_belt_assumption, bool):
            raise ValueError("empty_belt_assumption must be boolean")
        dma_snapshot = _validate_dma_status(dmasr)
        fps_value = self.fps if fps is None else _finite_number(fps, "fps", 0, 1_000_000)
        analysis = analyze_frame(
            gray8, binary8, threshold=self.threshold, roi=self.roi, fps=fps_value,
            total_count=None if frame_result is None else int(frame_result.total_count),
            count_line_y=self.count_line_y,
            dma_status=dma_snapshot if isinstance(dma_snapshot, dict) else None,
        )
        if frame_result is not None and int(frame_result.frame_id) != int(frame_id):
            raise ValueError("frame_result.frame_id must match frame_id")

        self.frames.append({
            "frame_id": int(frame_id),
            "monotonic_s": observed,
            "fps": fps_value,
            "background_gray_mean": analysis.background_mean,
            "background_gray_rms": analysis.background_rms,
            "roi_background_gray_mean": analysis.roi_background_mean,
            "roi_background_gray_rms": analysis.roi_background_rms,
            "foreground_gray_peak": analysis.foreground_peak,
            "foreground_pixels": analysis.foreground_pixels,
            "roi_foreground_gray_peak": analysis.roi_foreground_peak,
            "roi_foreground_pixels": analysis.roi_foreground_pixels,
            "component_count": analysis.component_count,
            "roi_component_count": analysis.roi_component_count,
            "component_areas": list(analysis.component_areas),
            "assumed_false_trigger_pixels": analysis.foreground_pixels if empty_belt_assumption else None,
            "empty_belt_assumption": empty_belt_assumption,
            "dmasr": dma_snapshot,
            "total_count": None if frame_result is None else int(frame_result.total_count),
        })
        if frame_result is not None:
            for event in frame_result.events:
                self.count_event_observations.append({
                    "track_id": int(event.track_id),
                    "event_frame_id": int(event.frame_id),
                    "observation_monotonic_s": observed,
                    "timestamp_semantics": "observation time in FrameResult; not exact physical crossing time",
                })
        return analysis

    def to_dict(self) -> dict[str, Any]:
        if self.frames:
            duration = self.frames[-1]["monotonic_s"] - self.frames[0]["monotonic_s"]
            flags = {frame["empty_belt_assumption"] for frame in self.frames}
            empty_assumption: bool | str = flags.pop() if len(flags) == 1 else "mixed"
            assumed_total = (sum(frame["assumed_false_trigger_pixels"] for frame in self.frames
                                 if frame["assumed_false_trigger_pixels"] is not None)
                            if any(frame["assumed_false_trigger_pixels"] is not None for frame in self.frames)
                            else None)
            duration_classification = "at_least_5_seconds" if duration >= 5.0 else "under_5_seconds"
        else:
            duration = None
            empty_assumption = None
            assumed_total = None
            duration_classification = "no_samples"
        return {
            "schema": "wo0016-stageb-analysis-v1",
            "threshold": self.threshold,
            "roi_xyxy_exclusive": list(self.roi),
            "count_line_y": self.count_line_y,
            "fps_input": self.fps,
            "time_semantics": "caller-supplied monotonic observation time; count event time is observation, not crossing",
            "observed_duration_s": duration,
            "observation_span_at_least_5s": bool(duration is not None and duration >= 5.0),
            "duration_classification": duration_classification,
            "five_second_sampling_verified": False,
            "empty_belt_assumption": empty_assumption,
            "assumed_false_trigger_pixel_total": assumed_total,
            "frames": list(self.frames),
            "count_event_observations": list(self.count_event_observations),
        }


def _png_map(folder: Path) -> dict[str, Path]:
    return {path.stem: path for path in sorted(folder.glob("*.png")) if path.is_file()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline paired-PNG Stage B calibration/audit helper")
    parser.add_argument("--offline", action="store_true", help="required marker: process PNG files only")
    parser.add_argument("--gray-dir", type=Path, required=True)
    parser.add_argument("--binary-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=128.0)
    parser.add_argument("--roi", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"), default=(0, 0, WIDTH, HEIGHT))
    parser.add_argument("--fps", type=float)
    parser.add_argument("--empty-belt-assumption", action="store_true")
    args = parser.parse_args(argv)
    if not args.offline:
        parser.error("--offline is required; this CLI does not acquire live camera frames")
    if not args.gray_dir.is_dir() or not args.binary_dir.is_dir():
        parser.error("gray and binary input directories must exist")
    if args.output_dir.exists():
        parser.error("output directory already exists; choose a new path to preserve audit evidence")
    gray_files, mask_files = _png_map(args.gray_dir), _png_map(args.binary_dir)
    if not gray_files or gray_files.keys() != mask_files.keys():
        parser.error("gray and binary directories must contain matching, non-empty PNG filename stems")

    recorder = BatchAuditRecorder(threshold=args.threshold, roi=tuple(args.roi), fps=args.fps)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    png_names = []
    for frame_id, stem in enumerate(sorted(gray_files)):
        gray = cv2.imread(str(gray_files[stem]), cv2.IMREAD_UNCHANGED)
        mask = cv2.imread(str(mask_files[stem]), cv2.IMREAD_UNCHANGED)
        analysis = recorder.record_frame(
            frame_id=frame_id, gray8=gray, binary8=mask, monotonic_s=time.monotonic(),
            empty_belt_assumption=args.empty_belt_assumption,
        )
        name = f"calibration_{stem}.png"
        if not cv2.imwrite(str(args.output_dir / name), analysis.overlay_bgr):
            parser.error(f"failed to write calibration image: {name}")
        png_names.append(name)

    report = recorder.to_dict()
    report.update({
        "execution_mode": "offline_png_sequence",
        "time_source": "host monotonic processing observation; not camera capture time",
        "calibration_pngs": png_names,
    })
    (args.output_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
