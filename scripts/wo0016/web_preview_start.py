"""Self-contained board startup payload; sent through SSH with explicit config."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

# Kept in the command line payload so the stop entry can identify this owner.
PREVIEW_LOCK = '/tmp/material-web-preview.lock'


def verify_deployment(config):
    root = Path(config['runtime_root'])
    for name in ('manifest', 'bit'):
        path = root / config[name]
        if hashlib.sha256(path.read_bytes()).hexdigest() != config[name + '_sha256']:
            raise RuntimeError(f'{name} SHA-256 与部署配置不一致，未执行自检或启动预览')


def verify_self_test(result, boot):
    if (not isinstance(result, dict) or result.get('status') != 'GRAY8_PIXEL_VERIFIED'
            or result.get('frames') != 3 or result.get('pixel_verified_frames') != 3
            or result.get('buffers_held') is not False or result.get('boot_id') != boot):
        raise RuntimeError('FPGA 自检结果不满足启动条件')


def run_self_test(config, outdir, boot, load):
    root = Path(config['runtime_root'])
    evidence = outdir / 'self-test.json'
    command = [config['python'], str(root / 'scripts/gray8/run_gray8_counter.py'),
               '--manifest', str(root / config['manifest']),
               '--manifest-sha256', config['manifest_sha256'], '--mode', 'self-test',
               '--frames', '3', '--execute', '--boot-id', boot, '--output', str(evidence)]
    if load:
        command.append('--load-overlay')
    env = os.environ.copy()
    env['XILINX_XRT'] = '/usr'
    with (outdir / 'self-test.log').open('w') as log:
        proc = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            code = proc.wait(timeout=90)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('自检尚未结束；进程及 DMA 状态已保留，需人工检查 self-test.log') from exc
    if code != 0 or not evidence.exists():
        raise RuntimeError('FPGA 自检失败，请检查 self-test.log；不要自动重试')
    verify_self_test(json.loads(evidence.read_text()), boot)


def launch_preview(config, outdir, boot):
    sys.path.insert(0, config['board_dir'])
    import camera_only_preview as preview
    preview.EXPECTED_BOOT = boot
    preview.WO_ROOT = outdir
    preview.PREVIEW_PORT = config['board_port']
    # An old installed module still enforces its historical attempt directory.
    # After a backend update both sides use web_output_name, without an attempt ID.
    name = (preview.web_output_name(boot) if hasattr(preview, 'web_output_name')
            else 'camera-preview-' + boot + '-06')
    if hasattr(preview, 'web_output_name'):
        preview.WEB_OUTPUT = True
    output = outdir / name
    return preview.main(['--expected-boot-id', boot, '--output-dir', str(output), '--calibration-mode'])


def main(config, outdir):
    verify_deployment(config)
    import fcntl
    # Hold the lock until preview.main returns. Do not close it after self-test.
    with open(PREVIEW_LOCK, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        root = Path(config['runtime_root'])
        sys.path.insert(0, str(root / 'scripts/wo0015'))
        import board_preflight as preflight
        preflight.audit_process_ownership()
        state = preflight.inspect_pynq_global_state(config['pynq_init'], require_empty_shutdown=True)
        load = state['pl_state_status'] == 'COLD_BOOT_NO_GLOBAL_STATE'
        if not load:
            preflight.inspect_pynq_global_state(
                config['pynq_init'], require_empty_shutdown=True, require_loaded_bit=True,
                expected_bit_path=root / config['bit'], expected_bit_sha256=config['bit_sha256'])
        boot_path = Path('/proc/sys/kernel/random/boot_id')
        boot = boot_path.read_text().strip()
        run_self_test(config, outdir, boot, load)
        if preflight.read_cma_meminfo()['CmaFree'] <= 32768:
            os.sync()
            Path('/proc/sys/vm/drop_caches').write_text('3\n')
            if preflight.read_cma_meminfo()['CmaFree'] <= 32768:
                raise RuntimeError('连续内存不足，无法启动相机预览')
        if boot_path.read_text().strip() != boot:
            raise RuntimeError('板卡开机状态已变化')
        subprocess.run(['v4l2-ctl', '-d', '/dev/video0',
                        '--set-fmt-video=width=640,height=400,pixelformat=MJPG', '--set-parm=60',
                        '--set-ctrl=auto_exposure=1,exposure_time_absolute=50,brightness=0'],
                       check=True, timeout=10)
        return launch_preview(config, outdir, boot)


if __name__ == '__main__':
    raise SystemExit(main(json.loads(sys.argv[1]), Path(sys.argv[2])))
