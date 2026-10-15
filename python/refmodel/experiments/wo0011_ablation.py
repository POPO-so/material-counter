"""Offline WO-0011 ablations. No changes to the committed WO-0010 detector.

Inference receives only the current image and previous tracker observations.
Truth is used afterwards for the same strict event scoring as WO-0010.
"""
import argparse
import csv
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'python'))
from refmodel.ps_reference.regions import preprocess, extract_regions
from refmodel.ps_reference.tracking import Tracker, TrackerConfig
from refmodel.ps_reference.evaluate_h3 import (
    _load_sequences, _input_sha256, derive_ground_truth_events,
    match_regions_to_ground_truth, score_count_events, canonical_trace_sha256,
)


def transform(gray, strategy):
    """Return Binary8 using explicit zero-border morphology or enclosed fill."""
    if strategy in ('bypass', 'binary_direct'):
        return preprocess(gray)
    if strategy in ('erosion', 'dilation'):
        return preprocess(gray, morph_mode=1 if strategy == 'erosion' else 2)
    binary = preprocess(gray)
    if strategy == 'opening':
        # Separate operations give the same explicit border rule as existing RTL.
        return preprocess(preprocess(binary, morph_mode=1), morph_mode=2)
    if strategy == 'fill_holes':
        # Padding guarantees the seed is outside, even if pixel(0,0) is foreground.
        padded = np.pad(binary, 1, constant_values=0)
        exterior = padded.copy()
        cv2.floodFill(exterior, None, (0, 0), 255, flags=4)
        holes = cv2.bitwise_not(exterior)[1:-1, 1:-1]
        return cv2.bitwise_or(binary, holes)
    raise ValueError('unsupported image strategy')


def image_features(gray, strategy='bypass', min_area=16, max_width=24,
                   integer_centroid=False):
    if strategy == 'bypass':
        regions = extract_regions(gray, min_area=min_area, max_width=max_width)
    else:
        regions = extract_regions(transform(gray, strategy), min_area=min_area,
                                  max_width=max_width)
    if integer_centroid:
        # Candidate wire-format truncation, not an approved interface encoding.
        regions = [replace(r, centroid_x=float(int(r.centroid_x)),
                           centroid_y=float(int(r.centroid_y))) for r in regions]
    return regions


def variants():
    return [
        {'name': 'bypass', 'strategy': 'bypass'},
        {'name': 'binary_direct', 'strategy': 'binary_direct'},
        {'name': 'erosion', 'strategy': 'erosion'},
        {'name': 'dilation', 'strategy': 'dilation'},
        {'name': 'opening', 'strategy': 'opening'},
        {'name': 'fill_holes', 'strategy': 'fill_holes'},
        {'name': 'width_guard_off', 'strategy': 'bypass', 'max_width': 0},
        {'name': 'min_area_1', 'strategy': 'bypass', 'min_area': 1},
        {'name': 'min_area_128', 'strategy': 'bypass', 'min_area': 128},
        {'name': 'missing_0', 'strategy': 'bypass', 'max_missing': 0},
        {'name': 'missing_4', 'strategy': 'bypass', 'max_missing': 4},
        {'name': 'step_2', 'strategy': 'bypass', 'max_step': 2.0},
        {'name': 'confirm_3', 'strategy': 'bypass', 'confirm_hits': 3},
        {'name': 'integer_centroid_floor', 'strategy': 'bypass', 'integer_centroid': True},
    ]


