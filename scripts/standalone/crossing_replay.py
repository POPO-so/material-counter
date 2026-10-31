from __future__ import annotations
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from scripts.wo0016.stream_counter import Detection, MaskCounter, _Track

DEFAULT_FIXTURE = Path(__file__).parent / 'fixtures' / 'crossing-regressions-20261031.json'

def load_fixture(path: str | Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if data.get('schema_version') != 1 or not isinstance(data.get('windows'), list):
        raise ValueError('unsupported crossing replay fixture schema')
    return data

def _detection(data: dict[str, Any]) -> Detection:
    return Detection(**{key: data[key] for key in ('x_min','x_max','y_min','y_max','area','centroid_x','centroid_y')})

def replay_window(window: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    initial = window['initial']
    params = dict(initial['tracker_parameters'])
    params.update(overrides or {})
    counter = MaskCounter(**params)
    counter.total_count = initial['total_count']
    counter.last_frame_id = initial['frame_id']
    first_ts = initial['capture_monotonic_ns']
    tracks = initial['tracks']
    if counter.motion_timebase_s is not None:
        stale = [t['track_id'] for t in tracks if t['last_frame'] != initial['frame_id']]
        if stale:
            raise ValueError(f"initial observation timestamps unavailable for stale tracks: {stale}")
        counter._last_capture_monotonic_ns = first_ts
    for record in tracks:
        tid = record['track_id']
        d = _detection(record['detection'])
        track_ts = first_ts if counter.motion_timebase_s is not None else None
        # A one-frame snapshot cannot establish px/s velocity. Keep it unknown.
        counter._tracks[tid] = _Track(
            track_id=tid, detection=d, last_frame=record['last_frame'],
            streak=record['streak'], confirmed=record['confirmed'], armed=record['armed'],
            counted=record['counted'], missed=record['missed'], finished=record['finished'],
            forward_velocity=record.get('forward_velocity'),
            capture_monotonic_ns=track_ts, velocity_px_per_s=None)
    counter._next_track_id = max(counter._tracks, default=0) + 1
    events = []
    frames = []
    for record in window['frames']:
        result = counter.update_detections(
            record['frame_id'], [_detection(d) for d in record['detections']],
            capture_monotonic_ns=record['capture_monotonic_ns'])
        events.extend(asdict(event) for event in result.events)
        frames.append(dict(frame_id=result.frame_id,total_count=result.total_count,
                           events=[asdict(event) for event in result.events]))
    return {'name':window['name'], 'events':events, 'total_count':counter.total_count,
            'added_count':counter.total_count-initial['total_count'],
            'frames':frames,
            'track_ids':[track['track_id'] for track in counter.tracking_snapshot()]}

def main() -> int:
    parser=argparse.ArgumentParser(description='Replay saved crossing detections through MaskCounter association.')
    parser.add_argument('--fixture',type=Path,default=DEFAULT_FIXTURE)
    parser.add_argument('--window',action='append',help='window name; repeatable')
    parser.add_argument('--max-step',type=float,help='experimental max_step override')
    args=parser.parse_args()
    fixture=load_fixture(args.fixture)
    selected=[w for w in fixture['windows'] if not args.window or w['name'] in args.window]
    if args.window and len(selected)!=len(set(args.window)):
        parser.error('unknown or duplicate window name')
    overrides={} if args.max_step is None else {'max_step':args.max_step}
    print(json.dumps([replay_window(w,overrides) for w in selected],ensure_ascii=False,indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
