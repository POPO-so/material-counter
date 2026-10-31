"""PYNQ-Z2 Binary8 double-buffer stream counter, with host-testable boundaries."""
from __future__ import annotations

import asyncio
import inspect
import math
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterable, Callable, Protocol

import cv2
import numpy as np

try:
    from .split_touching import is_single_connected_component, split_touching_component
    from .binary_stats import Binary8RoiValidator
    from .motion_association import ordered_matches
except ImportError:
    from split_touching import is_single_connected_component, split_touching_component
    from binary_stats import Binary8RoiValidator
    from motion_association import ordered_matches

WIDTH = 320
HEIGHT = 240
PIXELS = WIDTH * HEIGHT
RX_BYTES = PIXELS
try:
    from packet_encoder import TX_BYTES
except ImportError:  # pragma: no cover - package-style import support
    from .packet_encoder import TX_BYTES


class PacketEncoder(Protocol):
    def __call__(self, gray: np.ndarray, *, frame_id: int) -> bytes: ...


class PacketEncoderInto(Protocol):
    def __call__(self, gray: np.ndarray, output: Any, *, frame_id: int) -> None: ...


@dataclass(frozen=True)
class FrameInput:
    """Gray8 FPGA input plus host-side camera read identity and timestamps."""
    gray: np.ndarray
    source_sequence: int
    capture_monotonic_ns: int
    capture_wall_time_ns: int
    camera_read_ms: float
    source_gray: np.ndarray | None = None


class BufferState(str, Enum):
    FREE = "FREE"
    DMA_OWNED = "DMA_OWNED"
    CPU_READY = "CPU_READY"
    CPU_OWNED = "CPU_OWNED"
    HELD = "HELD"


@dataclass
class DmaBufferSlot:
    index: int
    tx_buffer: Any
    rx_buffer: Any
    cached_mask: np.ndarray | None = None
    state: BufferState = BufferState.FREE
    frame_id: int | None = None
    capture_gray: np.ndarray | None = None
    capture_mask: np.ndarray | None = None
    preview_gray: np.ndarray | None = None
    capture_metadata: dict[str, Any] | None = None
    capture_source_gray: np.ndarray | None = None
    dma_complete_monotonic_ns: int | None = None
    capture_monotonic_ns: int | None = None


@dataclass(frozen=True)
class Detection:
    x_min: int
    x_max: int
    y_min: int
    y_max: int
    area: int
    centroid_x: float
    centroid_y: float


@dataclass(frozen=True)
class CountEvent:
    track_id: int
    frame_id: int
    centroid_x: float
    centroid_y: float
    inferred_gap: bool = False


@dataclass(frozen=True)
class FrameResult:
    frame_id: int
    total_count: int
    detections: tuple[Detection, ...]
    events: tuple[CountEvent, ...]
    roi_top: int
    roi_bottom: int
    row_projection: tuple[int, ...]
    column_projection: tuple[int, ...]
    roi_foreground_pixels: int = 0
    ccl_executed: bool = False
    roi_subtimings_ms: dict[str, float] = field(default_factory=dict, compare=False)
    raw_detections: tuple[Detection, ...] = ()
    suppressed_events: tuple[CountEvent, ...] = ()
    tracks: tuple[dict[str, Any], ...] = ()
    glare_rejected_count: int = 0
    glare_area_px: int = 0


@dataclass
class _Track:
    track_id: int
    detection: Detection
    last_frame: int
    streak: int = 1
    confirmed: bool = False
    armed: bool = False
    counted: bool = False
    missed: int = 0
    finished: bool = False
    forward_velocity: float | None = None
    capture_monotonic_ns: int | None = None
    velocity_px_per_s: float | None = None


