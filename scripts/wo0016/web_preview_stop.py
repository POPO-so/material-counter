"""Stop only an identified preview that owns both camera and configured port."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

PROC_ROOT = Path('/proc')


def owners(*args):
    result = subprocess.run(['fuser', *args], capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise RuntimeError('无法检查设备或端口占用')
    return {int(value) for value in result.stdout.split()}


def stop_preview(config):
    port_args = ('-n', 'tcp', str(config['board_port']))
    camera = owners('/dev/video0')
    port = owners(*port_args)
    if len(camera) != 1 or camera != port:
        raise RuntimeError('相机与端口的占用进程不一致，未执行停止')
    pid = next(iter(camera))
    proc = PROC_ROOT / str(pid)
    args = proc.joinpath('cmdline').read_bytes().split(b'\0')
    known_paths = {value.encode() for value in config['legacy_preview_paths']}
    known = any(arg in known_paths for arg in args)
    known = known or any(b'/tmp/material-web-preview.lock' in arg and
                         b'import camera_only_preview as preview' in arg for arg in args)
    if not known:
        raise RuntimeError('占用进程不是已知网页预览入口，未执行停止')
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not owners('/dev/video0') and not owners(*port_args):
            try:
                state = proc.joinpath('stat').read_text().split(') ', 1)[1][0]
            except FileNotFoundError:
                state = 'Z'
            if state == 'Z':
                return
        time.sleep(0.5)
    raise RuntimeError('旧预览未在 30 秒内退出并释放相机和端口；未强制终止')


if __name__ == '__main__':
    stop_preview(json.loads(sys.argv[1]))
