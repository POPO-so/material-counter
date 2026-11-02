"""Freeze pinned RTL and run a real 640x400 packet/RLE/FRG XSim regression.

This tests the JPEG output protocol boundary; it does not simulate JPEG decode.
All generated files stay under --build-root. Source RTL is never modified.
"""
import argparse
from collections import deque
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

WIDTH, HEIGHT = 640, 400
PIXELS = WIDTH * HEIGHT
PINNED_COMMIT = 'c5a86807c93e6ed572e2f75e13a6f9c7f1320321'
REGION_SHA = '19439a2c666696d48521b112705763df6ac48012cfacd9476fcd6850a3551911'
RTL = [
    'fpga/experimental/gray8/axis_dma_gray_adapter.v',
    'fpga/experimental/gray8/axis_raw_gray_adapter.v',
    'fpga/rtl/axis_morphology3x3.v',
    'fpga/experimental/rle/axis_rle_encoder.v',
    'fpga/experimental/rle/axis_dma_rle_pipeline_bd.v',
    'fpga/experimental/rle/rle_feature_tap.v',
    'fpga/experimental/standalone/video_binary_overlay.v',
    'fpga/experimental/standalone/video_binary_overlay_bd.v',
]

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write_hex(path, words):
    path.write_text(''.join(f'{word & 0xffffffff:08x}\n' for word in words), encoding='ascii')

def raster_oracle(gray, roi, frame_id):
    """Independent pixel flood fill, maximal runs and row/column projections."""
    x0, y0, x1, y1 = roi
    fg = {(x, y) for y in range(y0, y1) for x in range(x0, x1) if gray[y*WIDTH+x] >= 128}
    rows, columns, runs = [0]*HEIGHT, [0]*WIDTH, []
    for x, y in fg:
        rows[y] += 1
        columns[x] += 1
    for y in range(HEIGHT):
        x = 0
        while x < WIDTH:
            if (x, y) not in fg:
                x += 1
                continue
            start = x
            while x+1 < WIDTH and (x+1, y) in fg:
                x += 1
            runs.append((y << 20) | (start << 10) | x)
            x += 1
    remaining, records = set(fg), []
    for seed in sorted(fg, key=lambda p: (p[1], p[0])):
        if seed not in remaining:
            continue
        remaining.remove(seed)
        queue, component = deque([seed]), []
        while queue:
            x, y = queue.popleft()
            component.append((x, y))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    neighbor = (x+dx, y+dy)
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        queue.append(neighbor)
        xs, ys = [p[0] for p in component], [p[1] for p in component]
        records.extend([len(component), sum(xs), sum(ys),
                        (max(xs) << 16) | min(xs), (max(ys) << 16) | min(ys), frame_id, 0, 0])
    projection = rows + columns
    packed = [projection[i] | (projection[i+1] << 16) for i in range(0, len(projection), 2)]
    assert len(runs) <= 16384 and len(records)//8 <= 512
    return fg, runs, packed, records

def make_case(out, index, name, gray, fid, roi, fault='none'):
    assert len(gray) == PIXELS
    packet_id = fid + (1 if fault == 'frame_id' else 0)
    checksum = sum(gray) & 0xffffffff
    packet = [0x47525938, (HEIGHT << 16) | WIDTH, PIXELS, packet_id]
    packet += list(struct.unpack('<64000I', gray))
    packet += [0x454e4438, packet_id, (checksum+(fault == 'checksum')) & 0xffffffff, 0xc0dec018]
    fg, runs, projections, records = raster_oracle(gray, roi, fid)
    status = 0 if fault == 'none' else 2
    error = {'none': 0, 'checksum': 5, 'frame_id': 7}[fault]
    parser_error = 5 if fault == 'checksum' else 0
    x0, y0, x1, y1 = roi
    header = [0x31454c52, 1, (HEIGHT << 16) | WIDTH, fid, 3,
              (y0 << 16) | x0, (y1 << 16) | x1, 0, 0, 1, 520, 16384, 128, 0, 0, 0]
    body = header + runs + projections
    actual_bytes = (len(body)+16)*4
    trailer = [0x31444e45, fid, 3, len(fg), len(runs), len(runs), status,
               actual_bytes, sum(body) & 0xffffffff, error, PIXELS, 0, 0, 0, 0, 0xc0dec0de]
    rle = body + trailer
    meta = [fid, status, error, len(rle), len(records)//8, x0, y0, x1, y1,
            parser_error, len(fg), 0, 0, 0, 0, 0]
    for kind, values in [('packet', packet), ('rle', rle), ('frg', records), ('meta', meta)]:
        write_hex(out/f'case{index}_{kind}.hex', values)
    return {'name': name, 'frame_id': fid, 'packet_frame_id': packet_id, 'fault': fault,
            'foreground': len(fg), 'runs': len(runs), 'frg_records': len(records)//8,
            'rle_words': len(rle), 'roi': list(roi), 'gray_sha256': hashlib.sha256(gray).hexdigest(),
            'expected_status': status, 'expected_error': error}