class MaskCounter:
    """Count confirmed components across a configurable X or Y line.

    The ROI can cover one conveyor lane or the full frame. max_step is
    longitudinal; max_lateral_step is perpendicular to motion.
    """

    def __init__(self, *, width: int = WIDTH, height: int = HEIGHT,
                 line_y: int = 120, roi_half_height: int = 24,
                 roi_mode: str = "lane", roi_top: int = 0, roi_bottom: int | None = None,
                 min_area: int = 12, direction: int = 1,
                 hysteresis: float = 2.0, confirm_hits: int = 2,
                 max_missing: int = 1, max_step: float = 24.0,
                 max_x_step: float = 20.0, backward_tolerance: float = 2.0,
                 count_axis: str = "y", line_x: int = 160,
                 max_lateral_step: float | None = None,
                 max_area: int | None = 900,
                 max_bbox_width: int = 85, max_bbox_height: int = 85,
                 component_cluster_dx: float = 0.0, component_cluster_dy: float = 12.0,
                 component_cluster_gap: float = 0.0,
                 guarded_merge: bool = False, single_intact_area: int = 95,
                 max_merge_area: int = 260, max_merged_span: int = 24,
                 max_split_area: int | None = None, min_split_child_area: int | None = None,
                 split_min_hole_area: int = 30,
                 split_min_hole_distance: float = 10,
                 split_hole_area_cap: int = 16,
                 split_recursive_item_area: int | None = None,
                 strict_lateral_gate: bool = False,
                 cooldown_frames: int = 0, cooldown_lateral: float = 15.0,
                 edge_max_missing: int = 0, prediction_max_missing: int = 0,
                 discard_finished_tracks: bool = False,
                 motion_timebase_s: float | None = None,
                 predictive_association: bool = False,
                 max_association_interval_s: float = 0.5):
        self.width = _integer(width, "width", minimum=1)
        self.height = _integer(height, "height", minimum=1)
        self.line_y = _integer(line_y, "line_y", minimum=0)
        if self.line_y >= self.height:
            raise ValueError("line_y must be inside the frame")
        self.roi_half_height = _integer(roi_half_height, "roi_half_height", minimum=1)
        if not isinstance(roi_mode, str) or roi_mode not in ("lane", "full"):
            raise ValueError("roi_mode must be lane or full")
        self.roi_mode = roi_mode
        self.roi_top = _integer(roi_top, "roi_top", minimum=0)
        self.roi_bottom = self.height if roi_bottom is None else _integer(roi_bottom, "roi_bottom", minimum=1)
        if not 0 <= self.roi_top < self.roi_bottom <= self.height:
            raise ValueError("ROI must satisfy 0 <= top < bottom <= frame height")
        self.min_area = _integer(min_area, "min_area", minimum=1)
        self.max_area = None if max_area is None else _integer(max_area, "max_area", minimum=self.min_area)
        self.max_bbox_width = _integer(max_bbox_width, "max_bbox_width", minimum=1)
        self.max_bbox_height = _integer(max_bbox_height, "max_bbox_height", minimum=1)
        if count_axis not in ("x", "y"):
            raise ValueError("count_axis must be x or y")
        self.count_axis = count_axis
        self.line_x = _integer(line_x, "line_x", minimum=0)
        if self.line_x >= self.width:
            raise ValueError("line_x must be inside the frame")
        if direction not in (-1, 1):
            raise ValueError("direction must be +1 (top to bottom) or -1")
        self.direction = direction
        self.hysteresis = _positive_float(hysteresis, "hysteresis")
        self.confirm_hits = _integer(confirm_hits, "confirm_hits", minimum=2)
        self.max_missing = _integer(max_missing, "max_missing", minimum=0)
        self.max_step = _positive_float(max_step, "max_step")
        self.max_x_step = _positive_float(max_x_step, "max_x_step")
        self.max_lateral_step = (self.max_x_step if max_lateral_step is None else
                                 _positive_float(max_lateral_step, "max_lateral_step"))
        if not isinstance(strict_lateral_gate, bool):
            raise ValueError("strict_lateral_gate must be a bool")
        self.strict_lateral_gate = strict_lateral_gate
        self.backward_tolerance = _nonnegative_float(backward_tolerance, "backward_tolerance")
        self.component_cluster_dx = _nonnegative_float(component_cluster_dx, "component_cluster_dx")
        self.component_cluster_dy = _nonnegative_float(component_cluster_dy, "component_cluster_dy")
        self.component_cluster_gap = _nonnegative_float(
            component_cluster_gap, "component_cluster_gap"
        )
        if not isinstance(guarded_merge, bool):
            raise ValueError("guarded_merge must be a bool")
        self.guarded_merge = guarded_merge
        self.single_intact_area = _integer(
            single_intact_area, "single_intact_area", minimum=1
        )
        self.max_merge_area = _integer(max_merge_area, "max_merge_area", minimum=1)
        self.max_split_area = (self.max_merge_area if max_split_area is None else
                               _integer(max_split_area, "max_split_area", minimum=1))
        self.split_min_hole_area = _integer(split_min_hole_area, "split_min_hole_area", minimum=1)
        self.split_min_hole_distance = _positive_float(split_min_hole_distance, "split_min_hole_distance")
        self.split_hole_area_cap = _integer(split_hole_area_cap, "split_hole_area_cap", minimum=1)
        self.split_recursive_item_area = (
            None if split_recursive_item_area is None else
            _integer(split_recursive_item_area, "split_recursive_item_area", minimum=1)
        )
        self.min_split_child_area = (self.min_area if min_split_child_area is None else
                                     _integer(min_split_child_area, "min_split_child_area", minimum=self.min_area))
        self.max_merged_span = _integer(max_merged_span, "max_merged_span", minimum=1)
        self.cooldown_frames = _integer(cooldown_frames, "cooldown_frames", minimum=0)
        self.cooldown_lateral = _nonnegative_float(cooldown_lateral, "cooldown_lateral")
        self.edge_max_missing = _integer(edge_max_missing, "edge_max_missing", minimum=0)
        self.prediction_max_missing = _integer(
            prediction_max_missing, "prediction_max_missing", minimum=0
        )
        if not isinstance(discard_finished_tracks, bool):
            raise ValueError("discard_finished_tracks must be a bool")
        self.discard_finished_tracks = discard_finished_tracks
        self.motion_timebase_s = (None if motion_timebase_s is None else
                                  _positive_float(motion_timebase_s, "motion_timebase_s"))
        if not isinstance(predictive_association, bool):
            raise ValueError('predictive_association must be bool')
        if predictive_association and self.motion_timebase_s is None:
            raise ValueError('predictive association requires motion_timebase_s')
        self.predictive_association = predictive_association
        self.max_association_interval_s = _positive_float(max_association_interval_s, 'max_association_interval_s')
        self._last_capture_monotonic_ns = None
        self._recent_events: list[CountEvent] = []
        self.last_suppressed_events: list[CountEvent] = []
        self.total_count = 0
        self.last_frame_id: int | None = None
        self._tracks: dict[int, _Track] = {}
        self._next_track_id = 1
        self.last_associations: list[dict[str, Any]] = []
        self.glare_rejected_count = 0
        self.glare_area_px = 0
        self.rejected_detections: tuple[Detection, ...] = ()
        self.rejected_contours: tuple[np.ndarray, ...] = ()
        self._split_origins: dict[Detection, int] = {}
        self._binary_validator = Binary8RoiValidator()

    def update_detections(self, frame_id: int, detections: Any, *,
                          capture_monotonic_ns: int | None = None) -> FrameResult:
        """Replay recorded qualified CCL detections through the live tracker.

        No masks are reconstructed and no segmentation claims are made here.
        Area filtering is identical to the live CCL path.
        """
        frame_id = _integer(frame_id, "frame_id", minimum=0)
        self._split_origins = {}
        if self.last_frame_id is not None and frame_id <= self.last_frame_id:
            raise ValueError("frame IDs must strictly increase")
        timestamp = self._motion_timestamp(capture_monotonic_ns)
        accepted = []
        rejected = []
        for detection in detections:
            if not isinstance(detection, Detection):
                raise ValueError("replay requires Detection records")
            if (not 0 <= detection.x_min <= detection.centroid_x <= detection.x_max < self.width
                    or not 0 <= detection.y_min <= detection.centroid_y <= detection.y_max < self.height
                    or detection.area < 1
                    or detection.area > (detection.x_max-detection.x_min+1)*(detection.y_max-detection.y_min+1)):
                raise ValueError("invalid recorded component geometry")
            if detection.area < self.min_area:
                continue
            if self._component_exceeds_limits(
                detection.area,
                detection.x_max - detection.x_min + 1,
                detection.y_max - detection.y_min + 1,
            ):
                rejected.append(detection)
            else:
                accepted.append(detection)
        self._set_rejected_components(rejected)
        accepted.sort(key=lambda d: (d.centroid_x, d.centroid_y, d.y_min, d.x_min))
        raw_detections = tuple(accepted)
        accepted = self._cluster_components(accepted)
        self.last_frame_id = frame_id
        events = self._associate_and_update(frame_id, accepted, timestamp)
        top, bottom = self._roi_bounds()
        return FrameResult(frame_id, self.total_count, tuple(accepted), tuple(events),
                           top, bottom, (), (), raw_detections=raw_detections,
                           suppressed_events=tuple(self.last_suppressed_events),
                           tracks=self.tracking_snapshot(),
                           glare_rejected_count=self.glare_rejected_count,
                           glare_area_px=self.glare_area_px)

    def tracking_snapshot(self) -> tuple[dict[str, Any], ...]:
        """Return serializable track state without mutating tracker behavior."""
        return tuple({
            "track_id": track.track_id,
            "detection": {
                "x_min": track.detection.x_min,
                "x_max": track.detection.x_max,
                "y_min": track.detection.y_min,
                "y_max": track.detection.y_max,
                "area": track.detection.area,
                "centroid_x": track.detection.centroid_x,
                "centroid_y": track.detection.centroid_y,
            },
            "last_frame": track.last_frame,
            "streak": track.streak,
            "confirmed": track.confirmed,
            "armed": track.armed,
            "counted": track.counted,
            "missed": track.missed,
            "finished": track.finished,
            "forward_velocity": track.forward_velocity,
        } for track in sorted(self._tracks.values(), key=lambda item: item.track_id))

    def _area_allowed(self, area: int) -> bool:
        return area >= self.min_area and (self.max_area is None or area <= self.max_area)

    def _component_exceeds_limits(self, area: int, box_width: int, box_height: int) -> bool:
        return (
            (self.max_area is not None and area > self.max_area)
            or box_width > self.max_bbox_width
            or box_height > self.max_bbox_height
        )

    def _set_rejected_components(
        self,
        detections: list[Detection],
        contours: list[np.ndarray] | None = None,
    ) -> None:
        self.rejected_detections = tuple(detections)
        self.glare_rejected_count = len(detections)
        self.glare_area_px = sum(detection.area for detection in detections)
        self.rejected_contours = tuple(contours or ())

    def _motion_timestamp(self, timestamp: int | None) -> int | None:
        """Require a monotonic host capture time only in the explicitly enabled mode."""
        if self.motion_timebase_s is None:
            return None
        timestamp = _integer(timestamp, "capture_monotonic_ns", minimum=1)
        if (self._last_capture_monotonic_ns is not None
                and timestamp <= self._last_capture_monotonic_ns):
            raise ValueError("capture_monotonic_ns must strictly increase")
        return timestamp

    def update(self, frame_id: int, mask: np.ndarray, *,
               capture_monotonic_ns: int | None = None) -> FrameResult:
        frame_id = _integer(frame_id, "frame_id", minimum=0)
        if self.last_frame_id is not None and frame_id <= self.last_frame_id:
            raise ValueError("frame IDs must strictly increase")
        timestamp = self._motion_timestamp(capture_monotonic_ns)

        validate_started = time.perf_counter()
        image = np.asarray(mask)
        if image.dtype != np.uint8 or image.ndim != 2 or image.shape != (self.height, self.width):
            raise ValueError(f"mask must be a uint8 array with shape {(self.height, self.width)}")
        roi_top, roi_bottom = self._roi_bounds()
        roi = image[roi_top:roi_bottom, :]
        self._binary_validator.validate(roi)
        validate_ms = (time.perf_counter() - validate_started) * 1000.0

        projection_started = time.perf_counter()
        row_projection, column_projection, foreground_pixels = self._project_roi(roi)
        projection_ms = (time.perf_counter() - projection_started) * 1000.0

        ccl_started = time.perf_counter()
        raw_detections = self._roi_components(roi, roi_top, foreground_pixels)
        detections = self._cluster_components(raw_detections)
        ccl_ms = (time.perf_counter() - ccl_started) * 1000.0

        track_started = time.perf_counter()
        self.last_frame_id = frame_id
        events = self._associate_and_update(frame_id, detections, timestamp)
        track_ms = (time.perf_counter() - track_started) * 1000.0
        return FrameResult(
            frame_id=frame_id,
            total_count=self.total_count,
            detections=tuple(detections),
            events=tuple(events),
            roi_top=roi_top,
            roi_bottom=roi_bottom,
            row_projection=row_projection,
            column_projection=column_projection,
            roi_foreground_pixels=foreground_pixels,
            ccl_executed=foreground_pixels >= self.min_area,
            raw_detections=tuple(raw_detections),
            suppressed_events=tuple(self.last_suppressed_events),
            roi_subtimings_ms={
                "validate_ms": validate_ms,
                "projection_ms": projection_ms,
                "ccl_ms": ccl_ms,
                "track_ms": track_ms,
            },
            tracks=self.tracking_snapshot(),
            glare_rejected_count=self.glare_rejected_count,
            glare_area_px=self.glare_area_px,
        )

    def _roi_bounds(self) -> tuple[int, int]:
        if self.roi_mode == "full":
            return self.roi_top, self.roi_bottom
        top = max(self.roi_top, self.line_y - self.roi_half_height)
        bottom = min(self.roi_bottom, self.line_y + self.roi_half_height + 1)
        if bottom <= top:
            raise ValueError("counting lane does not intersect ROI")
        return top, bottom

    @staticmethod
    def _project_roi(roi: np.ndarray) -> tuple[tuple[int, ...], tuple[int, ...], int]:
        # This method is called only after Binary8 validation. OpenCV's native
        # reductions avoid NumPy's per-axis Python dispatch on the PYNQ CPU.
        row_sums = cv2.reduce(roi, 1, cv2.REDUCE_SUM, dtype=cv2.CV_32S).reshape(-1)
        column_sums = cv2.reduce(roi, 0, cv2.REDUCE_SUM, dtype=cv2.CV_32S).reshape(-1)
        row_counts = row_sums // 0xFF
        column_counts = column_sums // 0xFF
        row_projection = tuple(row_counts.tolist())
        column_projection = tuple(column_counts.tolist())
        # Every nonzero Binary8 pixel is 0xFF; reuse the row reduction for an
        # exact total rather than making another full-frame pass.
        foreground_pixels = int(np.sum(row_counts, dtype=np.int64))
        return row_projection, column_projection, foreground_pixels

    def _roi_components(self, roi: np.ndarray, top: int,
                        foreground_pixels: int) -> list[Detection]:
        self._set_rejected_components([])
        self._split_origins = {}
        if foreground_pixels < self.min_area:
            return []
        if not roi.flags.c_contiguous:
            roi = np.ascontiguousarray(roi)
        fast_ccl = getattr(cv2, "connectedComponentsWithStatsWithAlgorithm", None)
        fast_algorithm = getattr(cv2, "CCL_GRANA", None)
        if callable(fast_ccl) and fast_algorithm is not None:
            count, labels, stats, centroids = fast_ccl(
                roi, 8, cv2.CV_16U, fast_algorithm
            )
        else:
            count, labels, stats, centroids = cv2.connectedComponentsWithStats(
                roi, connectivity=8, ltype=cv2.CV_16U
            )
        qualified_labels = np.flatnonzero(
            stats[1:count, cv2.CC_STAT_AREA] >= self.min_area
        ) + 1
        detections = []
        rejected = []
        rejected_contours = []
        for label in qualified_labels:
            x, y, box_width, box_height, area = (
                int(value) for value in stats[label]
            )
            detection = Detection(
                x_min=x,
                x_max=x + box_width - 1,
                y_min=y + top,
                y_max=y + top + box_height - 1,
                area=area,
                centroid_x=float(centroids[label, 0]),
                centroid_y=float(centroids[label, 1] + top),
            )
            if self._component_exceeds_limits(area, box_width, box_height):
                rejected.append(detection)
                component_mask = cv2.compare(labels, int(label), cv2.CMP_EQ)
                contours, _ = cv2.findContours(
                    component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )
                for contour in contours:
                    full_frame_contour = contour.copy()
                    full_frame_contour[:, 0, 1] += top
                    rejected_contours.append(full_frame_contour)
                continue
            if (self.guarded_merge and self.single_intact_area < area <= self.max_split_area):
                cropped = labels[y:y + box_height, x:x + box_width] == label
                pieces = split_touching_component(cropped, min_child_area=self.min_split_child_area,
                                                  min_hole_area=self.split_min_hole_area,
                                                  recursive_item_area=self.split_recursive_item_area,
                                                  min_hole_distance=self.split_min_hole_distance,
                                                  hole_area_cap=self.split_hole_area_cap)
                piece_masks = [np.asarray(piece) != 0 for piece in pieces]
                valid_partition = (2 <= len(piece_masks) <= 3
                                   and all(piece.shape == cropped.shape
                                           and is_single_connected_component(piece)
                                           for piece in piece_masks))
                if valid_partition:
                    coverage = np.sum(np.stack(piece_masks), axis=0)
                    valid_partition = (np.array_equal(coverage > 0, cropped)
                                       and not np.any(coverage > 1))
                if valid_partition:
                    for piece in pieces:
                        ys, xs = np.nonzero(piece)
                        piece_area = len(xs)
                        child = Detection(
                            x_min=x + int(xs.min()), x_max=x + int(xs.max()),
                            y_min=top + y + int(ys.min()),
                            y_max=top + y + int(ys.max()), area=piece_area,
                            centroid_x=x + float(np.mean(xs)),
                            centroid_y=top + y + float(np.mean(ys)),
                        )
                        detections.append(child)
                        self._split_origins[child] = int(label)
                    continue
            detections.append(detection)
        self._set_rejected_components(rejected, rejected_contours)
        detections.sort(key=lambda d: (d.centroid_x, d.centroid_y, d.y_min, d.x_min))
        return detections

    def _extract_roi_detections(
        self, mask: np.ndarray
    ) -> tuple[list[Detection], int, int, tuple[int, ...], tuple[int, ...]]:
        top, bottom = self._roi_bounds()
        roi = mask[top:bottom, :]
        row_projection, column_projection, foreground_pixels = self._project_roi(roi)
        detections = self._roi_components(roi, top, foreground_pixels)
        return detections, top, bottom, row_projection, column_projection

    def _cluster_components(self, detections: list[Detection]) -> list[Detection]:
        """Apply the configured legacy or area/span-guarded cluster policy.

        Inputs have passed CCL area filtering. Keep original CCL records for
        audit; returned observations use area-weighted centroids.
        """
        if self.guarded_merge:
            ordered = sorted(
                detections,
                key=lambda d: (d.area, d.x_min, d.x_max, d.y_min, d.y_max,
                               d.centroid_x, d.centroid_y),
            )
            count = len(ordered)
            parent = list(range(count))
            group_area = [d.area for d in ordered]
            group_bbox = [
                (d.x_min, d.x_max, d.y_min, d.y_max) for d in ordered
            ]
            group_wx = [d.area * d.centroid_x for d in ordered]
            group_wy = [d.area * d.centroid_y for d in ordered]
            group_split_origins = [
                {self._split_origins[d]} if d in self._split_origins else set()
                for d in ordered
            ]

            def find(index: int) -> int:
                while parent[index] != index:
                    parent[index] = parent[parent[index]]
                    index = parent[index]
                return index

            for left_index in range(count):
                for right_index in range(left_index + 1, count):
                    left_root = find(left_index)
                    right_root = find(right_index)
                    if left_root == right_root:
                        continue
                    if group_split_origins[left_root] & group_split_origins[right_root]:
                        continue

                    left_box = group_bbox[left_root]
                    right_box = group_bbox[right_root]
                    gap_x = max(0, left_box[0] - right_box[1] - 1,
                                right_box[0] - left_box[1] - 1)
                    gap_y = max(0, left_box[2] - right_box[3] - 1,
                                right_box[2] - left_box[3] - 1)
                    if math.hypot(gap_x, gap_y) > self.component_cluster_gap:
                        continue
                    if (group_area[left_root] >= self.single_intact_area
                            and group_area[right_root] >= self.single_intact_area):
                        continue

                    combined_area = group_area[left_root] + group_area[right_root]
                    if combined_area > self.max_merge_area:
                        continue
                    merged_box = (
                        min(left_box[0], right_box[0]),
                        max(left_box[1], right_box[1]),
                        min(left_box[2], right_box[2]),
                        max(left_box[3], right_box[3]),
                    )
                    merged_width = merged_box[1] - merged_box[0] + 1
                    merged_height = merged_box[3] - merged_box[2] + 1
                    if (merged_width > self.max_merged_span
                            or merged_height > self.max_merged_span):
                        continue

                    # Keep the earliest (small-fragment-first) root stable.
                    if right_root < left_root:
                        left_root, right_root = right_root, left_root
                    parent[right_root] = left_root
                    group_area[left_root] = combined_area
                    group_bbox[left_root] = merged_box
                    group_wx[left_root] += group_wx[right_root]
                    group_wy[left_root] += group_wy[right_root]
                    group_split_origins[left_root].update(group_split_origins[right_root])

            merged = []
            for index in range(count):
                if find(index) != index:
                    continue
                area = group_area[index]
                if not self._area_allowed(area):
                    continue
                x_min, x_max, y_min, y_max = group_bbox[index]
                merged.append(Detection(
                    x_min, x_max, y_min, y_max, area,
                    group_wx[index] / area, group_wy[index] / area,
                ))
            return sorted(merged, key=lambda d: (
                d.centroid_x, d.centroid_y, d.y_min, d.x_min
            ))

        if self.component_cluster_gap > 0:
            count = len(detections)
            parent = list(range(count))
            rank = [0] * count

            def find(index: int) -> int:
                while parent[index] != index:
                    parent[index] = parent[parent[index]]
                    index = parent[index]
                return index

            def union(left: int, right: int) -> None:
                left_root, right_root = find(left), find(right)
                if left_root == right_root:
                    return
                if rank[left_root] < rank[right_root]:
                    left_root, right_root = right_root, left_root
                parent[right_root] = left_root
                if rank[left_root] == rank[right_root]:
                    rank[left_root] += 1

            for left_index, left in enumerate(detections):
                for right_index in range(left_index + 1, count):
                    right = detections[right_index]
                    gap_x = max(0, left.x_min - right.x_max - 1,
                                right.x_min - left.x_max - 1)
                    gap_y = max(0, left.y_min - right.y_max - 1,
                                right.y_min - left.y_max - 1)
                    if math.hypot(gap_x, gap_y) <= self.component_cluster_gap:
                        union(left_index, right_index)

            grouped: dict[int, list[Detection]] = {}
            for index, detection in enumerate(detections):
                grouped.setdefault(find(index), []).append(detection)
            groups = list(grouped.values())
        elif self.component_cluster_dx == 0:
            return detections
        else:
            groups = []
            for detection in detections:
                for group in groups:
                    if all(abs(detection.centroid_x-other.centroid_x) <= self.component_cluster_dx
                           and abs(detection.centroid_y-other.centroid_y) <= self.component_cluster_dy
                           for other in group):
                        group.append(detection)
                        break
                else:
                    groups.append([detection])
        merged = []
        for group in groups:
            area = sum(d.area for d in group)
            # Preserve max_area as a bound on the resulting entity as well.
            if not self._area_allowed(area):
                continue
            merged.append(Detection(min(d.x_min for d in group), max(d.x_max for d in group),
                min(d.y_min for d in group), max(d.y_max for d in group), area,
                sum(d.area*d.centroid_x for d in group)/area,
                sum(d.area*d.centroid_y for d in group)/area))
        return sorted(merged, key=lambda d: (d.centroid_x, d.centroid_y, d.y_min, d.x_min))

    def _edge(self, detection: Detection) -> int:
        top, bottom = self._roi_bounds()
        if detection.y_min <= top + 1:
            return -1
        if detection.y_max >= bottom - 2:
            return 1
        return 0

    def _missing_budget(self, track: _Track) -> int:
        prediction_budget = 0
        if (self.count_axis == "x" and track.confirmed and track.armed
                and not track.counted):
            prediction_budget = self.prediction_max_missing
        if (self.count_axis == "x" and track.confirmed and track.armed
                and not track.counted and self._edge(track.detection)):
            prediction_budget = max(prediction_budget, self.edge_max_missing)
        return max(self.max_missing, prediction_budget)

    def _side(self, detection: Detection) -> float:
        position = (detection.centroid_x - self.line_x if self.count_axis == "x"
                    else detection.centroid_y - self.line_y)
        return self.direction * position

    def _associate_and_update(self, frame_id: int,
                              detections: list[Detection],
                              capture_monotonic_ns: int | None = None) -> list[CountEvent]:
        active = []
        self.last_associations = []
        self.last_suppressed_events = []
        self._recent_events = [event for event in self._recent_events
                               if frame_id-event.frame_id <= self.cooldown_frames]
        for track in self._tracks.values():
            if track.finished:
                continue
            too_old = (self.predictive_association and
                       (capture_monotonic_ns-track.capture_monotonic_ns)/1e9 > self.max_association_interval_s)
            if frame_id - track.last_frame > self._missing_budget(track) + 1 or too_old:
                track.finished = True
            else:
                active.append(track)

        edges = []
        for track in active:
            dt = frame_id - track.last_frame
            motion_dt = dt
            velocity = track.forward_velocity
            if self.motion_timebase_s is not None:
                elapsed_s = (capture_monotonic_ns - track.capture_monotonic_ns) / 1e9
                motion_dt = elapsed_s / self.motion_timebase_s
                velocity = (None if track.velocity_px_per_s is None else
                            track.velocity_px_per_s * self.motion_timebase_s)
            old = track.detection
            for index, detection in enumerate(detections):
                dx = detection.centroid_x - old.centroid_x
                dy = detection.centroid_y - old.centroid_y
                lateral = abs(dy if self.count_axis == "x" else dx)
                forward = self.direction * (dx if self.count_axis == "x" else dy)
                area_ratio = detection.area / old.area
                # A clipped entry slice grows sharply when the nut becomes
                # visible. Compare bbox occupancy only for a one-frame,
                # incoming image-border slice and a nut-sized observation.
                entering = (old.x_min == 0 if self.direction == 1
                            else old.x_max == self.width - 1)
                size_limit = max(self.component_cluster_dx, 2*self.component_cluster_dy) + 3
                if (self.count_axis == "x" and self.component_cluster_dx > 0
                        and dt == 1 and entering and self._side(old) < -self.hysteresis
                        and detection.x_max-detection.x_min+1 <= size_limit
                        and detection.y_max-detection.y_min+1 <= size_limit):
                    old_box = (old.x_max-old.x_min+1)*(old.y_max-old.y_min+1)
                    new_box = (detection.x_max-detection.x_min+1)*(detection.y_max-detection.y_min+1)
                    area_ratio *= old_box/new_box
                missing_frames = max(0, dt - 1)
                gap = missing_frames > 0
                if missing_frames > self.max_missing:
                    velocity_ok = (
                        track.confirmed and track.armed and not track.counted
                        and velocity is not None
                        and velocity > 0
                        and abs(forward - velocity * motion_dt) <= self.max_step
                    )
                    edge_recovery = (
                        self._edge(old) != 0
                        and self._edge(old) == self._edge(detection)
                        and missing_frames <= self.edge_max_missing
                        and lateral <= self.max_lateral_step
                        and velocity_ok
                        and max(detection.x_max-detection.x_min+1,
                               detection.y_max-detection.y_min+1) <= size_limit
                    )
                    predicted_recovery = (
                        missing_frames <= self.prediction_max_missing and velocity_ok
                    )
                    if not (edge_recovery or predicted_recovery):
                        continue
                lateral_limit = (self.max_lateral_step if self.strict_lateral_gate
                                 else self.max_lateral_step * dt)
                if self.motion_timebase_s is not None and not (self.predictive_association and self.strict_lateral_gate):
                    lateral_limit = self.max_lateral_step * motion_dt
                if (lateral <= lateral_limit
                        and -self.backward_tolerance <= forward <= self.max_step * motion_dt
                        and 0.25 <= area_ratio <= 4.0):
                    predicted_x, predicted_y = old.centroid_x, old.centroid_y
                    if (missing_frames or self.predictive_association) and velocity is not None:
                        longitudinal_prediction = self.direction * velocity * motion_dt
                        if self.count_axis == "x":
                            predicted_x += longitudinal_prediction
                        else:
                            predicted_y += longitudinal_prediction
                    prediction_distance = ((detection.centroid_x-predicted_x) ** 2
                                           + (detection.centroid_y-predicted_y) ** 2)
                    if self.predictive_association:
                        # Transverse movement is a stronger identity cue than
                        # proximity to another nut's previous longitudinal position.
                        prediction_distance += 8 * lateral ** 2
                    edges.append((prediction_distance, track.track_id, index, gap))

        # An inferred gap must have a unique candidate in both directions.
        # Refuse nearest-neighbour guessing when more than one object fits.
        track_frequency = Counter(edge[1] for edge in edges)
        detection_frequency = Counter(edge[2] for edge in edges)
        ambiguous_tracks = {key for key, count in track_frequency.items() if count > 1}
        ambiguous_detections = {key for key, count in detection_frequency.items() if count > 1}

        if self.predictive_association:
            edges = ordered_matches([edge for edge in edges if not edge[3] or
                (edge[1] not in ambiguous_tracks and edge[2] not in ambiguous_detections)],
                active, detections, axis=self.count_axis, lane_distance=self.max_lateral_step)

        used_tracks: set[int] = set()
        used_detections: set[int] = set()
        events = []
        for _, track_id, index, gap in sorted(edges):
            if gap and (track_id in ambiguous_tracks or index in ambiguous_detections):
                continue
            if track_id in used_tracks or index in used_detections:
                continue
            track = self._tracks[track_id]
            detection = detections[index]
            dt = frame_id - track.last_frame
            self.last_associations.append({"track_id": track_id, "detection_index": index,
                                           "previous_frame": track.last_frame,
                                           "kind": ("edge_gap_inferred" if gap and self._edge(track.detection)
                                                    else "predicted_gap_inferred" if gap
                                                    else "matched"),
                                           "dx": detection.centroid_x-track.detection.centroid_x,
                                           "dy": detection.centroid_y-track.detection.centroid_y})
            track.streak = track.streak + 1 if dt == 1 else 1
            track.confirmed = track.confirmed or track.streak >= self.confirm_hits
            track.missed = 0
            longitudinal = (detection.centroid_x-track.detection.centroid_x
                            if self.count_axis == "x" else detection.centroid_y-track.detection.centroid_y)
            track.forward_velocity = self.direction*longitudinal/dt
            if self.motion_timebase_s is not None:
                elapsed_s = (capture_monotonic_ns - track.capture_monotonic_ns) / 1e9
                track.velocity_px_per_s = self.direction * longitudinal / elapsed_s
                track.capture_monotonic_ns = capture_monotonic_ns
            track.last_frame = frame_id
            track.detection = detection
            track.armed = track.armed or self._side(detection) < -self.hysteresis
            used_tracks.add(track_id)
            used_detections.add(index)
            if (track.armed and track.confirmed and not track.counted
                    and self._side(detection) >= self.hysteresis):
                track.counted = True
                event = CountEvent(track.track_id, frame_id,
                                   detection.centroid_x, detection.centroid_y, inferred_gap=gap)
                lateral_position = lambda e: (e.centroid_y if self.count_axis == "x" else e.centroid_x)
                duplicate = self.cooldown_frames > 0 and any(
                    frame_id-old.frame_id <= self.cooldown_frames
                    and abs(lateral_position(event)-lateral_position(old)) <= self.cooldown_lateral
                    for old in self._recent_events)
                if duplicate:
                    self.last_suppressed_events.append(event)
                else:
                    self.total_count += 1
                    events.append(event)
                    self._recent_events.append(event)

        for track in active:
            if track.track_id not in used_tracks:
                track.missed = frame_id - track.last_frame
                track.streak = 0
                if track.missed > self._missing_budget(track):
                    track.finished = True

        for index, detection in enumerate(detections):
            if index in used_detections:
                continue
            side = self._side(detection)
            born_after_arming_zone = side >= -self.hysteresis
            track = _Track(
                track_id=self._next_track_id,
                detection=detection,
                last_frame=frame_id,
                armed=side < -self.hysteresis,
                counted=born_after_arming_zone,
                confirmed=self.confirm_hits <= 1,
                capture_monotonic_ns=capture_monotonic_ns,
            )
            self._tracks[track.track_id] = track
            self.last_associations.append({"track_id": track.track_id, "detection_index": index,
                                           "previous_frame": None, "kind": "new", "dx": None, "dy": None})
            self._next_track_id += 1
        if self.discard_finished_tracks:
            for track_id in [track_id for track_id, track in self._tracks.items()
                             if track.finished]:
                del self._tracks[track_id]
        self._last_capture_monotonic_ns = capture_monotonic_ns
        return sorted(events, key=lambda event: event.track_id)


