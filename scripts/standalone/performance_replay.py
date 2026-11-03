"""Replay compact crossing evidence through a cold CPU-only field-v1 counter."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import time

import numpy as np

from scripts.rle.protocol import RleFrame
from scripts.standalone.count_controls import make_runtime_counter
from scripts.standalone.render_crossing_snapshot import _read_artifact


_SOURCE_FILES = (
    'scripts/standalone/performance_replay.py',
    'scripts/standalone/render_crossing_snapshot.py',
    'scripts/standalone/count_controls.py',
    'scripts/rle/counter.py',
    'scripts/rle/components.py',
    'scripts/rle/protocol.py',
    'scripts/rle/split_work.py',
    'scripts/wo0016/stream_counter.py',
    'scripts/wo0016/split_touching.py',
    'scripts/wo0016/motion_association.py',
    'scripts/wo0016/binary_stats.py',
    'scripts/wo0016/packet_encoder.py',
)
_OPTIONAL_SOURCE_FILES = ('scripts/rle/native/rle_components.cpp',)


def _source_identity():
    root = Path(__file__).resolve().parents[2]
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
              for name in _SOURCE_FILES}
    canonical = json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode('utf-8')
    optional = {}
    for name in _OPTIONAL_SOURCE_FILES:
        path = root / name
        optional[name] = dict(included=path.is_file(),
                              sha256=hashlib.sha256(path.read_bytes()).hexdigest()
                              if path.is_file() else None)
    return dict(sha256_by_path=hashes, digest=hashlib.sha256(canonical).hexdigest(),
                optional_sources=optional)


def _native_library_identity(library):
    candidate = library if library is not None else os.environ.get('RLE_NATIVE_LIB')
    candidate = getattr(candidate, '_name', candidate)
    if candidate is None:
        return dict(path=None, sha256=None)
    if not isinstance(candidate, (str, os.PathLike)):
        raise ValueError('native library path is required to record its SHA-256')
    path = Path(candidate).expanduser().resolve()
    if not path.is_file():
        raise ValueError('native library file is unavailable for SHA-256: {}'.format(path))
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def _load_packet(event_path, record, metadata):
    packet_metadata = dict(metadata['packet'])
    packet_metadata['roi'] = tuple(packet_metadata['roi'])
    prefix = 'frame-{:010d}'.format(metadata['frame_id'])
    blob = _read_artifact(event_path, record, prefix + '.rle.npz')
    with np.load(io.BytesIO(blob), allow_pickle=False) as data:
        packet = RleFrame(**packet_metadata,
                          runs=data['runs'].copy(),
                          row_projection=data['row_projection'].copy(),
                          column_projection=data['column_projection'].copy())
    if not packet.valid:
        raise ValueError('cannot replay invalid RLE packet: frame {}'.format(metadata['frame_id']))
    return packet


def _semantic_summary(result):
    return dict(detections=[asdict(detection) for detection in result.detections],
                events=[asdict(event) for event in result.events],
                suppressed_events=[asdict(event) for event in result.suppressed_events],
                tracks=result.tracks, total_count=result.total_count)


def _stable_digest(value):
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def _json_value(value):
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _timing_summary(values):
    samples = [float(value) for value in values if value is not None]
    if not samples:
        return dict(count=0, p50=None, p95=None, max=None)
    return dict(count=len(samples), p50=float(np.percentile(samples, 50)),
                p95=float(np.percentile(samples, 95)), max=max(samples))


def _summarize_repeat(frames):
    metrics = ('split_ms', 'analyze_ms', 'region_ms', 'track_ms', 'wall_ms',
               'capture_interval_ms')
    return {name: _timing_summary([frame[name] for frame in frames]) for name in metrics}


def _validate_truth(raw):
    def reject_constant(value):
        raise ValueError('truth contains invalid non-finite number {}'.format(value))

    try:
        data = json.loads(raw, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError('truth JSON is invalid: {}'.format(error)) from error
    if (not isinstance(data, dict)
            or data.get('schema') != 'material-counter.truth.v1'
            or data.get('matching_axis') != 'x'):
        raise ValueError('truth must declare schema material-counter.truth.v1 and matching_axis x')
    objects = data.get('objects')
    if not isinstance(objects, list):
        raise ValueError('truth objects must be a list')
    seen = set()
    normalized = []
    for index, item in enumerate(objects):
        prefix = 'truth object {}'.format(index)
        if not isinstance(item, dict):
            raise ValueError('{} must be an object'.format(prefix))
        object_id = item.get('object_id')
        if (not isinstance(object_id, (str, int)) or isinstance(object_id, bool)
                or (isinstance(object_id, str) and not object_id.strip())):
            raise ValueError('{} object_id must be a nonempty string or integer'.format(prefix))
        identity = (type(object_id), object_id)
        if identity in seen:
            raise ValueError('truth object_id values must be unique')
        seen.add(identity)
        low, high = item.get('frame_id_min'), item.get('frame_id_max')
        if (type(low) is not int or type(high) is not int or low < 0 or high < low):
            raise ValueError('{} frame interval must be nonnegative integers with max >= min'.format(prefix))
        y, tolerance = item.get('centroid_y'), item.get('lateral_tolerance_px')
        try:
            y_number, tolerance_number = float(y), float(tolerance)
        except (TypeError, ValueError, OverflowError):
            y_number = tolerance_number = math.nan
        if (isinstance(y, bool) or not isinstance(y, (int, float)) or not math.isfinite(y_number)
                or isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
                or not math.isfinite(tolerance_number) or tolerance_number < 0):
            raise ValueError('{} centroid_y and lateral_tolerance_px must be finite; tolerance must be nonnegative'.format(prefix))
        normalized.append(dict(object_id=object_id, frame_id_min=low, frame_id_max=high,
                               centroid_y=y_number, lateral_tolerance_px=tolerance_number))
    return normalized


def _match_truth(objects, count_events):
    assigned = {item['object_id']: [] for item in objects}
    unassigned, ambiguous, ambiguous_object_ids = [], [], set()
    for frame_id, event_index, centroid_y in count_events:
        candidates = [item for item in objects
                      if item['frame_id_min'] <= frame_id <= item['frame_id_max']
                      and abs(centroid_y-item['centroid_y']) <= item['lateral_tolerance_px']]
        reference = dict(frame_id=frame_id, event_index=event_index)
        if not candidates:
            unassigned.append(reference)
        elif len(candidates) > 1:
            ambiguous.append(dict(**reference,
                                  candidate_object_ids=[item['object_id'] for item in candidates]))
            ambiguous_object_ids.update(item['object_id'] for item in candidates)
        else:
            assigned[candidates[0]['object_id']].append(reference)
    duplicate_events, unique_matches = [], []
    for object_id, events in assigned.items():
        if len(events) > 1:
            duplicate_events.append(dict(object_id=object_id, events=len(events),
                                         event_refs=events))
        elif len(events) == 1 and object_id not in ambiguous_object_ids:
            unique_matches.append(dict(object_id=object_id, **events[0]))
    missed = [item['object_id'] for item in objects
              if not assigned[item['object_id']] and item['object_id'] not in ambiguous_object_ids]
    return dict(status='MEASURED', matching_axis='x', unique_matches=unique_matches,
                duplicate_events=duplicate_events, missed_objects=missed,
                unassigned_events=unassigned, ambiguous_events=ambiguous,
                ambiguous_objects=[item['object_id'] for item in objects
                                   if item['object_id'] in ambiguous_object_ids],
                claims_material_accuracy=False,
                scope='Truth is evaluated independently per event window; no full-video deduplication is performed.')


def _replay_event(event_path, record, event_digest, native_lib, repeats, truth_objects=None):
    frames = record.get('frames')
    if not isinstance(frames, list) or not frames:
        raise ValueError('compact event must contain ordered frame metadata')
    for previous, current in zip(frames, frames[1:]):
        if current['frame_id'] <= previous['frame_id']:
            raise ValueError('event frame metadata must be strictly increasing')
    packets = [_load_packet(event_path, record, metadata) for metadata in frames]
    repeat_records = []
    truth_events = []
    for repeat in range(1, repeats + 1):
        counter = make_runtime_counter(native_lib, profile='field-v1')
        semantic_frames, measured_frames = [], []
        previous_capture_ns = None
        for metadata, packet in zip(frames, packets):
            started = time.perf_counter()
            result = counter.update_rle(
                packet, capture_monotonic_ns=metadata.get('capture_monotonic_ns'))
            wall_ms = (time.perf_counter() - started) * 1000
            if result is None:
                raise ValueError('counter rejected RLE packet for frame {}'.format(metadata['frame_id']))
            timings = result.roi_subtimings_ms
            capture_ns = metadata.get('capture_monotonic_ns')
            interval_ms = (None if capture_ns is None or previous_capture_ns is None else
                           (capture_ns-previous_capture_ns)/1e6)
            if repeat == 1:
                truth_events.extend((event.frame_id, event_index, event.centroid_y)
                                    for event_index, event in enumerate(result.events))
            semantic_frames.append(dict(frame_id=metadata['frame_id'],
                                        **_semantic_summary(result)))
            measured_frames.append(dict(
                frame_id=metadata['frame_id'], split_ms=timings.get('split_ms', 0.0),
                analyze_ms=timings.get('analyze_ms', 0.0),
                region_ms=timings.get('rle_regions_ms', 0.0),
                track_ms=timings.get('track_ms', 0.0), wall_ms=wall_ms,
                capture_monotonic_ns=capture_ns, capture_interval_ms=interval_ms,
                split_stats=_json_value(copy.deepcopy(getattr(counter, 'last_split_stats', {}))),
                split_diagnostics=_json_value(copy.deepcopy(getattr(counter, 'last_split_diagnostics', ()))),
                cluster_parent_diagnostics=_json_value(copy.deepcopy(
                    getattr(counter, 'last_cluster_parent_diagnostics', ()))),
                snapshot_cluster_parent_diagnostics=_json_value(
                    metadata.get('cluster_parent_diagnostics', ())),
                detection_count=len(result.detections), runs=packet.run_count))
            previous_capture_ns = capture_ns
        repeat_records.append(dict(repeat=repeat, frames=measured_frames,
                                   semantic_summary=semantic_frames,
                                   semantic_sha256=_stable_digest(semantic_frames),
                                   summary=_summarize_repeat(measured_frames)))
    digests = {item['semantic_sha256'] for item in repeat_records}
    artifacts = record.get('artifacts')
    if not isinstance(artifacts, dict):
        raise ValueError('compact event is missing artifact summaries')
    truth = (dict(status='NOT_MEASURED', matching_axis='x', claims_material_accuracy=False,
                  scope='No truth file supplied; cold event-window replay only.')
             if truth_objects is None else _match_truth(truth_objects, truth_events))
    truth['evaluated_repeat'] = 1
    return dict(event_path=str(event_path), event_sha256=event_digest,
                artifacts={name: dict(summary) for name, summary in artifacts.items()},
                frame_count=len(frames), deterministic=len(digests) == 1,
                repeats=repeat_records, truth=truth)


def replay_events(event_paths, *, native_lib=None, repeats=3, truth_path=None):
    if type(repeats) is not int or repeats < 1:
        raise ValueError('repeats must be a positive integer')
    truth_objects = None
    truth_identity = None
    if truth_path is not None:
        truth_file = Path(truth_path).resolve()
        truth_raw = truth_file.read_bytes()
        truth_objects = _validate_truth(truth_raw)
        truth_identity = dict(truth_path=str(truth_file),
                              truth_sha256=hashlib.sha256(truth_raw).hexdigest())
    inputs = []
    for path in event_paths:
        event_path = Path(path).resolve()
        raw = event_path.read_bytes()
        record = json.loads(raw)
        if (record.get('schema') not in ('material-counter.crossing-snapshot.v1',
                                         'material-counter.crossing-snapshot.v2')
                or record.get('storage_mode') != 'compact'):
            raise ValueError('replay requires a compact crossing event.json with schema v1 or v2')
        group = _replay_event(event_path, record, hashlib.sha256(raw).hexdigest(),
                              native_lib, repeats, truth_objects)
        if truth_identity is not None:
            group['truth'].update(truth_identity)
        inputs.append(group)
    return dict(schema='material-counter.cpu-performance-replay.v1',
                execution=dict(mode='cpu-only', window_is_complete_video=False,
                               claims_material_accuracy=False,
                               note='Cold-start replay of captured event windows; not full-video or material-accuracy evidence.',
                               cold_window_boundary='Every event window starts with a fresh counter; truth is matched independently within each event window, with no full-video deduplication.',
                               determinism_scope='Repeated runs compare fresh counters on the same captured window; this does not prove matching the historical initial track state or original live-session trajectories.'),
                application_source=_source_identity(),
                native_library=_native_library_identity(native_lib), inputs=inputs)


def _positive_integer(value):
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError('must be a positive integer') from error
    if parsed < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event', required=True, action='append', type=Path,
                        help='compact event.json; may be supplied multiple times')
    parser.add_argument('--native-lib', type=Path, help='optional native RLE component library')
    parser.add_argument('--truth', type=Path, help='optional material-counter.truth.v1 JSON file')
    parser.add_argument('--output', required=True, type=Path, help='JSON report path')
    parser.add_argument('--repeats', type=_positive_integer, default=3)
    args = parser.parse_args(argv)
    report = replay_events(args.event,
                           native_lib=str(args.native_lib) if args.native_lib else None,
                           repeats=args.repeats, truth_path=args.truth)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
