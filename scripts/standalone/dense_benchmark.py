"""CPU-only dense split and 100 synthetic-object regression; never opens hardware."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import time

import cv2
import numpy as np

from scripts.rle import counter as counter_module
from scripts.rle.protocol import decode_packet, encode_mask
from scripts.standalone.count_controls import make_runtime_counter
from scripts.wo0016.split_touching import split_touching_component


def distribution(values):
    return dict(count=len(values), p50=float(np.percentile(values, 50)),
                p95=float(np.percentile(values, 95)), max=float(max(values))) if values else None


def stress_mask():
    pixels = np.zeros((256, 256), np.uint8)
    starts = (1, 78, 155, 232)
    path = []
    for row, top in enumerate(starts):
        for col, left in enumerate(starts):
            pixels[top:top+23, left:left+23] = 1
            pixels[top+5:top+18, left+5:left+18] = 0
        path.extend((row, col) for col in (range(4) if row % 2 == 0 else range(3, -1, -1)))
    for (r1, c1), (r2, c2) in zip(path, path[1:]):
        if r1 == r2:
            pixels[starts[r1]+11, min(starts[c1], starts[c2])+23:max(starts[c1], starts[c2])+1] = 1
        else:
            pixels[min(starts[r1], starts[r2])+23:max(starts[r1], starts[r2])+1, starts[c1]+11] = 1
    return pixels


def hundred_objects():
    """Five waves of ten two-ring parents, with explicit object crossing windows."""
    truth, frames = [], []
    for batch in range(5):
        start_frame = len(frames)
        for lane in range(10):
            for child, crossing in ((0, 13), (1, 9)):
                truth.append(dict(object_id=f'{batch}:{lane}:{child}',
                    frame_id_min=start_frame+crossing-1, frame_id_max=start_frame+crossing+1,
                    centroid_y=22+lane*36, lateral_tolerance_px=3))
        for step in range(21):
            mask = np.zeros((400, 640), np.uint8)
            for lane in range(10):
                for center_x in (260+step*5, 283+step*5):
                    cv2.circle(mask, (center_x, 22+lane*36), 13, 255, -1)
                    cv2.circle(mask, (center_x, 22+lane*36), 6, 0, -1)
            frames.append(mask)
        frames.extend(np.zeros((400, 640), np.uint8) for _ in range(4))
    return frames, truth


def run_benchmark(library, repeats=20):
    cv2.setNumThreads(1)
    root = Path(counter_module.__file__).resolve().parents[2]
    sources = {}
    for name in ('scripts/rle/counter.py', 'scripts/rle/components.py', 'scripts/rle/split_work.py',
                 'scripts/wo0016/split_touching.py', 'scripts/wo0016/stream_counter.py',
                 'scripts/standalone/count_controls.py'):
        path = root/name
        sources[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    cases = []
    fixture = root/'scripts/rle/fixtures/conveyor-clusters-20261103.npz'
    with np.load(fixture, allow_pickle=False) as archive:
        masks = [(f'captured-{number}', archive[f'frame_{number}'].copy()) for number in (5298, 5307)]
    masks.append(('stress-16', stress_mask()))
    for name, mask in masks:
        timings, counts, partition_hashes, child_areas = [], [], [], []
        for _ in range(repeats):
            started = time.perf_counter()
            pieces = split_touching_component(mask, min_child_area=80)
            timings.append((time.perf_counter()-started)*1000)
            counts.append(len(pieces))
            if not partition_hashes:
                partition_hashes = [hashlib.sha256(np.asarray(piece, dtype=np.uint8).tobytes()).hexdigest()
                                    for piece in pieces]
                child_areas = [int(np.count_nonzero(piece)) for piece in pieces]
            coverage = np.sum(pieces, axis=0)
            if not np.array_equal(coverage, mask != 0):
                raise AssertionError('split did not preserve exact coverage')
        cases.append(dict(name=name, input_sha256=hashlib.sha256(mask.tobytes()).hexdigest(),
                          children=counts, child_areas=child_areas, partition_sha256=partition_hashes,
                          split_ms=distribution(timings)))
    masks, truth = hundred_objects()
    packets = [decode_packet(encode_mask(mask, frame_id=i)) for i, mask in enumerate(masks)]
    counter = make_runtime_counter(library, profile='field-v1')
    rows, events = [], []
    digest = hashlib.sha256()
    for number, (mask, packet) in enumerate(zip(masks, packets)):
        digest.update(mask.tobytes())
        started = time.perf_counter()
        result = counter.update_rle(packet, capture_monotonic_ns=(number+1)*100_000_000)
        timings = result.roi_subtimings_ms
        events.extend(asdict(event) for event in result.events)
        rows.append(dict(frame_id=number, wall_ms=(time.perf_counter()-started)*1000,
            split_ms=timings.get('split_ms', 0), region_ms=timings['rle_regions_ms'],
            track_ms=timings['track_ms'], split_stats=dict(getattr(counter, 'last_split_stats', {}))))
    owners = [[obj['object_id'] for obj in truth
               if obj['frame_id_min'] <= event['frame_id'] <= obj['frame_id_max']
               and abs(obj['centroid_y']-event['centroid_y']) <= obj['lateral_tolerance_px']]
              for event in events]
    object_events = {obj['object_id']: [i for i, matches in enumerate(owners)
                                      if matches == [obj['object_id']]] for obj in truth}
    unique_correct = sum(len(matches) == 1 for matches in object_events.values())
    return dict(schema='material-counter.dense-cpu-benchmark.v1',
        environment=dict(python=platform.python_version(), system=platform.system(),
                         machine=platform.machine(), numpy=np.__version__, opencv=cv2.__version__,
                         opencv_threads=cv2.getNumThreads()),
        benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        native_sha256=hashlib.sha256(Path(library).read_bytes()).hexdigest(),
        source_sha256=sources, cases=cases,
        synthetic=dict(input_sha256=digest.hexdigest(), expected_objects=len(truth),
            actual_events=len(events), uniquely_correct_objects=unique_correct,
            passed=unique_correct == 100 and len(events) == 100 and all(len(o) == 1 for o in owners),
            per_object=object_events, event_owners=owners, events=events, truth=truth, frames=rows,
            summary={key: distribution([row[key] for row in rows])
                     for key in ('wall_ms', 'split_ms', 'region_ms', 'track_ms')}),
        scope='CPU-only synthetic truth and two captured static masks; not board throughput or real-material accuracy.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-lib', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--repeats', type=int, default=20)
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error('repeats must be positive')
    report = run_benchmark(args.native_lib, args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(cases=[dict(name=c['name'], split_ms=c['split_ms']) for c in report['cases']],
                         synthetic=report['synthetic']['summary'],
                         correct=report['synthetic']['uniquely_correct_objects'],
                         events=report['synthetic']['actual_events'])))
    return 0 if report['synthetic']['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