async def _wait_channel(channel: Any) -> None:
    wait = getattr(channel, "wait", None)
    if callable(wait):
        loop = asyncio.get_running_loop()
        completed = loop.create_future()

        def finish(error: BaseException | None) -> None:
            if completed.done():
                return
            if error is None:
                completed.set_result(None)
            else:
                completed.set_exception(error)

        def blocking_wait() -> None:
            error: BaseException | None = None
            try:
                wait()
            except BaseException as exc:
                error = exc
            try:
                loop.call_soon_threadsafe(finish, error)
            except RuntimeError:
                # A timed-out DMA wait may finish after the fail-hold loop closes.
                pass

        threading.Thread(target=blocking_wait, name="wo0016-dma-wait", daemon=True).start()
        await completed
        return

    wait_async = getattr(channel, "wait_async", None)
    if not callable(wait_async):
        raise TypeError("DMA channel must provide wait() or wait_async()")
    result = wait_async()
    if not inspect.isawaitable(result):
        raise TypeError("channel.wait_async() must return an awaitable")
    await result


def transfer_async(channel: Any, buffer: Any, nbytes: int) -> asyncio.Task:
    """Start PYNQ's non-blocking transfer and return an async completion task.

    PYNQ's ``transfer`` starts the operation and returns ``None``; completion
    methods belong to the channel. Prefer its blocking ``wait`` in a worker
    thread because PYNQ's ``wait_async`` requires a configured interrupt.
    """
    nbytes = _integer(nbytes, "nbytes", minimum=1)
    channel.transfer(buffer, nbytes=nbytes)
    return asyncio.create_task(_wait_channel(channel))


