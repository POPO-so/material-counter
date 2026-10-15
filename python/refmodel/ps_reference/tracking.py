"""Deterministic, observation-only counting reference; no device or GT access."""
from dataclasses import dataclass
import math

from .regions import Region


@dataclass(frozen=True)
class TrackerConfig:
    line_x: float = 160.0
    hysteresis: float = 2.0
    direction: int = 1
    confirm_hits: int = 2
    max_missing: int = 2
    max_step: float = 6.0
    max_y: float = 8.0
    backward_tolerance: float = 2.0

    def __post_init__(self):
        if self.direction not in (-1, 1):
            raise ValueError('direction must be +1 or -1')
        for value in (self.line_x, self.hysteresis, self.max_step, self.max_y, self.backward_tolerance):
            if not math.isfinite(value):
                raise ValueError('parameters must be finite')
        if self.hysteresis <= 0 or self.max_step <= 0 or self.max_y <= 0 or self.backward_tolerance < 0:
            raise ValueError('invalid distance/velocity bounds')
        if type(self.confirm_hits) is not int or self.confirm_hits < 2:
            raise ValueError('confirmation requires at least two consecutive observations')
        if type(self.max_missing) is not int or self.max_missing < 0:
            raise ValueError('max_missing must be nonnegative integer')


@dataclass(frozen=True)
class CountEvent:
    track_id: int
    frame_id: int
    centroid_x: float
    centroid_y: float


@dataclass
class Track:
    track_id: int
    region: Region
    last_frame: int
    hits: int = 1
    streak: int = 1
    vx: float = 0.0
    confirmed: bool = False
    armed: bool = False
    counted: bool = False
    born_counted: bool = False
    state: str = 'tentative'


class Tracker:
    def __init__(self, config=None):
        self.config = config or TrackerConfig()
        self.tracks = {}
        self.next_id = 1
        self.last_frame_id = None
        self.count = 0
        self.last_assignments = []
        self.duplicate_detections = 0
        self.merge_observations = 0

    def _side(self, region):
        return self.config.direction * (region.centroid_x - self.config.line_x)

    def _observe(self, track, frame_id, region):
        dt = frame_id - track.last_frame
        motion = self.config.direction * (region.centroid_x - track.region.centroid_x) / dt
        track.vx = self.config.direction * min(self.config.max_step, max(0.0, motion))
        track.streak = track.streak + 1 if dt == 1 else 1
        track.hits += 1
        track.confirmed |= track.streak >= self.config.confirm_hits
        track.region = region
        track.last_frame = frame_id
        track.state = 'confirmed' if track.confirmed else 'tentative'
        track.armed |= self._side(region) < -self.config.hysteresis
        # Prediction is used only for association. Events require a real feature.
        if track.confirmed and track.armed and not track.counted and self._side(region) > self.config.hysteresis:
            track.counted = True
            self.count += 1
            return CountEvent(track.track_id, frame_id, region.centroid_x, region.centroid_y)
        return None

    def update(self, frame_id, detections):
        if type(frame_id) is not int or frame_id < 0:
            raise ValueError('frame_id must be nonnegative integer')
        if self.last_frame_id is not None and frame_id <= self.last_frame_id:
            raise ValueError('frame IDs must strictly increase; duplicate/reordered frames are rejected')
        detections = list(detections)
        for region in detections:
            if not isinstance(region, Region):
                raise TypeError('tracker accepts Region instances only')
            if (not math.isfinite(region.centroid_x) or not math.isfinite(region.centroid_y)
                    or region.area <= 0 or region.x_min > region.x_max or region.row_min > region.row_max):
                raise ValueError('invalid region')

        candidates = []
        seen = set()
        for index, region in enumerate(detections):
            key = (region.bbox, region.centroid_x, region.centroid_y, region.area, region.peak, region.flags)
            if key in seen:
                self.duplicate_detections += 1
                continue
            seen.add(key)
            if region.flags & 1:
                self.merge_observations += 1
                continue
            candidates.append((index, region))
        self.last_frame_id = frame_id
        self.last_assignments = []
        eligible = []
        for track in self.tracks.values():
            if track.state == 'finished':
                continue
            if frame_id - track.last_frame > self.config.max_missing + 1:
                track.state = 'finished'
            else:
                eligible.append(track)

        edges = []
        for track in eligible:
            dt = frame_id - track.last_frame
            prediction = track.region.centroid_x + track.vx * dt
            for index, region in candidates:
                dx = abs(region.centroid_x - prediction)
                dy = abs(region.centroid_y - track.region.centroid_y)
                travel = self.config.direction * (region.centroid_x - track.region.centroid_x)
                ratio = region.area / track.region.area
                if (dx <= self.config.max_step * dt and dy <= self.config.max_y
                        and travel >= -self.config.backward_tolerance and 0.5 <= ratio <= 2.0):
                    edges.append((dx * dx + dy * dy, track.track_id, region.centroid_x,
                                  region.centroid_y, index))
        used_tracks, used_detections = set(), set()
        events = []
        for _, track_id, _, _, index in sorted(edges):
            if track_id in used_tracks or index in used_detections:
                continue
            used_tracks.add(track_id)
            used_detections.add(index)
            self.last_assignments.append((track_id, index))
            event = self._observe(self.tracks[track_id], frame_id, detections[index])
            if event is not None:
                events.append(event)
        for track in eligible:
            if track.track_id not in used_tracks:
                track.state = 'finished' if frame_id - track.last_frame > self.config.max_missing else 'lost'
                track.streak = 0
        # New IDs are assigned by geometry, not incoming order or external identity.
        for index, region in sorted(candidates, key=lambda item: (item[1].centroid_x, item[1].centroid_y,
                                                                 item[1].bbox, item[1].area)):
            if index in used_detections:
                continue
            born_counted = self._side(region) >= 0.0
            track = Track(self.next_id, region, frame_id, armed=self._side(region) < -self.config.hysteresis,
                          counted=born_counted, born_counted=born_counted)
            self.tracks[track.track_id] = track
            self.last_assignments.append((track.track_id, index))
            self.next_id += 1
        self.last_assignments.sort()
        return sorted(events, key=lambda event: event.track_id)
