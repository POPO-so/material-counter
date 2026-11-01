"""RLE regions and candidate-only masks feeding the existing PS tracker."""
from __future__ import annotations
from pathlib import Path
import sys
import time
import numpy as np

_LEGACY = Path(__file__).resolve().parents[1] / "wo0016"
if str(_LEGACY) not in sys.path:
    sys.path.insert(0, str(_LEGACY))
from scripts.wo0016.stream_counter import MaskCounter, Detection, FrameResult
from scripts.wo0016.split_touching import (analyze_split, split_touching_component,
                                          is_single_connected_component)
from .components import analyze_runs, local_mask
from .protocol import RleFrame


class RleCounter(MaskCounter):
    """Perform no frame rasterization in the normal region/tracking path."""

    def __init__(self, *, library, **settings):
        defaults = dict(width=640, height=400, line_y=200, line_x=320,
            roi_mode="full", min_area=48, max_area=3600,
            max_bbox_width=170, max_bbox_height=170, max_step=48,
            max_x_step=40, hysteresis=4, component_cluster_dx=0)
        defaults.update(settings)
        super().__init__(**defaults)
        self.library = library
        self.last_seen_frame_id = None
        self.invalid_frames = 0

    def update_rle(self, frame: RleFrame, *, capture_monotonic_ns=None) -> FrameResult | None:
        if not isinstance(frame, RleFrame):
            raise TypeError("expected a validated RleFrame")
        if self.last_seen_frame_id is not None and frame.frame_id <= self.last_seen_frame_id:
            raise ValueError("RLE frame IDs must strictly increase")
        if (frame.width, frame.height) != (self.width, self.height):
            raise ValueError("RLE geometry differs from tracker")
        top, bottom = self._roi_bounds()
        if frame.roi != (0, top, self.width, bottom):
            raise ValueError("RLE ROI differs from configured tracker ROI")
        self.last_seen_frame_id = frame.frame_id
        if not frame.valid:
            self.invalid_frames += 1
            return None

        started = time.perf_counter()
        timestamp = self._motion_timestamp(capture_monotonic_ns)
        self._split_origins = {}
        detections, rejected = [], []
        if frame.total_fg >= self.min_area:
            analysis = analyze_runs(frame.runs, library=self.library, min_area=self.min_area)
            for index, component in enumerate(analysis.components):
                if component.area < self.min_area:
                    continue
                detection = Detection(component.x_min, component.x_max, component.y_min,
                    component.y_max, component.area, component.centroid_x, component.centroid_y)
                if self._component_exceeds_limits(component.area,
                        component.x_max-component.x_min+1, component.y_max-component.y_min+1):
                    rejected.append(detection)
                    continue
                if self.guarded_merge and self.single_intact_area < component.area <= self.max_split_area:
                    mask = local_mask(frame.runs, analysis.labels, index,
                                      bounds=(component.x_min, component.y_min,
                                              component.x_max, component.y_max)) != 0
                    # Only run the full split when it has a hole or neck path
                    # that can accept a partition; intact single parts exit here.
                    # The analysis is computed once and handed to the splitter.
                    split_analysis = analyze_split(mask,
                        min_child_area=self.min_split_child_area,
                        min_hole_area=self.split_min_hole_area,
                        hole_area_cap=self.split_hole_area_cap)
                    if split_analysis.has_evidence:
                        pieces = split_touching_component(mask,
                            min_child_area=self.min_split_child_area, min_hole_area=self.split_min_hole_area,
                            recursive_item_area=self.split_recursive_item_area,
                            min_hole_distance=self.split_min_hole_distance,
                            hole_area_cap=self.split_hole_area_cap,
                            assume_connected=True, analysis=split_analysis)
                        # Keep the defensive check: the counter must never
                        # accept a disconnected or overlapping child set.
                        partitions = [np.asarray(piece) != 0 for piece in pieces]
                        valid = (2 <= len(partitions) <= 3 and all(piece.shape == mask.shape
                            and is_single_connected_component(piece) for piece in partitions))
                        if valid:
                            coverage = np.sum(np.stack(partitions), axis=0)
                            valid = np.array_equal(coverage > 0, mask) and not np.any(coverage > 1)
                        if valid:
                            for piece in partitions:
                                ys, xs = np.nonzero(piece)
                                child = Detection(component.x_min+int(xs.min()), component.x_min+int(xs.max()),
                                    component.y_min+int(ys.min()), component.y_min+int(ys.max()), len(xs),
                                    component.x_min+float(xs.mean()), component.y_min+float(ys.mean()))
                                detections.append(child)
                                self._split_origins[child] = index
                            continue
                detections.append(detection)
        self._set_rejected_components(rejected)
        detections.sort(key=lambda d: (d.centroid_x, d.centroid_y, d.y_min, d.x_min))
        raw = tuple(detections)
        detections = self._cluster_components(detections)
        region_ms = (time.perf_counter()-started)*1000
        started = time.perf_counter()
        self.last_frame_id = frame.frame_id
        events = self._associate_and_update(frame.frame_id, detections, timestamp)
        track_ms = (time.perf_counter()-started)*1000
        return FrameResult(frame_id=frame.frame_id, total_count=self.total_count,
            detections=tuple(detections), events=tuple(events), roi_top=top, roi_bottom=bottom,
            row_projection=tuple(frame.row_projection[top:bottom].tolist()),
            column_projection=tuple(frame.column_projection.tolist()),
            roi_foreground_pixels=frame.total_fg, ccl_executed=frame.total_fg>=self.min_area,
            roi_subtimings_ms={"rle_regions_ms": region_ms, "track_ms": track_ms},
            raw_detections=raw, suppressed_events=tuple(self.last_suppressed_events),
            tracks=self.tracking_snapshot(), glare_rejected_count=self.glare_rejected_count,
            glare_area_px=self.glare_area_px)
