"""Replay compact crossing evidence through a cold CPU-only field-v1 counter."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import io
import json
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


def _replay_event(event_path, record, event_digest, native_lib, repeats):
    frames = record.get('frames')
    if not isinstance(frames, list) or not frames:
        raise ValueError('compact event must contain ordered frame metadata')
    for previous, current in zip(frames, frames[1:]):
        if current['frame_id'] <= previous['frame_id']:
            raise ValueError('event frame metadata must be strictly increasing')
    packets = [_load_packet(event_path, record, metadata) for metadata in frames]
    repeat_records = []
    for repeat in range(1, repeats + 1):
        counter = make_runtime_counter(native_lib, profile='field-v1')
        semantic_frames, measured_frames = [], []
        for metadata, packet in zip(frames, packets):
            started = time.perf_counter()
            result = counter.update_rle(
                packet, capture_monotonic_ns=metadata.get('capture_monotonic_ns'))
            wall_ms = (time.perf_counter() - started) * 1000
            if result is None:
                raise ValueError('counter rejected RLE packet for frame {}'.format(metadata['frame_id']))
            semantic_frames.append(dict(frame_id=metadata['frame_id'],
                                        **_semantic_summary(result)))
            measured_frames.append(dict(
                frame_id=metadata['frame_id'], wall_ms=wall_ms,
                region_ms=result.roi_subtimings_ms.get('rle_regions_ms', 0.0),
                track_ms=result.roi_subtimings_ms.get('track_ms', 0.0),
                detection_count=len(result.detections), runs=packet.run_count))
        repeat_records.append(dict(repeat=repeat, frames=measured_frames,
                                   semantic_summary=semantic_frames,
                                   semantic_sha256=_stable_digest(semantic_frames)))
    digests = {item['semantic_sha256'] for item in repeat_records}
    artifacts = record.get('artifacts')
    if not isinstance(artifacts, dict):
        raise ValueError('compact event is missing artifact summaries')
    return dict(event_path=str(event_path), event_sha256=event_digest,
                artifacts={name: dict(summary) for name, summary in artifacts.items()},
                frame_count=len(frames), deterministic=len(digests) == 1,
                repeats=repeat_records)


def replay_events(event_paths, *, native_lib=None, repeats=3):
    if type(repeats) is not int or repeats < 1:
        raise ValueError('repeats must be a positive integer')
    inputs = []
    for path in event_paths:
        event_path = Path(path).resolve()
        raw = event_path.read_bytes()
        record = json.loads(raw)
        if (record.get('schema') != 'material-counter.crossing-snapshot.v1'
                or record.get('storage_mode') != 'compact'):
            raise ValueError('replay requires a compact crossing event.json')
        group = _replay_event(event_path, record, hashlib.sha256(raw).hexdigest(),
                              native_lib, repeats)
        inputs.append(group)
    return dict(schema='material-counter.cpu-performance-replay.v1',
                execution=dict(mode='cpu-only', window_is_complete_video=False,
                               claims_material_accuracy=False,
                               note='Cold-start replay of captured event windows; not full-video or material-accuracy evidence.',
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
    parser.add_argument('--output', required=True, type=Path, help='JSON report path')
    parser.add_argument('--repeats', type=_positive_integer, default=3)
    args = parser.parse_args(argv)
    report = replay_events(args.event,
                           native_lib=str(args.native_lib) if args.native_lib else None,
                           repeats=args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