def allocate_ping_pong_buffers(allocate: Callable[..., Any], *,
                               free: Callable[[Any], None] | None = None,
                               after_allocate: Callable[[Any], None] | None = None,
                               tx_bytes: int = TX_BYTES
                               ) -> tuple[tuple[Any, Any], tuple[Any, Any]]:
    """Allocate and validate paired CMA slots, rolling back every partial allocation."""
    tx_bytes = _integer(tx_bytes, "tx_bytes", minimum=1)
    allocated = []
    tx_buffers = []
    rx_buffers = []

    def allocate_tracked(*, shape: tuple[int, ...], dtype: Any) -> Any:
        buffer = allocate(shape=shape, dtype=dtype)
        allocated.append(buffer)
        if after_allocate is not None:
            after_allocate(buffer)
        return buffer

    try:
        for _ in range(2):
            tx = allocate_tracked(shape=(tx_bytes,), dtype=np.uint8)
            rx = allocate_tracked(shape=(RX_BYTES,), dtype=np.uint8)
            _validate_buffer(tx, tx_bytes, 8, "TX")
            _validate_buffer(rx, RX_BYTES, 4, "RX")
            tx_buffers.append(tx)
            rx_buffers.append(rx)
        spans = []
        for kind, buffers, size in (("TX", tx_buffers, tx_bytes),
                                    ("RX", rx_buffers, RX_BYTES)):
            for index, buffer in enumerate(buffers):
                start = int(buffer.physical_address)
                spans.append((start, start + size, kind, index))
        spans.sort()
        for left, right in zip(spans, spans[1:]):
            if left[1] > right[0]:
                raise ValueError("allocated DMA buffers overlap")
    except BaseException as primary_error:
        # No DMA has started here, so every returned object is still ours to free.
        for buffer in reversed(allocated):
            try:
                if free is not None:
                    free(buffer)
                else:
                    release = getattr(buffer, "freebuffer", None)
                    if not callable(release):
                        release = getattr(buffer, "close", None)
                    if callable(release):
                        release()
            except BaseException as cleanup_error:
                note = f"CMA allocation rollback failed: {type(cleanup_error).__name__}: {cleanup_error}"
                if hasattr(primary_error, "add_note"):
                    primary_error.add_note(note)
                else:
                    errors = getattr(primary_error, "cleanup_errors", ())
                    try:
                        primary_error.cleanup_errors = tuple(errors) + (cleanup_error,)
                    except Exception:
                        pass
        raise
    return tuple(tx_buffers), tuple(rx_buffers)


