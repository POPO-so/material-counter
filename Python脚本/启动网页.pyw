"""双击启动并打开现有板端相机预览。"""
import ctypes
import http.client
import os
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
import time
import webbrowser
from urllib.request import ProxyHandler, build_opener

HOST = '192.168.2.99'
URL = f'http://{HOST}:8080/'
LOCAL_URL = 'http://127.0.0.1:18765/'
STARTUP_LOG = Path(tempfile.gettempdir()) / 'material-counter-startup.log'
STARTUP_STATUS = Path(tempfile.gettempdir()) / 'material-counter-startup-status.json'
BOARD_DIR = '/home/xilinx/material-web-resources-20261027'

# 使用已部署的预览模块，并为每次启动分配独立目录。
BOARD_CODE = r'''import fcntl, json, os, pathlib, subprocess, sys, time

root = pathlib.Path('/home/xilinx/gray8-dma-20261027')
manifest = root / 'export/manifest.json'
manifest_hash = 'b81e6ea09cba48d643b3f09ae88b9ea784176c81ebc53799f1af38aedb0cd484'
bit = root / 'export/gray8.bit'
bit_hash = 'feadeae19058e23c6bfb443e4f25aade48a90d19c8b64bbbc7fe3219ebcea926'
outdir = pathlib.Path(sys.argv[1])
lock = open('/tmp/material-web-preview.lock', 'a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
sys.path.insert(0, str(root / 'scripts/wo0015'))
import board_preflight as preflight
preflight.audit_process_ownership()
state = preflight.inspect_pynq_global_state(
    '/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq/__init__.py',
    require_empty_shutdown=True)
load = state['pl_state_status'] == 'COLD_BOOT_NO_GLOBAL_STATE'
if not load:
    preflight.inspect_pynq_global_state(
        '/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq/__init__.py',
        require_empty_shutdown=True, require_loaded_bit=True,
        expected_bit_path=bit, expected_bit_sha256=bit_hash)
boot = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
evidence = outdir / 'self-test.json'
cmd = ['/usr/local/share/pynq-venv/bin/python3',
       str(root / 'scripts/gray8/run_gray8_counter.py'), '--manifest', str(manifest),
       '--manifest-sha256', manifest_hash, '--mode', 'self-test', '--frames', '3',
       '--execute', '--boot-id', boot, '--output', str(evidence)]
if load:
    cmd.append('--load-overlay')
env = os.environ.copy()
env['XILINX_XRT'] = '/usr'
with (outdir / 'self-test.log').open('w') as log:
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 90
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(1)
    if proc.poll() is None:
        raise RuntimeError('自检尚未结束；进程及 DMA 状态已保留，需人工检查 self-test.log')
if proc.returncode != 0 or not evidence.exists():
    raise RuntimeError('FPGA 自检失败，请检查 self-test.log；不要自动重试')
result = json.loads(evidence.read_text())
if (result.get('status') != 'GRAY8_PIXEL_VERIFIED' or result.get('frames') != 3
        or result.get('pixel_verified_frames') != 3 or result.get('buffers_held') is not False
        or result.get('boot_id') != boot):
    raise RuntimeError('FPGA 自检结果不满足启动条件')
if preflight.read_cma_meminfo()['CmaFree'] <= 32768:
    os.sync()
    pathlib.Path('/proc/sys/vm/drop_caches').write_text('3\n')
    if preflight.read_cma_meminfo()['CmaFree'] <= 32768:
        raise RuntimeError('连续内存不足，无法启动相机预览')
if pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip() != boot:
    raise RuntimeError('板卡开机状态已变化')
subprocess.run(['v4l2-ctl', '-d', '/dev/video0',
                '--set-fmt-video=width=640,height=400,pixelformat=MJPG',
                '--set-parm=60',
                '--set-ctrl=auto_exposure=1,exposure_time_absolute=50,brightness=0'],
               check=True, timeout=10)
sys.path.insert(0, os.getcwd())
import camera_only_preview as preview
preview.EXPECTED_BOOT = boot
preview.WO_ROOT = outdir
output = outdir / ('camera-preview-' + boot + '-06')
raise SystemExit(preview.main(['--expected-boot-id', boot, '--output-dir', str(output), '--calibration-mode']))
'''

