"""WO-0012 proposed 16-byte feature/frame records and PC-only H3 audit.

This is a draft codec, not a DMA driver, installed ABI, or production PS path.
"""
import argparse
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'python'))
from refmodel.ps_reference.regions import Region, extract_regions, preprocess
from refmodel.experiments.wo0012_bounded_runs import extract_run_frame, RunCaps
from refmodel.ps_reference.tracking import Tracker
from refmodel.ps_reference.evaluate_h3 import (
    _load_sequences, _input_sha256, derive_ground_truth_events,
    match_regions_to_ground_truth, score_count_events,
)


def _uint(value, bits, name):
    if type(value) is not int or not 0 <= value < 1 << bits:
        raise ValueError(f'{name} must be uint{bits}')
    return value


def quantize(value, mode='nearest_half_up'):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('centroid must be finite and nonnegative')
    if mode == 'floor':
        return math.floor(value)
    if mode == 'nearest_half_up':
        return math.floor(value + 0.5)
    raise ValueError('unknown quantization mode')


def _validate_region(r, width, height):
    if not isinstance(r, Region):
        raise ValueError('expected Region')
    for value, bits, name in [(r.x_min, 10, 'x_min'), (r.x_max, 10, 'x_max'),
                              (r.row_min, 8, 'row_min'), (r.row_max, 8, 'row_max'),
                              (r.area, 16, 'area'), (r.peak, 8, 'peak'), (r.flags, 8, 'flags')]:
        _uint(value, bits, name)
    if not (0 <= r.x_min <= r.x_max < width and 0 <= r.row_min <= r.row_max < height):
        raise ValueError('invalid bbox or configured image dimensions')
    if not 0 < r.area <= (r.x_max-r.x_min+1) * (r.row_max-r.row_min+1):
        raise ValueError('area outside bbox or zero')
    if r.peak != 0 or r.flags & ~7 or not r.flags & 4:
        raise ValueError('draft requires known 2D flags and peak=0')
    if not (math.isfinite(r.centroid_x) and math.isfinite(r.centroid_y)
            and r.x_min <= r.centroid_x <= r.x_max and r.row_min <= r.centroid_y <= r.row_max):
        raise ValueError('invalid centroid')


def encode_packet(regions, frame_id, timestamp, total_fg, frame_flags=0,
                  mode='nearest_half_up', width=320, height=240, record_capacity=32, epoch=0):
    regions = list(regions)
    if record_capacity not in (32, 64):
        raise ValueError('supported draft capacities are 32 and 64')
    if len(regions) > record_capacity:
        raise ValueError('draft record capacity exceeded')
    for value, bits, name in [(frame_id, 32, 'frame_id'), (timestamp, 32, 'timestamp'),
                              (total_fg, 32, 'total_fg'), (frame_flags, 8, 'frame_flags'),
                              (epoch, 16, 'epoch')]:
        _uint(value, bits, name)
    if total_fg > width*height or frame_flags & 0xC0:
        raise ValueError('invalid foreground or reserved frame flags')
    if frame_flags and regions:
        raise ValueError('error frame must contain no feature records')
    output = bytearray()
    for r in regions:
        _validate_region(r, width, height)
        cx, cy = quantize(r.centroid_x, mode), quantize(r.centroid_y, mode)
        word0 = r.x_min | r.x_max << 10 | cx << 20
        word1 = r.area | r.peak << 16 | r.row_min << 24
        word2 = r.row_max | cy << 8 | r.flags << 16
        output.extend(struct.pack('<IIII', word0, word1, word2, 0))
    if sum(r.area for r in regions) > total_fg:
        raise ValueError('feature area sum exceeds total_fg')
    output.extend(struct.pack('<IIII', frame_id, timestamp, total_fg,
                              len(regions) | frame_flags << 8 | epoch << 16))
    return bytes(output)


