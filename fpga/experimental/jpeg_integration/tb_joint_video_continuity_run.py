"""Run a pinned overlay/region RTL XSim test with continuous 720p digital video.

No packet test, DUT, physical HDMI/TMDS or board configuration is modified.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

REGION_SHA = '19439a2c666696d48521b112705763df6ac48012cfacd9476fcd6850a3551911'
BASE = 'c5a86807c93e6ed572e2f75e13a6f9c7f1320321'
PINNED = {
    'fpga/experimental/standalone/video_binary_overlay.v': '01d5563c7be95e92d38b20028063b7e85cf67ef4428d27c6c9d411b3b70ef9f9',
    'fpga/experimental/standalone/video_binary_overlay_bd.v': 'dfc487227d9ad9eade9084f2d90f72ddc7e909cd208ef91a1ced63548355e0be',
    'fpga/experimental/rle/rle_feature_tap.v': '493bc04f15e5b72b4a8ccf8b6257abc5d3810045b596334399a7517d99874b0c',
}

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def tcl_path(path):
    return '{' + Path(path).resolve().as_posix() + '}'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=Path('J:/'))
    parser.add_argument('--build-root', type=Path, default=Path('C:/w/mc-jpeg-video-continuity-20261101'))
    parser.add_argument('--vivado', type=Path, default=Path('C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat'))
    args = parser.parse_args()
    owned = Path(__file__).resolve().parent
    build = args.build_root.resolve()/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    inputs, sim = build/'input', build/'sim'
    inputs.mkdir(parents=True, exist_ok=False)
    sim.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.snapshot/'jpeg-joint-source.json').read_text(encoding='utf-8-sig'))
    assert manifest['base_commit'] == BASE
    protected = {name: sha(owned/name) for name in
                 ['rle_region_features.v', 'tb_joint_packet.sv', 'tb_joint_packet_run.py', 'tb_joint_packet_results.json']}
    assert protected['rle_region_features.v'] == REGION_SHA
    sources, frozen = [], []
    for relative, expected in PINNED.items():
        original, target = args.snapshot/relative, inputs/relative
        assert manifest['files'][relative]['sha256'] == sha(original) == expected, relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        sources.append(target)
        frozen.append({'source': str(original.resolve()), 'relative': relative,
                       'snapshot': str(target), 'sha256': expected})
    for name in ['rle_region_features.v', 'tb_joint_video_continuity.sv', 'tb_joint_video_continuity_run.py']:
        original, target = owned/name, inputs/name
        shutil.copy2(original, target)
        if name.endswith(('.v', '.sv')):
            sources.append(target)
        frozen.append({'source': str(original), 'relative': name, 'snapshot': str(target), 'sha256': sha(target)})
    source_manifest = build/'tb_joint_video_continuity_inputs.json'
    source_manifest.write_text(json.dumps({'base_commit': BASE, 'sources': frozen,
                                          'protected_existing': protected}, indent=2)+'\n', encoding='utf-8')
    tcl = build/'tb_joint_video_continuity_xsim.tcl'
    tcl.write_text('exec xvlog -sv '+' '.join(tcl_path(p) for p in sources)+' > compile.log 2>@1\n'
                   'exec xelab tb_joint_video_continuity -s video_continuity_snapshot > elaborate.log 2>@1\n'
                   'exec xsim video_continuity_snapshot -runall > simulate.log 2>@1\n', encoding='ascii')
    with (sim/'driver_stdout.log').open('wb') as log:
        process = subprocess.run([str(args.vivado), '-mode', 'batch', '-source', str(tcl),
                                  '-log', 'vivado.log', '-journal', 'vivado.jou'], cwd=sim,
                                 stdout=log, stderr=subprocess.STDOUT, timeout=240)
    assert process.returncode == 0, f'Vivado/XSim failed: {sim}'
    transcript = (sim/'simulate.log').read_text(encoding='utf-8', errors='replace')
    assert not re.search(r'^\s*(Fatal:|ERROR:)', transcript, re.M), f'Simulation failed: {sim}'
    passed = re.search(r'^JOINT_VIDEO_CONTINUITY_PASS (.+)$', transcript, re.M)
    assert passed, f'Missing terminal PASS marker: {sim}'
    counters = {key: int(value) for key, value in re.findall(r'(\w+)=(\d+)', passed.group(1))}
    assert counters['frames'] == 5 and counters['timing_checks'] == 5*1650*750
    assert counters['active_pixels'] == 5*1280*720 and counters['blanking_pixels'] == 5*(1650*750-1280*720)
    assert counters['accept_pulses'] == counters['boundary_swaps'] == 2
    assert counters['rgb_checks'] == 20 and counters['white_A'] > 0 and counters['white_C'] > 0
    assert counters['black'] > 0 and counters['background'] == 5
    assert counters['pause_frames'] >= 1 and counters['pause_active_pixels'] > 0
    assert counters['invalid_retention_pixels'] > 0 and counters['rejects'] == 1
    trace = (sim/'video_continuity_frames.log').read_text(encoding='ascii')
    assert len(re.findall(r'^FRAME_PASS ', trace, re.M)) == 5
    assert len(re.findall(r'^SWAP ', trace, re.M)) == 2
    for entry in frozen:
        assert sha(entry['source']) == sha(entry['snapshot']) == entry['sha256'], entry['relative']
    assert all(sha(owned/name) == expected for name, expected in protected.items()), 'Protected file changed'
    evidence_files = [source_manifest, tcl, sim/'compile.log', sim/'elaborate.log', sim/'simulate.log',
                      sim/'video_continuity_frames.log', sim/'vivado.log']
    result = {
        'status': 'PASS', 'source_sha256': sha(owned/'tb_joint_video_continuity.sv'),
        'testbench_sha256': sha(owned/'tb_joint_video_continuity.sv'),
        'runner_sha256': sha(Path(__file__)), 'region_sha256': REGION_SHA, 'baseline_commit': BASE,
        'build_dir': str(build), 'counts': counters,
        'timing': {'active': [1280, 720], 'total': [1650, 750],
                   'horizontal_sync_back_active_front': [40, 220, 1280, 110],
                   'vertical_sync_back_active_front': [5, 20, 720, 5],
                   'pixel_period_ns': 13.468, 'mask_period_ns': 20,
                   'sampled_input_to_output_latency_pixel_clocks': 1},
        'coverage': ['full active/hsync/vsync comparison on every pixel clock including blanking',
                     'five complete frames, each 720 lines of 1280 active pixels',
                     'old mask RGB and identity retained while producer stalls across frames',
                     'malformed tap frame_id rejected without mask_accept',
                     'two explicit accepts, both pixel-domain identities switch only on VSYNC rise',
                     'background, mask A white, mask C white and black RGB probes'],
        'displayed_id_domain': 'frame-boundary assertion uses pixel-domain core.displayed_frame_id; public mask-domain mirror checked after CDC ACK',
        'protected_existing': protected,
        'physical_hdmi_tmds_tested': False, 'board_timing_tested': False,
        'evidence': [{'path': str(p.resolve()), 'sha256': sha(p)} for p in evidence_files],
    }
    result_file = build/'tb_joint_video_continuity_results.json'
    result_file.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    (owned/result_file.name).write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status': 'PASS', 'result_path': str(result_file), 'result_sha256': sha(result_file),
                      'counts': counters, 'region_sha256': REGION_SHA}))

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'JOINT_VIDEO_CONTINUITY_FAIL: {exc}', file=sys.stderr)
        raise