def _validate_buffer(buffer: Any, expected_nbytes: int, alignment: int, name: str) -> None:
    if getattr(buffer, "nbytes", None) != expected_nbytes:
        raise ValueError(f"{name} CMA buffer must be exactly {expected_nbytes} bytes")
    address = getattr(buffer, "physical_address", None)
    if isinstance(address, bool) or not isinstance(address, (int, np.integer)) or int(address) <= 0:
        raise ValueError(f"{name} CMA buffer needs a non-zero integer physical_address")
    if int(address) % alignment:
        raise ValueError(f"{name} CMA buffer address must be {alignment}-byte aligned")
    view = np.asarray(buffer)
    if not view.flags.c_contiguous or view.dtype != np.uint8 or view.nbytes != expected_nbytes:
        raise ValueError(f"{name} CMA buffer must be contiguous uint8 storage of the exact size")


class DoubleBufferStream:
    """Overlap frame DMA with CPU processing using two exclusive TX/RX slots."""

    def __init__(self, *, recv_channel: Any, send_channel: Any,
                 tx_buffers: tuple[Any, Any], rx_buffers: tuple[Any, Any],
                 counter: MaskCounter,
                 packet_encoder: PacketEncoder,
                 packet_encoder_into: PacketEncoderInto | None = None,
                 before_transfer: Callable[[int, DmaBufferSlot], Any] | None = None,
                 before_rx_copy: Callable[[int, DmaBufferSlot], Any] | None = None,
                 after_transfer: Callable[[int, DmaBufferSlot], Any] | None = None,
                 on_frame: Callable[[FrameResult, np.ndarray], Any] | None = None,
                 preview_callback: Callable[[FrameResult, np.ndarray, np.ndarray], Any] | None = None,
                 raw_capture_callback: Callable[[int, np.ndarray, np.ndarray], None] | None = None,
                 source_capture_callback: Callable[[int, np.ndarray], None] | None = None,
                 capture_callback: Callable[[FrameResult, DmaBufferSlot], Any] | None = None,
                 stage_timings_ms: dict[str, list[float]] | None = None,
                 tx_bytes: int = TX_BYTES,
                 transfer_timeout_s: float = 5.0,
                 history_limit: int | None = None):
        self.tx_bytes = _integer(tx_bytes, "tx_bytes", minimum=1)
        if (history_limit is not None
                and (isinstance(history_limit, bool)
                     or not isinstance(history_limit, int) or history_limit < 1)):
            raise ValueError("history_limit must be a positive integer or None")
        if len(tx_buffers) != 2 or len(rx_buffers) != 2:
            raise ValueError("exactly two TX and two RX buffers are required")
        for buffer in tx_buffers:
            _validate_buffer(buffer, self.tx_bytes, 8, "TX")
        for buffer in rx_buffers:
            _validate_buffer(buffer, RX_BYTES, 4, "RX")
        self.recv_channel = recv_channel
        self.send_channel = send_channel
        self.counter = counter
        self.packet_encoder = packet_encoder
        self.packet_encoder_into = packet_encoder_into
        self.before_transfer = before_transfer
        self.before_rx_copy = before_rx_copy
        self.after_transfer = after_transfer
        self.on_frame = on_frame
        self.preview_callback = preview_callback
        self.raw_capture_callback = raw_capture_callback
        self.source_capture_callback = source_capture_callback
        self.capture_callback = capture_callback
        self.stage_timings_ms = stage_timings_ms
        self.history_limit = history_limit
        if history_limit is not None and stage_timings_ms is not None:
            for stage, samples in tuple(stage_timings_ms.items()):
                if not isinstance(samples, (list, deque)):
                    raise TypeError("stage timing histories must be lists or deques")
                if not isinstance(samples, deque) or samples.maxlen != history_limit:
                    stage_timings_ms[stage] = deque(samples, maxlen=history_limit)
        self._postprocess_ms: dict[int, float] = {}
        self.transfer_timeout_s = _positive_float(transfer_timeout_s, "transfer_timeout_s")
        self.slots = [
            DmaBufferSlot(i, tx_buffers[i], rx_buffers[i],
                          cached_mask=np.empty((HEIGHT, WIDTH), dtype=np.uint8),
                          capture_gray=(np.empty((HEIGHT, WIDTH), dtype=np.uint8)
                                        if capture_callback is not None
                                        or raw_capture_callback is not None else None),
                          capture_mask=(np.empty((HEIGHT, WIDTH), dtype=np.uint8)
                                        if capture_callback is not None
                                        or raw_capture_callback is not None else None),
                          preview_gray=(np.empty((HEIGHT, WIDTH), dtype=np.uint8)
                                        if preview_callback is not None
                                        and capture_callback is None else None))
            for i in range(2)
        ]
        self._free_slots: asyncio.Queue[DmaBufferSlot] = asyncio.Queue(maxsize=2)
        for slot in self.slots:
            self._free_slots.put_nowait(slot)
        self._ready_slots: asyncio.Queue[DmaBufferSlot | None] = asyncio.Queue(maxsize=2)
        self._results = ([] if history_limit is None else deque(maxlen=history_limit))
        self._last_result: FrameResult | None = None
        self._frames_processed = 0
        self._pending_waits: list[asyncio.Task] = []
        self.held_slots: tuple[DmaBufferSlot, ...] = ()
        self.dma_may_be_active = False
        self._has_run = False

    @staticmethod
    def _packet_frame_id(frame_id: int, *, bounded: bool) -> int:
        return frame_id & 0xFFFFFFFF if bounded else frame_id

    @property
    def frames_processed(self) -> int:
        return self._frames_processed

    @property
    def last_result(self) -> FrameResult | None:
        return self._last_result

    async def run(self, frames: AsyncIterable[np.ndarray | FrameInput], *,
                  stop_requested: Callable[[], bool] | None = None
                  ) -> tuple[FrameResult, ...]:
        if self._has_run:
            raise RuntimeError("a DoubleBufferStream instance can run only once")
        if stop_requested is not None and not callable(stop_requested):
            raise TypeError("stop_requested must be callable or None")
        self._has_run = True
        consumer = asyncio.create_task(self._consume_ready_slots())
        frame_iterator = frames.__aiter__()
        primary_error: BaseException | None = None
        pending_source_task: asyncio.Task | None = None
        frame_id = 0
        try:
            while True:
                if stop_requested is not None and stop_requested():
                    break
                if consumer.done():
                    consumer.result()
                pending_source_task = asyncio.create_task(frame_iterator.__anext__())
                source_wait_started = time.perf_counter()
                try:
                    while True:
                        done, _ = await asyncio.wait(
                            (pending_source_task, consumer), return_when=asyncio.FIRST_COMPLETED,
                            timeout=(0.05 if stop_requested is not None else None),
                        )
                        if done:
                            break
                        if stop_requested is not None and stop_requested():
                            source_cleanup_error = await self._cancel_frame_read(pending_source_task)
                            pending_source_task = None
                            if source_cleanup_error is not None:
                                raise source_cleanup_error
                            break
                finally:
                    self._record_timing("frame_source_wait",
                                        time.perf_counter() - source_wait_started)
                if pending_source_task is None:
                    break
                if consumer in done:
                    source_error = await self._cancel_frame_read(pending_source_task)
                    pending_source_task = None
                    try:
                        consumer.result()
                    except BaseException as consumer_error:
                        if source_error is not None:
                            _attach_cleanup_error(consumer_error, source_error)
                        raise
                    if source_error is not None:
                        raise source_error
                    raise RuntimeError("DMA consumer stopped before the frame source")
                try:
                    source_frame = pending_source_task.result()
                except StopAsyncIteration:
                    pending_source_task = None
                    break
                if consumer.done():
                    consumer.result()
                pending_source_task = None
                if stop_requested is not None and stop_requested():
                    break
                frame_input = source_frame if isinstance(source_frame, FrameInput) else None
                gray = np.asarray(frame_input.gray if frame_input is not None else source_frame)
                if gray.dtype != np.uint8 or gray.shape != (HEIGHT, WIDTH):
                    raise ValueError(f"source frames must be uint8 Gray8 {(HEIGHT, WIDTH)} arrays")
                slot = await self._get_free_slot_or_consumer_failure(consumer)
                if slot.state is not BufferState.FREE:
                    raise RuntimeError("buffer pool returned a slot that is not free")
                slot.state = BufferState.CPU_OWNED
                slot.frame_id = frame_id
                slot.dma_complete_monotonic_ns = None
                slot.capture_source_gray = None
                slot.capture_monotonic_ns = (None if frame_input is None else
                                             frame_input.capture_monotonic_ns)
                if frame_input is not None and frame_input.source_sequence != frame_id:
                    raise ValueError("camera source sequence does not match DMA frame ID")
                if frame_input is not None and frame_input.source_gray is not None:
                    source_gray = np.asarray(frame_input.source_gray)
                    if (source_gray.dtype != np.uint8 or source_gray.shape != (400, 640)
                            or not source_gray.flags.c_contiguous):
                        raise ValueError("original source frames must be contiguous uint8 640x400")
                    slot.capture_source_gray = source_gray
                if self.capture_callback is not None or self.raw_capture_callback is not None:
                    if slot.capture_gray is None:
                        raise RuntimeError("capture-enabled slot has no Gray8 snapshot storage")
                    np.copyto(slot.capture_gray, gray, casting="no")
                    if frame_input is None:
                        slot.capture_metadata = {
                            "source_sequence": frame_id,
                            "capture_monotonic_ns": time.monotonic_ns(),
                            "capture_wall_time_ns": time.time_ns(),
                            "camera_read_ms": None,
                            "capture_timestamp_kind": "host time at source handoff",
                        }
                    else:
                        slot.capture_metadata = {
                            "source_sequence": frame_input.source_sequence,
                            "capture_monotonic_ns": frame_input.capture_monotonic_ns,
                            "capture_wall_time_ns": frame_input.capture_wall_time_ns,
                            "camera_read_ms": frame_input.camera_read_ms,
                            "capture_timestamp_kind": "host time after VideoCapture.read",
                        }
                if self.preview_callback is not None:
                    preview_gray = (slot.capture_gray if slot.capture_gray is not None
                                    else slot.preview_gray)
                    if preview_gray is None:
                        raise RuntimeError("preview callback has no per-slot Gray8 snapshot")
                    if preview_gray is not slot.capture_gray:
                        np.copyto(preview_gray, gray, casting="no")
                encode_started = time.perf_counter()
                packet_frame_id = self._packet_frame_id(
                    frame_id, bounded=self.history_limit is not None
                )
                if self.packet_encoder_into is not None:
                    self.packet_encoder_into(
                        gray, np.asarray(slot.tx_buffer), frame_id=packet_frame_id)
                else:
                    packet = self.packet_encoder(gray, frame_id=packet_frame_id)
                    if (not isinstance(packet, (bytes, bytearray, memoryview))
                            or len(packet) != self.tx_bytes):
                        raise ValueError(
                            f"packet encoder must return exactly {self.tx_bytes} bytes")
                    slot.tx_buffer[:] = np.frombuffer(packet, dtype=np.uint8)
                self._record_timing("encode", time.perf_counter() - encode_started)
                dma_started = time.perf_counter()
                if self.before_transfer is not None:
                    prepared = self.before_transfer(frame_id, slot)
                    if inspect.isawaitable(prepared):
                        await prepared

                self.dma_may_be_active = True
                slot.state = BufferState.DMA_OWNED
                rx_wait = transfer_async(self.recv_channel, slot.rx_buffer, RX_BYTES)
                self._pending_waits.append(rx_wait)
                try:
                    tx_wait = transfer_async(self.send_channel, slot.tx_buffer, self.tx_bytes)
                except BaseException:
                    self._hold_nonfree_slots()
                    raise
                self._pending_waits.append(tx_wait)
                try:
                    await asyncio.wait_for(
                        asyncio.gather(rx_wait, tx_wait),
                        timeout=self.transfer_timeout_s,
                    )
                    self.dma_may_be_active = False
                    slot.dma_complete_monotonic_ns = time.monotonic_ns()
                    self._record_timing("dma", time.perf_counter() - dma_started)
                except BaseException:
                    self._hold_nonfree_slots()
                    raise
                finally:
                    self._pending_waits = [task for task in self._pending_waits if not task.done()]
                if self.before_rx_copy is not None:
                    sync_started = time.perf_counter()
                    prepared = self.before_rx_copy(frame_id, slot)
                    if inspect.isawaitable(prepared):
                        await prepared
                    self._record_timing("rx_sync", time.perf_counter() - sync_started)
                if slot.cached_mask is None:
                    raise RuntimeError("DMA slot has no cached CPU mask")
                copy_started = time.perf_counter()
                rx_view = np.asarray(slot.rx_buffer)
                np.copyto(slot.cached_mask.reshape(-1), rx_view, casting="no")
                if self.capture_callback is not None or self.raw_capture_callback is not None:
                    if slot.capture_mask is None:
                        raise RuntimeError("capture-enabled slot has no raw mask snapshot storage")
                    np.copyto(slot.capture_mask, slot.cached_mask, casting="no")
                self._record_timing("rx_copy", time.perf_counter() - copy_started)
                postprocess_ms = 0.0
                if self.after_transfer is not None:
                    postprocess_started = time.perf_counter()
                    validated = self.after_transfer(frame_id, slot)
                    if inspect.isawaitable(validated):
                        await validated
                    packer_check_seconds = time.perf_counter() - postprocess_started
                    postprocess_ms += packer_check_seconds * 1000.0
                    self._record_timing("packer_check", packer_check_seconds)
                if self.raw_capture_callback is not None:
                    if slot.capture_gray is None or slot.capture_mask is None:
                        raise RuntimeError("raw capture callback has no Gray8/Binary8 slot snapshots")
                    raw_copy_started = time.perf_counter()
                    self.raw_capture_callback(frame_id, slot.capture_gray, slot.capture_mask)
                    self._record_timing("raw_capture_copy", time.perf_counter() - raw_copy_started)
                if self.source_capture_callback is not None and slot.capture_source_gray is not None:
                    self.source_capture_callback(frame_id, slot.capture_source_gray)
                self._postprocess_ms[frame_id] = postprocess_ms

                if consumer.done():
                    consumer.result()
                slot.state = BufferState.CPU_READY
                await self._ready_slots.put(slot)
                frame_id += 1

            await self._ready_slots.put(None)
            await consumer
            return tuple(self._results)
        except BaseException as exc:
            primary_error = exc
            if pending_source_task is not None:
                source_cleanup_error = await self._cancel_frame_read(pending_source_task)
                pending_source_task = None
                if source_cleanup_error is not None and source_cleanup_error is not exc:
                    _attach_cleanup_error(exc, source_cleanup_error)
            self._hold_nonfree_slots()
            if not consumer.done():
                consumer.cancel()
                try:
                    await consumer
                except asyncio.CancelledError:
                    pass
            raise
        finally:
            close_frames = getattr(frame_iterator, "aclose", None)
            if callable(close_frames):
                try:
                    await close_frames()
                except BaseException as close_error:
                    if primary_error is None:
                        raise
                    _attach_cleanup_error(primary_error, close_error)

    async def _cancel_frame_read(self, task: asyncio.Task) -> BaseException | None:
        if not task.done():
            task.cancel()
            done, _ = await asyncio.wait((task,), timeout=self.transfer_timeout_s)
            if not done:
                task.add_done_callback(_consume_task_exception)
                return TimeoutError("frame source did not stop after cancellation")
        try:
            task.result()
        except asyncio.CancelledError as exc:
            cleanup_notes = tuple(getattr(exc, "__notes__", ()))
            if cleanup_notes:
                cleanup_error = RuntimeError("frame source cancellation cleanup failed")
                if hasattr(cleanup_error, "add_note"):
                    for note in cleanup_notes:
                        cleanup_error.add_note(str(note))
                else:
                    cleanup_error.__notes__ = list(cleanup_notes)
                return cleanup_error
            return None
        except StopAsyncIteration:
            return None
        except BaseException as exc:
            return exc
        return None

    async def _get_free_slot_or_consumer_failure(
        self, consumer: asyncio.Task
    ) -> DmaBufferSlot:
        wait_started = time.perf_counter()
        try:
            slot_waiter = asyncio.create_task(self._free_slots.get())
            done, _ = await asyncio.wait(
                (slot_waiter, consumer), return_when=asyncio.FIRST_COMPLETED
            )
            if consumer in done:
                if slot_waiter.done():
                    self._free_slots.put_nowait(slot_waiter.result())
                else:
                    slot_waiter.cancel()
                    await asyncio.gather(slot_waiter, return_exceptions=True)
                consumer.result()
            return slot_waiter.result()
        finally:
            self._record_timing("free_slot_wait", time.perf_counter() - wait_started)

    async def _consume_ready_slots(self) -> None:
        while True:
            slot = await self._ready_slots.get()
            if slot is None:
                return
            if slot.state is not BufferState.CPU_READY:
                raise RuntimeError("only completed DMA buffers may enter CPU processing")
            slot.state = BufferState.CPU_OWNED
            if slot.frame_id is None:
                raise RuntimeError("ready slot has no frame_id")
            mask = slot.cached_mask
            if mask is None:
                raise RuntimeError("completed DMA slot has no cached CPU mask")
            frame_id = slot.frame_id
            postprocess_started = time.perf_counter()
            roi_started = time.perf_counter()

            def update_counter_in_worker():
                worker_started = time.perf_counter()
                cpu_started = time.thread_time()
                if self.counter.motion_timebase_s is None:
                    result = self.counter.update(slot.frame_id, mask)
                else:
                    result = self.counter.update(
                        slot.frame_id, mask,
                        capture_monotonic_ns=slot.capture_monotonic_ns)
                return result, time.perf_counter() - worker_started, time.thread_time() - cpu_started

            result, worker_wall_seconds, worker_cpu_seconds = await asyncio.to_thread(
                update_counter_in_worker)
            roi_seconds = time.perf_counter() - roi_started
            self._record_timing("roi_count", roi_seconds)
            self._record_timing("roi_worker_wall", worker_wall_seconds)
            self._record_timing("roi_worker_cpu", worker_cpu_seconds)
            self._record_timing("roi_dispatch_wait",
                                max(roi_seconds - worker_wall_seconds, 0.0))
            if self.on_frame is not None:
                if inspect.iscoroutinefunction(self.on_frame):
                    await self.on_frame(result, mask)
                else:
                    callback_result = await asyncio.to_thread(self.on_frame, result, mask)
                    if inspect.isawaitable(callback_result):
                        await callback_result
            if self.preview_callback is not None:
                preview_gray = (slot.capture_gray if slot.capture_gray is not None
                                else slot.preview_gray)
                if preview_gray is None:
                    raise RuntimeError("completed DMA slot has no preview Gray8 snapshot")
                if inspect.iscoroutinefunction(self.preview_callback):
                    await self.preview_callback(result, mask, preview_gray)
                else:
                    callback_result = await asyncio.to_thread(
                        self.preview_callback, result, mask, preview_gray
                    )
                    if inspect.isawaitable(callback_result):
                        await callback_result
            if self.capture_callback is not None:
                if (slot.capture_gray is None or slot.capture_mask is None
                        or slot.capture_metadata is None):
                    raise RuntimeError("capture-enabled slot is missing its raw snapshots")
                if inspect.iscoroutinefunction(self.capture_callback):
                    await self.capture_callback(result, slot)
                else:
                    capture_result = await asyncio.to_thread(self.capture_callback, result, slot)
                    if inspect.isawaitable(capture_result):
                        await capture_result
            postprocess_ms = self._postprocess_ms.pop(frame_id, 0.0)
            postprocess_ms += (time.perf_counter() - postprocess_started) * 1000.0
            self._record_timing("postprocess", postprocess_ms / 1000.0)
            self._results.append(result)
            self._last_result = result
            self._frames_processed += 1
            slot.state = BufferState.FREE
            slot.frame_id = None
            slot.capture_metadata = None
            slot.capture_source_gray = None
            slot.dma_complete_monotonic_ns = None
            self._free_slots.put_nowait(slot)

    def _record_timing(self, stage: str, elapsed_seconds: float) -> None:
        if self.stage_timings_ms is not None:
            samples = self.stage_timings_ms.get(stage)
            if samples is None:
                samples = ([] if self.history_limit is None else
                           deque(maxlen=self.history_limit))
                self.stage_timings_ms[stage] = samples
            samples.append(elapsed_seconds * 1000.0)

    def _hold_nonfree_slots(self) -> None:
        held = []
        for slot in self.slots:
            if slot.state is not BufferState.FREE:
                slot.state = BufferState.HELD
                held.append(slot)
        self.held_slots = tuple(held)


def _attach_cleanup_error(primary_error: BaseException, cleanup_error: BaseException) -> None:
    message = f"frame source cleanup failed: {cleanup_error}"
    if hasattr(primary_error, "add_note"):
        primary_error.add_note(message)
        return
    errors = getattr(primary_error, "cleanup_errors", ())
    try:
        primary_error.cleanup_errors = tuple(errors) + (cleanup_error,)
    except Exception:
        pass


def _consume_task_exception(task: asyncio.Task) -> None:
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


def _integer(value: Any, name: str, *, minimum: int) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    result = int(value)
    if result < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return result


def _positive_float(value: Any, name: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be finite and positive")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite and positive") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def _nonnegative_float(value: Any, name: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be finite and nonnegative")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite and nonnegative") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return result