def tcl_path(path):
    return '{' + Path(path).resolve().as_posix() + '}'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=Path('J:/'))
    parser.add_argument('--build-root', type=Path, default=Path('C:/w/mc-jpeg-joint-packet-20261101'))
    parser.add_argument('--vivado', type=Path, default=Path('C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat'))
    parser.add_argument('--vendor-y', type=Path)
    args = parser.parse_args()
    owned = Path(__file__).resolve().parent
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    build = args.build_root.resolve()/stamp
    inputs, vectors, sim = build/'input', build/'vectors', build/'sim'
    for folder in (inputs, vectors, sim):
        folder.mkdir(parents=True, exist_ok=False)
    manifest_file = args.snapshot/'jpeg-joint-source.json'
    manifest = json.loads(manifest_file.read_text(encoding='utf-8-sig'))
    assert manifest['base_commit'] == PINNED_COMMIT
    shutil.copy2(manifest_file, inputs/'jpeg-joint-source.json')
    sources, frozen = [], []
    for relative in RTL:
        original = args.snapshot/relative
        expected = manifest['files'][relative]['sha256']
        assert digest(original) == expected, f'Pinned snapshot mismatch: {relative}'
        target = inputs/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        assert digest(target) == expected
        sources.append(target)
        frozen.append({'source': str(original.resolve()), 'relative': relative,
                       'snapshot': str(target.resolve()), 'sha256': expected})
    for name in ['rle_region_features.v', 'tb_joint_packet.sv', 'tb_joint_packet_run.py']:
        original, target = owned/name, inputs/name
        shutil.copy2(original, target)
        expected = digest(target)
        if name == 'rle_region_features.v':
            assert expected == REGION_SHA, 'Frozen region RTL changed'
        if name.endswith(('.v', '.sv')):
            sources.append(target)
        frozen.append({'source': str(original.resolve()), 'relative': name,
                       'snapshot': str(target.resolve()), 'sha256': expected})
    synthetic = bytearray([20])*PIXELS
    for y in range(5, 8):
        for x in range(10, 20):
            synthetic[y*WIDTH+x] = 200
    configurations = [('good_synthetic', 'none'), ('bad_checksum', 'checksum'),
                      ('bad_frame_id', 'frame_id'), ('good_recovery', 'none')]
    cases = [make_case(vectors, i, name, bytes(synthetic), 0x2001+i,
                       (0, 0, WIDTH, HEIGHT), fault) for i, (name, fault) in enumerate(configurations)]
    vendor = None
    if args.vendor_y:
        vendor_raw = args.vendor_y.read_bytes()
        assert len(vendor_raw) == 512000, 'Expected existing padded vendor YUV reference (512000 bytes)'
        vendor_copy = inputs/'vendor_planar_padded_yuv.bin'
        vendor_copy.write_bytes(vendor_raw)
        vendor = {'path': str(args.vendor_y.resolve()), 'sha256': digest(vendor_copy),
                  'frozen_copy': str(vendor_copy), 'interpretation': 'First 256000 bytes are raster Y; chroma unused'}
        cases.append(make_case(vectors, len(cases), 'vendor_y_real_frame', vendor_raw[:PIXELS],
                               0x2005, (240, 180, 272, 196)))
    input_manifest = {'base_commit': PINNED_COMMIT, 'region_sha256': REGION_SHA,
                      'sources': frozen, 'cases': cases, 'vendor_y': vendor,
                      'scope': 'Gray8 packet boundary simulation, not JPEG RTL decode'}
    frozen_manifest = build/'tb_joint_packet_inputs.json'
    frozen_manifest.write_text(json.dumps(input_manifest, indent=2)+'\n', encoding='utf-8')
    compile_command = 'exec xvlog -sv ' + ' '.join(tcl_path(p) for p in sources) + ' > compile.log 2>@1'
    tcl = build/'tb_joint_packet_xsim.tcl'
    tcl.write_text(compile_command+'\n'
                   'exec xelab tb_joint_packet -s tb_joint_packet_snapshot_mt2 -mt 2 -v 2 > elaborate.log 2>@1\n'
                   f'exec xsim tb_joint_packet_snapshot_mt2 -runall -testplusarg {{VECTORS={vectors.as_posix()}}} '
                   f'-testplusarg {{CASES={len(cases)}}} > simulate.log 2>@1\n', encoding='ascii')
    with (sim/'driver_stdout.log').open('wb') as stream:
        completed = subprocess.run([str(args.vivado), '-mode', 'batch', '-source', str(tcl),
                                    '-log', 'vivado.log', '-journal', 'vivado.jou'],
                                   cwd=sim, stdout=stream, stderr=subprocess.STDOUT, timeout=240)
    assert completed.returncode == 0, f'Vivado/XSim failed: inspect {sim}'
    simulation = (sim/'simulate.log').read_text(encoding='utf-8', errors='replace')
    assert f'JOINT_PACKET_PASS cases={len(cases)} width=640 height=400' in simulation
    assert not re.search(r'^\s*(Fatal:|ERROR:)', simulation, re.M)
    assert len(re.findall(r'^JOINT_PACKET_CASE_PASS ', simulation, re.M)) == len(cases)
    measured = []
    for line in simulation.splitlines():
        if not line.startswith('JOINT_PACKET_CASE_PASS '):
            continue
        values = dict(re.findall(r'(\w+)=(\w+)', line))
        counters = {key: int(value, 16 if key == 'fid' else 10) for key, value in values.items()}
        expected = cases[counters['case']]
        assert counters['fid'] == expected['frame_id']
        assert counters['pixels'] == PIXELS and counters['frg_records'] == expected['frg_records']
        assert counters['input_stalls'] > 0 and counters['output_stalls'] > 0
        assert counters['accept'] == (expected['fault'] == 'none')
        measured.append(counters)
    explicit_accepts = int(re.search(r'^JOINT_PACKET_PASS .*explicit_accepts=(\d+)', simulation, re.M).group(1))
    assert explicit_accepts == sum(item['accept'] for item in measured)
    for entry in frozen:
        assert digest(entry['snapshot']) == entry['sha256']
        assert digest(entry['source']) == entry['sha256'], f'Source changed during run: {entry["source"]}'
    evidence_files = [frozen_manifest, tcl, sim/'compile.log', sim/'elaborate.log', sim/'simulate.log', sim/'vivado.log']
    evidence_files += sorted(vectors.glob('*.hex'))
    evidence_files += sorted(sim.glob('observed_*.hex'))
    result = {'status': 'PASS', 'source_sha256': digest(owned/'tb_joint_packet.sv'),
              'testbench_sha256': digest(owned/'tb_joint_packet.sv'),
              'runner_sha256': digest(Path(__file__)),
              'region_sha256': REGION_SHA, 'pipeline_sha256': manifest['files'][RTL[4]]['sha256'],
              'baseline_commit': PINNED_COMMIT, 'cases': cases, 'build_dir': str(build),
              'width': WIDTH, 'height': HEIGHT,
              'invalid_gate': 'observed RLE status/error == 0, pipeline input_error == 0, FRG done && !overflow && same frame_id',
              'frg_semantics': 'Speculative capture has no packet-invalid input; invalid frame records are not accepted',
              'automatic_mask_accept': False, 'jpeg_rtl_decode_tested': False,
              'vid_active': 0,
              'display_verification_scope': 'VSYNC publication/retention of displayed_id only; HDMI active-pixel continuity not tested',
              'measured_cases': measured,
              'counts': {'cases': len(cases), 'good': sum(c['fault'] == 'none' for c in cases),
                         'bad_checksum': 1, 'bad_frame_id': 1, 'recovery_good': 1,
                         'explicit_mask_accept': explicit_accepts, 'automatic_mask_accept': 0,
                         'invalid_frame_accept': 0,
                         'pixels_checked': sum(item['pixels'] for item in measured),
                         'input_stalls': sum(item['input_stalls'] for item in measured),
                         'output_stalls': sum(item['output_stalls'] for item in measured)},
              'evidence': [{'path': str(p.resolve()), 'sha256': digest(p)} for p in evidence_files]}
    result_file = build/'tb_joint_packet_results.json'
    result_file.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    (owned/'tb_joint_packet_results.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status': 'PASS', 'cases': len(cases), 'build_dir': str(build),
                      'result_path': str(result_file), 'result_sha256': digest(result_file),
                      'region_sha256': REGION_SHA}))

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'JOINT_PACKET_FAIL: {exc}', file=sys.stderr)
        raise