def decode_packet(packet, expected_frame_id=None, width=320, height=240, record_capacity=32, expected_epoch=0):
    if record_capacity not in (32, 64):
        raise ValueError('supported draft capacities are 32 and 64')
    if len(packet) < 16 or len(packet) % 16:
        raise ValueError('partial packet or missing metadata')
    fid, timestamp, total_fg, tail = struct.unpack('<IIII', packet[-16:])
    count, flags = tail & 255, (tail >> 8) & 255
    epoch = tail >> 16
    if epoch != expected_epoch:
        raise ValueError('stale or incorrect epoch')
    if count > record_capacity or len(packet) != 16*(count+1):
        raise ValueError('metadata count and actual bytes disagree')
    if expected_frame_id is not None and fid != expected_frame_id:
        raise ValueError('stale or incorrect frame_id')
    if flags:
        raise ValueError('frame error; no partial features may be delivered')
    records = []
    for offset in range(0, len(packet)-16, 16):
        w0, w1, w2, w3 = struct.unpack_from('<IIII', packet, offset)
        if w0 & 0xC0000000 or w2 & 0xFF000000 or w3:
            raise ValueError('reserved record bits')
        r = Region(w0 & 1023, (w0 >> 10) & 1023, w1 >> 24, w2 & 255,
                   w1 & 65535, float((w0 >> 20) & 1023), float((w2 >> 8) & 255),
                   (w1 >> 16) & 255, (w2 >> 16) & 255)
        _validate_region(r, width, height)
        records.append(r)
    if total_fg > width*height or sum(r.area for r in records) > total_fg:
        raise ValueError('invalid foreground summary')
    return {'frame_id': fid, 'timestamp': timestamp, 'total_fg': total_fg,
            'seg_count': count, 'flags': flags, 'epoch': epoch}, records