# 只停止已知预览入口；无法确认持有者时保留现场，不使用强制终止。
STOP_CODE = r'''import os, pathlib, signal, subprocess, time
def owners(*args):
    result = subprocess.run(['fuser', *args], capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise RuntimeError('无法检查设备或端口占用')
    return {int(x) for x in result.stdout.split()}
camera = owners('/dev/video0')
port = owners('-n', 'tcp', '8080')
if len(camera) != 1 or camera != port:
    raise RuntimeError('相机与端口的占用进程不一致，未执行停止')
pid = next(iter(camera))
proc = pathlib.Path('/proc') / str(pid)
args = proc.joinpath('cmdline').read_bytes().split(b'\0')
known_paths = {b'/home/xilinx/web-preview-20261027-124938/launch_viewer.py',
               b'/home/xilinx/image-debug-660088b71d/launch.py'}
known = any(arg in known_paths for arg in args)
known = known or any(b'/tmp/material-web-preview.lock' in arg and
                     b'import camera_only_preview as preview' in arg for arg in args)
if not known:
    raise RuntimeError('占用进程不是已知网页预览入口，未执行停止')
os.kill(pid, signal.SIGTERM)
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    if not owners('/dev/video0') and not owners('-n', 'tcp', '8080'):
        try:
            state = proc.joinpath('stat').read_text().split(') ', 1)[1][0]
        except FileNotFoundError:
            state = 'Z'
        if state == 'Z':
            break
    time.sleep(0.5)
else:
    raise RuntimeError('旧预览未在 30 秒内退出并释放相机和端口；未强制终止')
'''


def available():
    try:
        with build_opener(ProxyHandler({})).open(URL + 'status.json', timeout=2) as response:
            state = json.load(response)
        if not isinstance(state, dict):
            return False
        telemetry = state.get('telemetry', {})
        return (isinstance(telemetry, dict) and telemetry.get('source') == 'software'
                and telemetry.get('dma') == 'disabled' and telemetry.get('fpga') == 'disabled'
                and telemetry.get('frames_processed', 0) > 0 and telemetry.get('stale') is False)
    except (OSError, ValueError):
        return False


def stop_board():
    import paramiko
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(HOST, username='xilinx', password='xilinx', timeout=8,
                       look_for_keys=False, allow_agent=False)
        command = '/usr/local/share/pynq-venv/bin/python3 -c ' + shlex.quote(STOP_CODE)
        stdin, stdout, stderr = client.exec_command('sudo -S -p "" sh -c ' + shlex.quote(command), timeout=40)
        stdin.write('xilinx\n')
        stdin.flush()
        stdout.read()
        error = stderr.read().decode('utf-8', errors='replace').strip()
        if stdout.channel.recv_exit_status():
            raise RuntimeError('停止旧图传失败：' + (error or '请检查板端进程与设备占用'))
        return True
    finally:
        client.close()


def start_board():
    import paramiko

    command = (
        'cd ' + shlex.quote(BOARD_DIR) + ' || exit 11; '
        'command -v fuser >/dev/null || exit 13; '
        "if fuser /dev/video0 >/dev/null 2>&1; then echo 'Camera is busy' >&2; exit 14; fi; "
        'd=$(mktemp -d /tmp/material-web-preview.XXXXXX) || exit 15; chmod 755 "$d"; '
        'nohup /usr/local/share/pynq-venv/bin/python3 -u -c ' + shlex.quote(BOARD_CODE) +
        ' "$d" >"$d/viewer.log" 2>&1 </dev/null & echo "$d"'
    )
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(HOST, username='xilinx', password='xilinx', timeout=8,
                       look_for_keys=False, allow_agent=False)
        stdin, stdout, stderr = client.exec_command('sudo -S -p "" sh -c ' + shlex.quote(command), timeout=20)
        stdin.write('xilinx\n')
        stdin.flush()
        log_dir = stdout.read().decode('utf-8', errors='replace').strip()
        error = stderr.read().decode('utf-8', errors='replace').strip()
        code = stdout.channel.recv_exit_status()
        if code:
            raise RuntimeError(f'板端启动失败（退出码 {code}）：{error or "请检查相机和部署文件"}')
        if not log_dir.startswith('/tmp/material-web-preview.'):
            raise RuntimeError('板端未返回预览日志目录。')
        return log_dir
    finally:
        client.close()



ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / 'scripts' / 'wo0016' / 'dashboard.html'
MODULES = ('camera_only_preview.py', 'mjpeg_preview.py', 'image_debug.py',
           'dashboard_state.py', 'live_monitor_state.py', 'resource_monitor.py',
           'live_calibration.py', 'stream_counter.py', 'lighting_profile.py',
           'scale_parameters.py', 'split_touching.py', 'packet_encoder.py', 'packet_codec.py')