def evaluate_sequence(sequence, variant):
    config = TrackerConfig(**{key: variant[key] for key in
                             ('max_missing', 'max_step', 'confirm_hits') if key in variant})
    tracker = Tracker(config)
    frames, predicted = [], []
    feature_totals = dict(feature_tp=0, feature_fn=0, feature_fp=0)
    previous_ids = {}
    id_switches = 0
    for frame in sequence['frames']:
        fid = frame['frame_id']
        path = sequence['condition_dir'] / 'frames' / f'frame_{fid:04d}.png'
        # Admission is based on observed files; no GT-driven image synthesis.
        if path.is_file():
            with Image.open(path) as image:
                gray = np.array(image, dtype=np.uint8, copy=True)
            regions = image_features(gray, variant['strategy'],
                                     variant.get('min_area', 16), variant.get('max_width', 24),
                                     variant.get('integer_centroid', False))
        else:
            regions = []
        events = tracker.update(fid, regions)
        # Everything below this point is evaluator-only truth access.
        matches, metrics = match_regions_to_ground_truth(regions, frame['objects'])
        for key in feature_totals:
            feature_totals[key] += metrics[key]
        assignments = []
        index_by_track = dict(tracker.last_assignments)
        for tid, index in tracker.last_assignments:
            oid = matches.get(index, {}).get('object_id')
            if oid is not None:
                id_switches += int(oid in previous_ids and previous_ids[oid] != tid)
                previous_ids[oid] = tid
            assignments.append({'track_id': tid, 'region_index': index,
                                'scoring_object_id': oid})
        event_records = []
        for event in events:
            oid = matches.get(index_by_track[event.track_id], {}).get('object_id')
            predicted.append({'object_id': oid, 'frame_id': fid})
            event_records.append({**asdict(event), 'scoring_object_id': oid})
        frames.append({'frame_id': fid, 'observation_present': path.is_file(),
                       'regions': [asdict(r) for r in regions], 'assignments': assignments,
                       'events': event_records,
                       'tracks': [{'id': t.track_id, 'last_frame': t.last_frame,
                                   'state': t.state, 'confirmed': t.confirmed,
                                   'counted': t.counted} for t in tracker.tracks.values()]})
    truth = derive_ground_truth_events(sequence['frames'])
    score = score_count_events(truth, predicted)
    failure_types = dict(unmatched_fp=0, nonqualified_fp=0, timing_fp=0)
    seen = set()
    for event in predicted:
        oid = event['object_id']
        if oid is None:
            failure_types['unmatched_fp'] += 1
        elif oid not in seen:
            if oid not in truth['qualified_events']:
                failure_types['nonqualified_fp'] += 1
            elif event['frame_id'] != truth['allowed_observation_frames'][oid]:
                failure_types['timing_fp'] += 1
            seen.add(oid)
    assert sum(failure_types.values()) == score['spurious_fp']
    score.update(failure_types)
    score.update(feature_totals)
    score.update(id_switches=id_switches, merge_observations=tracker.merge_observations,
                 frame_count=sequence['num_frames'], missing_frames=len(sequence['dropped_frame_ids']),
                 png_frames=sequence['num_frames'] - len(sequence['dropped_frame_ids']),
                 max_records=max(len(f['regions']) for f in frames),
                 max_active_tracks=max(sum(t['state'] != 'finished' for t in f['tracks']) for f in frames))
    return {'condition_id': sequence['condition_id'], 'variant': variant,
            'truth': truth, 'frames': frames, 'summary': score}, score


def aggregate(rows):
    keys = ('event_tp', 'event_fn', 'duplicate_fp', 'spurious_fp', 'predicted_event_count',
            'qualified_truth_count', 'center_truth_count', 'pending_event_count',
            'feature_tp', 'feature_fn', 'feature_fp', 'id_switches', 'merge_observations',
            'frame_count', 'png_frames', 'missing_frames', 'unmatched_fp', 'nonqualified_fp', 'timing_fp')
    result = {key: sum(row[key] for row in rows) for key in keys}
    denominator = result['event_tp'] + result['duplicate_fp'] + result['spurious_fp']
    result['qualified_precision'] = result['event_tp'] / denominator if denominator else None
    result['qualified_recall'] = result['event_tp'] / result['qualified_truth_count']
    result['max_records'] = max(row['max_records'] for row in rows)
    result['max_active_tracks'] = max(row['max_active_tracks'] for row in rows)
    return result


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')