def axis32_beats(packet):
    if not packet or len(packet) % 4:
        raise ValueError('whole 32-bit beats required')
    words = struct.unpack('<'+'I'*(len(packet)//4), packet)
    return [{'tdata': word, 'tkeep': 15, 'tlast': index == len(words)-1}
            for index, word in enumerate(words)]


def encode_binary_packet(binary, frame_id, timestamp, epoch=0):
    """Separate debug mode: row-major Binary8 pixels then the 16-byte trailer."""
    if (not isinstance(binary, np.ndarray) or binary.dtype != np.uint8
            or binary.shape != (240, 320) or not np.isin(binary, (0, 255)).all()):
        raise ValueError('draft Binary8 mode requires 320x240 uint8 00/FF')
    trailer = encode_packet([], frame_id, timestamp, int(np.count_nonzero(binary)), epoch=epoch)
    return binary.tobytes(order='C') + trailer


def decode_binary_packet(packet, expected_frame_id=None, expected_epoch=0):
    if len(packet) != 76816:
        raise ValueError('Binary8 debug packet must be exactly 76816 bytes')
    meta, records = decode_packet(packet[-16:], expected_frame_id=expected_frame_id, expected_epoch=expected_epoch)
    if records:
        raise ValueError('Binary8 mode cannot contain feature records')
    binary = np.frombuffer(packet, dtype=np.uint8, count=76800).reshape(240, 320).copy()
    if not np.isin(binary, (0, 255)).all() or np.count_nonzero(binary) != meta['total_fg']:
        raise ValueError('Binary8 pixels/foreground trailer mismatch')
    return meta, binary


def evaluate_h3(data, out):
    data, out = Path(data).resolve(), Path(out).resolve()
    if out.exists() or out == data or data in out.parents:
        raise ValueError('new output directory outside input required')
    _, sequences, source_files = _load_sequences(data)
    hashes = _input_sha256(data, source_files)
    modes = ('float_reference', 'floor', 'nearest_half_up', 'bounded_nearest')
    rows = {mode: {} for mode in modes}
    errors = {mode: {'max_x': 0.0, 'max_y': 0.0} for mode in modes}
    traces = {}
    bounded_rejected_frames = 0
    out.mkdir(parents=True)
    for sequence in sequences:
        trackers = {mode: Tracker() for mode in modes}
        predictions = {mode: [] for mode in modes}
        ids = {mode: {} for mode in modes}
        switches = {mode: 0 for mode in modes}
        frame_traces = []
        for frame in sequence['frames']:
            fid = frame['frame_id']
            path = sequence['condition_dir']/'frames'/f'frame_{fid:04d}.png'
            if path.is_file():
                with Image.open(path) as image:
                    gray = np.array(image, dtype=np.uint8, copy=True)
                original = extract_regions(gray)
                total_fg = int(np.count_nonzero(gray >= 128))
                bounded = extract_run_frame(preprocess(gray), caps=RunCaps(40, 32, 32))
                if not bounded.accepted:
                    bounded_rejected_frames += 1
                bounded_regions = bounded.regions
                bounded_flags = ({'row_run_cap_exceeded': 2, 'active_cap_exceeded': 4,
                                  'record_cap_exceeded': 1, 'multi_root_merge': 8,
                                  'area_overflow': 16}[bounded.rejection_reason]
                                 if not bounded.accepted else 0)
            else:
                original, total_fg = [], 0
                bounded_regions = []
                bounded_flags = 0
            observations = {'float_reference': original}
            for mode in ('floor', 'nearest_half_up'):
                packet = encode_packet(original, fid, 0, total_fg, mode=mode)
                _, observations[mode] = decode_packet(packet, expected_frame_id=fid)
                for raw, wire in zip(original, observations[mode]):
                    errors[mode]['max_x'] = max(errors[mode]['max_x'], abs(raw.centroid_x-wire.centroid_x))
                    errors[mode]['max_y'] = max(errors[mode]['max_y'], abs(raw.centroid_y-wire.centroid_y))
            packet = encode_packet(bounded_regions, fid, 0, total_fg, frame_flags=bounded_flags, epoch=2)
            if bounded_flags:
                try:
                    decode_packet(packet, expected_frame_id=fid, expected_epoch=2)
                except ValueError as exc:
                    assert 'frame error' in str(exc)
                else:
                    raise AssertionError('invalid bounded frame was delivered')
                observations['bounded_nearest'] = []
            else:
                _, observations['bounded_nearest'] = decode_packet(packet, expected_frame_id=fid, expected_epoch=2)
            for raw, wire in zip(bounded_regions, observations['bounded_nearest']):
                errors['bounded_nearest']['max_x'] = max(errors['bounded_nearest']['max_x'], abs(raw.centroid_x-wire.centroid_x))
                errors['bounded_nearest']['max_y'] = max(errors['bounded_nearest']['max_y'], abs(raw.centroid_y-wire.centroid_y))
            inferred = {mode: trackers[mode].update(fid, observations[mode]) for mode in modes}
            # Identity matching only after all three inference steps.
            fr = {'frame_id': fid, 'modes': {}}
            for mode in modes:
                tracker = trackers[mode]
                matches, _ = match_regions_to_ground_truth(observations[mode], frame['objects'])
                for tid, ri in tracker.last_assignments:
                    oid = matches.get(ri, {}).get('object_id')
                    if oid is not None:
                        switches[mode] += int(oid in ids[mode] and ids[mode][oid] != tid)
                        ids[mode][oid] = tid
                by_id = dict(tracker.last_assignments)
                events = []
                for ev in inferred[mode]:
                    oid = matches.get(by_id[ev.track_id], {}).get('object_id')
                    item = {'object_id': oid, 'frame_id': fid, 'track_id': ev.track_id}
                    predictions[mode].append(item)
                    events.append(item)
                fr['modes'][mode] = {'centroids': [[r.centroid_x, r.centroid_y] for r in observations[mode]],
                                     'assignments': tracker.last_assignments, 'events': events}
            frame_traces.append(fr)
        truth = derive_ground_truth_events(sequence['frames'])
        for mode in modes:
            rows[mode][sequence['condition_id']] = {**score_count_events(truth, predictions[mode]),
                                                   'id_switches': switches[mode]}
        traces[sequence['condition_id']] = frame_traces
    aggregate = {}
    fields = ('event_tp', 'event_fn', 'duplicate_fp', 'spurious_fp', 'predicted_event_count',
              'qualified_truth_count', 'center_truth_count', 'pending_event_count', 'id_switches')
    for mode in modes:
        a = {k: sum(row[k] for row in rows[mode].values()) for k in fields}
        a['precision'] = a['event_tp']/(a['event_tp']+a['duplicate_fp']+a['spurious_fp'])
        a['recall'] = a['event_tp']/a['qualified_truth_count']
        aggregate[mode] = a
    assert aggregate['float_reference']['event_tp'] == 19
    assert aggregate['floor']['event_tp'] == 5
    assert bounded_rejected_frames == 0
    assert aggregate['bounded_nearest'] == aggregate['nearest_half_up']
    assert hashes == _input_sha256(data, source_files)
    summary = {'status': 'PROPOSED_PC_MODEL_ONLY', 'aggregate': aggregate,
               'bounded_rejected_h3_frames': bounded_rejected_frames,
               'conditions': rows, 'quantization_errors': errors, 'input_sha256': hashes,
               'trace_sha256': hashlib.sha256(json.dumps(traces, sort_keys=True).encode()).hexdigest(),
               'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'scope': 'All 21 H3 sequences, line160/h2 exact event protocol; no new truth or latency allowance'}
    (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    (out/'trace.json').write_text(json.dumps(traces, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'aggregate': aggregate, 'quantization_errors': errors}, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=ROOT/'data/synthetic/h3_full')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    evaluate_h3(args.data, args.out)