BRIDGE_PORT = 18765


def wait_for_preview(launcher, sftp, log_dir, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with sftp.open(log_dir + '/viewer.log', 'rb') as stream:
                log = stream.read().decode('utf-8', errors='replace')
            if 'Traceback (most recent call last)' in log:
                raise RuntimeError('新图传启动失败，请检查板端日志 ' + log_dir + '/viewer.log；未自动重试')
            if 'CAMERA_HUD_PREVIEW_READY' in log and launcher['available']():
                return
        except FileNotFoundError:
            pass
        time.sleep(1)
    raise RuntimeError('新图传未就绪，请检查板端日志 ' + log_dir + '/viewer.log；未自动重试')


def make_bridge(board_host, update, port=BRIDGE_PORT, board_port=8080):
    lock = threading.Lock()
    state = {'busy': False, 'message': '准备就绪', 'success': None}

    def report(message):
        with lock:
            state['message'] = message

    def work():
        try:
            update(report)
        except Exception as exc:
            with lock:
                state.update(busy=False, message=str(exc), success=False)
        else:
            with lock:
                state.update(busy=False, message='后端更新完成，图传已恢复', success=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed(self):
            return (self.headers.get('Origin') == 'http://127.0.0.1:' + str(self.server.server_port) and
                    self.headers.get('Host') == '127.0.0.1:' + str(self.server.server_port))

        def local_host(self):
            return self.headers.get('Host') == '127.0.0.1:' + str(self.server.server_port)

        def reply(self, code, value):
            body = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            if self.allowed():
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
                self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Private-Network', 'true')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.reply(200 if self.allowed() else 403, {})

        def do_GET(self):
            if not self.local_host():
                self.reply(403, {'error': 'forbidden'})
                return
            if self.path == '/api/bridge-version':
                self.reply(200, {'version': 3})
                return
            if self.path == '/api/startup-status':
                try:
                    value = json.loads(STARTUP_STATUS.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    value = {'phase': 'idle', 'message': ''}
                self.reply(200, value)
                return
            if self.path == '/status':
                with lock:
                    snapshot = dict(state)
                self.reply(200, snapshot)
                return
            if self.path in ('/', '/index.html', '/api/page-version'):
                try:
                    page = PAGE.read_bytes()
                except OSError:
                    self.reply(503, {'error': 'page unavailable'})
                    return
                version = hashlib.sha256(page).hexdigest()
                if self.path == '/api/page-version':
                    self.reply(200, {'sha256': version})
                    return
                page = page.replace(b'<head>', b'<head><meta name="dashboard-version" content="' + version.encode('ascii') + b'">', 1)
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            if self.path in ('/status.json', '/stream.mjpg', '/binary.mjpg', '/api/debug-snapshot'):
                self.proxy('GET')
                return
            self.reply(404, {'error': 'not found'})

        def proxy(self, method, body=None):
            connection = http.client.HTTPConnection(board_host, board_port, timeout=8)
            started = False
            headers = {'Host': board_host + ':' + str(board_port)}
            if method == 'POST':
                headers.update({'Content-Type': 'application/json', 'Origin': 'http://' + board_host + ':' + str(board_port)})
            try:
                connection.request(method, self.path, body=body, headers=headers)
                response = connection.getresponse()
                self.send_response(response.status)
                for header in ('Content-Type', 'Content-Length', 'Cache-Control', 'Pragma'):
                    value = response.getheader(header)
                    if value is not None:
                        self.send_header(header, value)
                self.send_header('Connection', 'close')
                self.end_headers()
                started = True
                if self.path in ('/stream.mjpg', '/binary.mjpg'):
                    while True:
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                else:
                    self.wfile.write(response.read())
            except (OSError, http.client.HTTPException):
                if not started:
                    self.reply(503, {'error': 'board unavailable'})
                self.close_connection = True
            finally:
                connection.close()

        def do_POST(self):
            if not self.allowed():
                self.reply(403, {'error': 'forbidden'})
                return
            if self.path in ('/api/calibration', '/api/debug-mode', '/api/resource-monitor', '/api/reset-count', '/api/view-mode', '/api/stop'):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    length = 0
                if self.headers.get_content_type() != 'application/json' or not 0 < length <= 512:
                    self.reply(400, {'error': 'invalid request'})
                    return
                self.proxy('POST', self.rfile.read(length))
                return
            if self.path != '/update':
                self.reply(404, {'error': 'not found'})
                return
            if self.headers.get_content_type() != 'application/json' or self.headers.get('Content-Length') != '2' or self.rfile.read(2) != b'{}':
                self.reply(400, {'error': 'invalid request'})
                return
            with lock:
                if state['busy']:
                    self.reply(409, {'error': 'update already running'})
                    return
                state.update(busy=True, message='正在检查本机代码', success=None)
            threading.Thread(target=work, daemon=True).start()
            self.reply(202, {'accepted': True})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def deploy(host, board_dir, report):
    import paramiko
    source = ROOT / 'scripts' / 'wo0016'
    contents = {}
    for name in MODULES:
        path = ROOT / 'scripts' / 'gray8' / name if name == 'packet_codec.py' else source / name
        data = path.read_bytes()
        compile(data, name, 'exec')
        contents[name] = data

    launcher = {'available': available, 'stop_board': stop_board, 'start_board': start_board}
    if not launcher['available']():
        raise RuntimeError('当前图传未就绪，未进行更新；请先检查板端状态')
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    stage = board_dir + '/.backend-stage-' + uuid.uuid4().hex
    previous = board_dir + '/.backend-previous'
    stopped = False
    stage_created = False
    replaced = []
    originals = {}
    try:
        report('正在上传并检查板端代码')
        client.connect(host, username='xilinx', password='xilinx', timeout=8,
                       banner_timeout=8, auth_timeout=8, look_for_keys=False, allow_agent=False)
        with client.open_sftp() as sftp:
            sftp.mkdir(stage, 0o755)
            stage_created = True
            for name, data in contents.items():
                with sftp.open(stage + '/' + name, 'wb') as remote:
                    remote.write(data)
                with sftp.open(stage + '/' + name, 'rb') as remote:
                    if hashlib.sha256(remote.read()).digest() != hashlib.sha256(data).digest():
                        raise RuntimeError('上传校验失败：' + name)
            command = ('cd ' + shlex.quote(stage) + ' && PYTHONDONTWRITEBYTECODE=1 '
                       '/usr/local/share/pynq-venv/bin/python3 -c ' +
                       shlex.quote("import pathlib; [compile(p.read_bytes(), str(p), 'exec') for p in pathlib.Path('.').glob('*.py')]; print('OK')"))
            _, stdout, stderr = client.exec_command(command, timeout=45)
            result = stdout.read().decode('utf-8', errors='replace')
            error = stderr.read().decode('utf-8', errors='replace')
            if stdout.channel.recv_exit_status() or 'OK' not in result:
                raise RuntimeError('板端代码检查失败，旧服务仍在运行：' + error[-600:])
            try:
                sftp.mkdir(previous, 0o755)
            except OSError:
                if not sftp.stat(previous):
                    raise
            for name in MODULES:
                try:
                    with sftp.open(board_dir + '/' + name, 'rb') as old:
                        originals[name] = old.read()
                except FileNotFoundError:
                    originals[name] = None
                if originals[name] is not None:
                    with sftp.open(previous + '/' + name, 'wb') as backup:
                        backup.write(originals[name])
            report('正在停止旧图传并切换代码')
            launcher['stop_board']()
            stopped = True
            report('正在检查板端模块依赖')
            command = ('cd ' + shlex.quote(stage) + ' && PYTHONDONTWRITEBYTECODE=1 '
                       '/usr/local/share/pynq-venv/bin/python3 -c ' +
                       shlex.quote("import camera_only_preview; print('OK')"))
            _, stdout, stderr = client.exec_command(command, timeout=45)
            result = stdout.read().decode('utf-8', errors='replace')
            error = stderr.read().decode('utf-8', errors='replace')
            if stdout.channel.recv_exit_status() or 'OK' not in result:
                raise RuntimeError('板端依赖检查失败，旧代码未替换：' + error[-600:])
            try:
                for name in MODULES:
                    sftp.posix_rename(stage + '/' + name, board_dir + '/' + name)
                    replaced.append(name)
            except Exception:
                for name in replaced:
                    target = board_dir + '/' + name
                    if originals[name] is None:
                        sftp.remove(target)
                    else:
                        temp = target + '.rollback'
                        with sftp.open(temp, 'wb') as out:
                            out.write(originals[name])
                        sftp.posix_rename(temp, target)
                raise RuntimeError('切换代码失败；已恢复旧文件，需重新启动图传')
            try:
                sftp.rmdir(stage)
            except OSError:
                pass
        report('正在启动并验证图传（包含板端自检）')
        log_dir = launcher['start_board']()
        with client.open_sftp() as sftp:
            wait_for_preview(launcher, sftp, log_dir)
    except Exception as exc:
        if stopped and not replaced:
            raise RuntimeError(str(exc) + '；旧服务已停止，请检查后重新启动') from exc
        raise
    finally:
        if stage_created:
            try:
                with client.open_sftp() as cleanup:
                    for name in MODULES:
                        try:
                            cleanup.remove(stage + '/' + name)
                        except FileNotFoundError:
                            pass
                    try:
                        cleanup.rmdir(stage)
                    except FileNotFoundError:
                        pass
            except OSError:
                pass
        client.close()


def run_bridge():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel.CreateMutexW(None, False, 'Local\\MaterialCounterWebSync')
    if not mutex:
        raise OSError('无法创建网页服务互斥锁')
    if ctypes.get_last_error() == 183:
        return
    bridge = make_bridge(HOST, lambda report: deploy(HOST, BOARD_DIR, report))
    bridge.serve_forever()


def start_web_sync():
    pythonw = ROOT / '.venv' / 'Scripts' / 'pythonw.exe'
    subprocess.Popen([str(pythonw), str(Path(__file__).resolve()), '--bridge'],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def wait_for_web_sync(timeout=15):
    expected = hashlib.sha256(PAGE.read_bytes()).hexdigest()
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(LOCAL_URL + 'api/bridge-version', timeout=1) as response:
                version = json.load(response).get('version')
            with opener.open(LOCAL_URL + 'api/page-version', timeout=1) as response:
                page_hash = json.load(response).get('sha256')
            if version == 3 and page_hash == expected:
                return
        except (OSError, ValueError, AttributeError):
            pass
        time.sleep(0.2)
    log = Path(tempfile.gettempdir()) / 'material-counter-web-sync.log'
    raise RuntimeError(f'本机网页服务未就绪，请检查 18765 端口及日志 {log}。')


def set_startup_status(phase, message):
    temporary = STARTUP_STATUS.with_name(STARTUP_STATUS.name + '.' + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps({'phase': phase, 'message': message}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(STARTUP_STATUS)
    finally:
        temporary.unlink(missing_ok=True)


def main(report=lambda message: None):
    set_startup_status('starting', '正在启动网页服务…')
    report('正在启动本机网页服务…')
    start_web_sync()
    wait_for_web_sync()
    report('正在打开网页…')
    if not webbrowser.open(LOCAL_URL):
        raise RuntimeError('无法打开浏览器，请手动访问：' + LOCAL_URL)
    report('网页已就绪')
    set_startup_status('starting', '正在检查板卡连接与相机预览…')
    if not available():
        set_startup_status('starting', '正在启动板端预览和自检，可能需要两分钟…')
        log_dir = start_board()
        for _ in range(120):
            if available():
                break
            time.sleep(1)
        else:
            raise RuntimeError(f'板端图传未就绪。请检查相机及板端日志 {log_dir}/viewer.log。')
    set_startup_status('ready', '相机预览已就绪')


def run_silently():
    try:
        main()
    except Exception as exc:
        set_startup_status('error', str(exc))
        with STARTUP_LOG.open('a', encoding='utf-8') as stream:
            traceback.print_exc(file=stream)


if __name__ == '__main__':
    if '--bridge' in sys.argv[1:]:
        try:
            run_bridge()
        except Exception:
            with (Path(tempfile.gettempdir()) / 'material-counter-web-sync.log').open('a', encoding='utf-8') as stream:
                traceback.print_exc(file=stream)
    else:
        try:
            pythonw = ROOT / '.venv' / 'Scripts' / 'pythonw.exe'
            if not pythonw.is_file():
                raise RuntimeError('未找到项目 Python 环境：' + str(pythonw))
            if Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
                subprocess.Popen([str(pythonw), str(Path(__file__).resolve())],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            else:
                run_silently()
        except Exception:
            with STARTUP_LOG.open('a', encoding='utf-8') as stream:
                traceback.print_exc(file=stream)