def feature_budget(sequences):
    stats = {'frames': 0, 'max_unfiltered_regions': 0, 'max_foreground': 0,
             'min_component_area': None, 'max_component_area': 0,
             'min_separated_area': None, 'max_separated_area': 0,
             'max_component_width': 0, 'max_component_height': 0,
             'max_separated_width': 0, 'max_separated_height': 0,
             'hole_changed_pixels': 0, 'max_independent_gt_objects': 0}
    for sequence in sequences:
        for frame in sequence['frames']:
            path = sequence['condition_dir'] / 'frames' / f"frame_{frame['frame_id']:04d}.png"
            if not path.is_file():
                continue
            gray = np.array(Image.open(path), dtype=np.uint8)
            binary = transform(gray, 'bypass')
            raw = image_features(gray, min_area=1, max_width=0)
            stats['frames'] += 1
            stats['max_unfiltered_regions'] = max(stats['max_unfiltered_regions'], len(raw))
            stats['max_foreground'] = max(stats['max_foreground'], int(np.count_nonzero(binary)))
            stats['hole_changed_pixels'] += int(np.count_nonzero(binary != transform(gray, 'fill_holes')))
            # GT used here only to audit the feature requirement, never inference.
            stats['max_independent_gt_objects'] = max(stats['max_independent_gt_objects'],
                                                      len(frame['objects']))
            for r in raw:
                stats['min_component_area'] = min(stats['min_component_area'] or r.area, r.area)
                stats['max_component_area'] = max(stats['max_component_area'], r.area)
                stats['max_component_width'] = max(stats['max_component_width'], r.x_max-r.x_min+1)
                stats['max_component_height'] = max(stats['max_component_height'], r.row_max-r.row_min+1)
                if r.x_max-r.x_min+1 <= 24:
                    stats['min_separated_area'] = min(stats['min_separated_area'] or r.area, r.area)
                    stats['max_separated_area'] = max(stats['max_separated_area'], r.area)
                    stats['max_separated_width'] = max(stats['max_separated_width'], r.x_max-r.x_min+1)
                    stats['max_separated_height'] = max(stats['max_separated_height'], r.row_max-r.row_min+1)
    return stats


def run(data, out):
    data, out = Path(data).resolve(), Path(out).resolve()
    if out.exists() or out == data or data in out.parents:
        raise ValueError('output must be new and outside the source dataset')
    _, sequences, files = _load_sequences(data)
    before = _input_sha256(data, files)
    out.mkdir(parents=True)
    all_results, conditions, digests = {}, {}, {}
    for variant in variants():
        name = variant['name']
        directory = out / name
        directory.mkdir()
        rows, conditions[name], digests[name] = [], {}, {}
        for sequence in sequences:
            trace, metrics = evaluate_sequence(sequence, variant)
            _write_json(directory / f"{sequence['condition_id']}.json", trace)
            rows.append(metrics)
            conditions[name][sequence['condition_id']] = metrics
            digests[name][sequence['condition_id']] = canonical_trace_sha256(trace['frames'])
        all_results[name] = aggregate(rows)
    assert digests['binary_direct'] == digests['bypass'], 'binary and bypass must be equivalent'
    reference = json.loads((ROOT / 'data/results/wo0010-reference-final-20261013/summary.json').read_text())
    base = reference['aggregate']['image']
    for key in ('event_tp', 'event_fn', 'duplicate_fp', 'spurious_fp', 'feature_tp',
                'feature_fn', 'feature_fp', 'qualified_truth_count', 'id_switches'):
        assert all_results['bypass'][key] == base[key], (key, all_results['bypass'][key], base[key])
    after = _input_sha256(data, files)
    assert before == after, 'input dataset changed'
    summary = {'scope': 'WO-0011 PC-only, no production changes',
               'protocol': 'WO-0010 line160/h2, object and first-visible-qualified-frame match',
               'variant_parameters': variants(), 'aggregate': all_results,
               'condition_summaries': conditions, 'input_sha256': before,
               'input_unchanged': True, 'baseline_reproduced': True,
               'binary_direct_equals_bypass': True, 'feature_budget': feature_budget(sequences),
               'trace_sha256': hashlib.sha256(json.dumps(digests, sort_keys=True).encode()).hexdigest(),
               'trace_hashes': digests,
               'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in [Path(__file__), *sorted((ROOT/'python/refmodel/ps_reference').glob('*.py'))]}}
    _write_json(out / 'summary.json', summary)
    with (out / 'comparison.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, ['variant', *all_results['bypass'].keys()])
        writer.writeheader()
        for name, row in all_results.items():
            writer.writerow({'variant': name, **row})
    print(json.dumps({'aggregate': all_results, 'feature_budget': summary['feature_budget'],
                      'trace_sha256': summary['trace_sha256']}, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=ROOT/'data/synthetic/h3_full')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run(args.data, args.out)
