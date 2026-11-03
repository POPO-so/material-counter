"""RLE regions and candidate-only masks feeding the existing PS tracker."""
from __future__ import annotations
from pathlib import Path
import sys
import time
import math
import numpy as np

_LEGACY = Path(__file__).resolve().parents[1] / "wo0016"
if str(_LEGACY) not in sys.path:
    sys.path.insert(0, str(_LEGACY))
from scripts.wo0016.stream_counter import MaskCounter, Detection, FrameResult
from scripts.wo0016.split_touching import (
    _MAX_CHILD_SEEDS,
    _MAX_PARENT_BBOX_PIXELS,
    _MAX_PARENT_FOREGROUND_PIXELS,
    enclosed_hole_centers,
    split_touching_component,
    is_single_connected_component,
)
from .components import analyze_runs, local_mask
from .protocol import RleFrame
from .components import Component
from .split_work import SplitBudget, SplitRetries, positive_limit, run_signature


class RleCounter(MaskCounter):
    """Perform no frame rasterization in the normal region/tracking path."""

    def __init__(self, *, library, split_budget_ms=None, split_max_candidates=None,
                 split_max_pixels=None, split_retry_interval_s=0.0, **settings):
        defaults = dict(width=640, height=400, line_y=200, line_x=320,
            roi_mode="full", min_area=48, max_area=3600,
            max_bbox_width=170, max_bbox_height=170, max_step=48,
            max_x_step=40, hysteresis=4, component_cluster_dx=0)
        defaults.update(settings)
        super().__init__(**defaults)
        self.library = library
        self.last_seen_frame_id = None
        self.invalid_frames = 0
        self.last_backend = "native"
        self.split_budget_ms = positive_limit(split_budget_ms, 'split_budget_ms')
        self.split_max_candidates = positive_limit(split_max_candidates, 'split_max_candidates', integer=True)
        self.split_max_pixels = positive_limit(split_max_pixels, 'split_max_pixels', integer=True)
        self.split_retry_interval_s = positive_limit(split_retry_interval_s, 'split_retry_interval_s', allow_zero=True)
        if self.split_retry_interval_s is None:
            raise ValueError('split_retry_interval_s must be a nonnegative number')
        self._split_retries = SplitRetries(self.split_retry_interval_s)
        self.last_split_stats = {}
        self.last_split_diagnostics = []

    def clear_pending_cluster_parents(self):
        super().clear_pending_cluster_parents()
        self._split_retries.entries.clear()

    def _split_local(self, frame, analysis, index, component):
        """Return checked children and reliable visible-hole parent evidence."""
        mask = local_mask(frame.runs, analysis.labels, index) != 0
        pieces = split_touching_component(mask,
            min_child_area=self.min_split_child_area, min_hole_area=self.split_min_hole_area,
            recursive_item_area=self.split_recursive_item_area,
            min_hole_distance=self.split_min_hole_distance, hole_area_cap=self.split_hole_area_cap)
        partitions = [np.asarray(piece) != 0 for piece in pieces]
        valid = (2 <= len(partitions) <= _MAX_CHILD_SEEDS
            and all(piece.shape == mask.shape and is_single_connected_component(piece)
                    for piece in partitions))
        children = []
        if valid:
            coverage = np.zeros(mask.shape, dtype=np.uint8)
            for piece in partitions:
                ys, xs = np.nonzero(piece)
                if (len(xs) < self.min_split_child_area or self._component_exceeds_limits(
                        len(xs), int(xs.max()-xs.min()+1), int(ys.max()-ys.min()+1))):
                    valid = False
                    break
                coverage += piece
                children.append(Detection(component.x_min+int(xs.min()), component.x_min+int(xs.max()),
                    component.y_min+int(ys.min()), component.y_min+int(ys.max()), len(xs),
                    component.x_min+float(xs.mean()), component.y_min+float(ys.mean())))
            valid = valid and np.array_equal(coverage > 0, mask) and not np.any(coverage > 1)
        if valid:
            return tuple(children), 0
        if self.cluster_parent_recovery:
            holes = enclosed_hole_centers(mask, min_hole_area=self.split_min_hole_area,
                                         hole_area_cap=self.split_hole_area_cap)
            if 2 <= len(holes) <= _MAX_CHILD_SEEDS:
                separation = np.linalg.norm(holes[:, None]-holes[None, :], axis=2)
                if not np.any(separation[np.triu_indices(len(holes), 1)] < self.split_min_hole_distance):
                    return (), int(len(holes))
        return (), 0

    def update_rle(self, frame: RleFrame, *, capture_monotonic_ns=None,
                   region_features=None) -> FrameResult | None:
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

        features_valid = (isinstance(region_features, tuple)
            and all(isinstance(c, Component) for c in region_features)
            and all(all(isinstance(v, (int, np.integer)) and not isinstance(v, (bool, np.bool_))
                        for v in (c.x_min, c.y_min, c.x_max, c.y_max, c.area))
                    and all(math.isfinite(v) for v in (c.centroid_x, c.centroid_y))
                    and c.area > 0 and 0 <= c.x_min <= c.x_max < self.width
                    and 0 <= c.y_min <= c.y_max < self.height
                    and c.area <= (c.x_max-c.x_min+1)*(c.y_max-c.y_min+1)
                    and c.x_min <= c.centroid_x <= c.x_max
                    and c.y_min <= c.centroid_y <= c.y_max for c in region_features)
            and sum(c.area for c in region_features) == frame.total_fg)
        def split_candidate(component):
            box_width = component.x_max - component.x_min + 1
            box_height = component.y_max - component.y_min + 1
            return (
                self.guarded_merge
                and self.single_intact_area < component.area <= self.max_split_area
                and component.area <= _MAX_PARENT_FOREGROUND_PIXELS
                and box_width * box_height <= _MAX_PARENT_BBOX_PIXELS
            )

        guarded_candidate = (features_valid and any(
            split_candidate(component) for component in region_features))
        use_features = features_valid and not guarded_candidate
        self.last_backend = ("pl_features" if use_features else
                             "native" if region_features is None or guarded_candidate else "fallback")

        started = time.perf_counter()
        timestamp = self._motion_timestamp(capture_monotonic_ns)
        self._split_origins = {}
        detections, rejected, cluster_parent_observations = [], [], []
        analyze_ms = split_ms = 0.0
        budget = SplitBudget(self.split_max_candidates, self.split_max_pixels, self.split_budget_ms)
        self.last_split_stats = dict(candidates=0, attempted=0, succeeded=0, deferred=0, cached=0, unresolved=0)
        self.last_split_diagnostics = []
        if frame.total_fg >= self.min_area:
            if use_features:
                components = region_features
                analysis = None
            else:
                analyze_started = time.perf_counter()
                analysis = analyze_runs(frame.runs, library=self.library, min_area=self.min_area)
                analyze_ms = (time.perf_counter()-analyze_started)*1000
                components = analysis.components
            candidates = [(index, component) for index, component in enumerate(components)
                          if component.area >= self.min_area and split_candidate(component)]
            now = (timestamp / 1e9 if timestamp is not None else
                   frame.frame_id * (self.motion_timebase_s or .1))
            states, priority = self._split_retries.prepare(candidates, now, axis=self.count_axis,
                line=self.line_x if self.count_axis == 'x' else self.line_y,
                max_step=self.max_step, lateral_step=self.max_lateral_step,
                timebase=self.motion_timebase_s or .1)
            self.last_split_stats['candidates'] = len(candidates)
            candidate_ids = set(priority)
            order = priority + [index for index in range(len(components)) if index not in candidate_ids]
            for index in order:
                component = components[index]
                if component.area < self.min_area:
                    continue
                detection = Detection(component.x_min, component.x_max, component.y_min,
                    component.y_max, component.area, component.centroid_x, component.centroid_y)
                if split_candidate(component):
                    if analysis is None:
                        raise AssertionError("guarded merge candidates must use native CCL")
                    entry = states[index]
                    signature = (run_signature(frame.runs, analysis.labels, index, component)
                                 if self.split_retry_interval_s else b'')
                    cached = self._split_retries.cached(entry, signature, now)
                    reason = None if cached else budget.admit(
                        (component.x_max-component.x_min+1)*(component.y_max-component.y_min+1))
                    if cached or reason:
                        self.last_split_stats['cached' if cached else 'deferred'] += 1
                        known = (self.split_retry_interval_s > 0 and entry.signature == signature
                                 and entry.outcome in ('parent', 'single', 'split'))
                        seeds = entry.seeds if known else 0
                        self.last_split_diagnostics.append(dict(component=index, x_min=component.x_min,
                            state='cached' if cached else 'deferred', reason=reason, seed_count=seeds))
                        if known and entry.outcome == 'single':
                            pass  # reuse only a currently identical, uniquely matched observation
                        elif known and entry.outcome == 'split':
                            # The full current run sequence, not merely area or bbox,
                            # has proved the same partition geometry. Cache only
                            # statistics; no old child mask is presented as an image.
                            dx, dy = component.x_min-entry.origin[0], component.y_min-entry.origin[1]
                            for old in entry.children:
                                child = Detection(old.x_min+dx, old.x_max+dx, old.y_min+dy, old.y_max+dy,
                                    old.area, old.centroid_x+dx, old.centroid_y+dy)
                                detections.append(child)
                                self._split_origins[child] = index
                            continue
                        else:
                            if seeds:
                                cluster_parent_observations.append((detection, seeds))
                            rejected.append(detection)
                            self.last_split_stats['unresolved'] += 1
                            continue
                    else:
                        split_started = time.perf_counter()
                        children, seeds = self._split_local(frame, analysis, index, component)
                        # Include mask creation, child validation and failed-parent evidence.
                        duration = (time.perf_counter()-split_started)*1000
                        split_ms += duration
                        budget.elapsed_ms += duration
                        self.last_split_stats['attempted'] += 1
                        self.last_split_diagnostics.append(dict(component=index, x_min=component.x_min,
                            state='attempted', elapsed_ms=duration, seed_count=seeds, children=len(children)))
                        entry.last_attempt_s, entry.signature = now, signature
                        entry.outcome = 'parent' if seeds else 'single' if not children else 'split'
                        entry.seeds = seeds
                        entry.children, entry.origin = children, (component.x_min, component.y_min)
                        if children:
                            self.last_split_stats['succeeded'] += 1
                            detections.extend(children)
                            self._split_origins.update((child, index) for child in children)
                            continue
                        if seeds:
                            cluster_parent_observations.append((detection, seeds))
                            rejected.append(detection)
                            self.last_split_stats['unresolved'] += 1
                            continue
                if self._component_exceeds_limits(
                        component.area, component.x_max-component.x_min+1,
                        component.y_max-component.y_min+1):
                    rejected.append(detection)
                    continue
                detections.append(detection)
        self._set_rejected_components(rejected)
        detections.sort(key=lambda d: (d.centroid_x, d.centroid_y, d.y_min, d.x_min))
        raw = tuple(detections)
        cluster_started = time.perf_counter()
        detections = self._cluster_components(detections)
        cluster_ms = (time.perf_counter()-cluster_started)*1000
        region_ms = (time.perf_counter()-started)*1000
        started = time.perf_counter()
        self.last_frame_id = frame.frame_id
        events = self._associate_and_update(
            frame.frame_id, detections, timestamp,
            cluster_parent_observations=tuple(cluster_parent_observations),
        )
        track_ms = (time.perf_counter()-started)*1000
        render_started = time.perf_counter()
        result = FrameResult(frame_id=frame.frame_id, total_count=self.total_count,
            detections=tuple(detections), events=tuple(events), roi_top=top, roi_bottom=bottom,
            row_projection=tuple(frame.row_projection[top:bottom].tolist()),
            column_projection=tuple(frame.column_projection.tolist()),
            roi_foreground_pixels=frame.total_fg, ccl_executed=frame.total_fg>=self.min_area,
            roi_subtimings_ms={"rle_regions_ms": region_ms, "track_ms": track_ms,
                               "pl_features_ms": region_ms if use_features else 0.0,
                               "analyze_ms": analyze_ms, "split_ms": split_ms,
                               "cluster_ms": cluster_ms, "associate_ms": track_ms},
            raw_detections=raw, suppressed_events=tuple(self.last_suppressed_events),
            tracks=self.tracking_snapshot(), glare_rejected_count=self.glare_rejected_count,
            glare_area_px=self.glare_area_px)
        result.roi_subtimings_ms["render_ms"] = (time.perf_counter()-render_started)*1000
        return result
