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
import re
import shlex
import subprocess
import sys
from functools import lru_cache
from pathlib import Path, PurePosixPath
import time
import webbrowser
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / 'Python脚本' / '网页部署.json'
PAYLOAD_DIR = ROOT / 'scripts' / 'wo0016'
LOCAL_URL = 'http://127.0.0.1:18765/'
RUNTIME_DIR = ROOT / '.runtime'
STARTUP_LOG = RUNTIME_DIR / 'material-counter-startup.log'
STARTUP_STATUS = RUNTIME_DIR / 'material-counter-startup-status.json'
BRIDGE_LOG = RUNTIME_DIR / 'material-counter-web-sync.log'
BRIDGE_VERSION = 5


def load_deployment_config(path=None):
    path = CONFIG_PATH if path is None else Path(path)
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('重复字段: ' + key)
            result[key] = value
        return result
    def valid_path(value, absolute):
        if not isinstance(value, str) or not value or any(c in value for c in ('\\', '\0', '\n', '\r')):
            return False
        pure = PurePosixPath(value)
        return (pure.is_absolute() == absolute and value == str(pure)
                and value not in ('/', '.') and '..' not in pure.parts)
    try:
        cfg = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_pairs)
        required = {'host', 'board_port', 'board_dir', 'runtime_root', 'python', 'pynq_init',
                    'manifest', 'manifest_sha256', 'bit', 'bit_sha256', 'legacy_preview_paths'}
        if not isinstance(cfg, dict) or set(cfg) != required:
            raise ValueError('字段缺失或含有未知字段')
        host = cfg['host']
        if (not isinstance(host, str) or len(host) > 253 or not host
                or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                       for label in host.split('.'))):
            raise ValueError('host 必须为 IPv4 地址或主机名')
        if type(cfg['board_port']) is not int or not 1 <= cfg['board_port'] <= 65535:
            raise ValueError('board_port 必须在 1 到 65535 之间')
        for key in ('board_dir', 'runtime_root', 'python', 'pynq_init'):
            if not valid_path(cfg[key], True):
                raise ValueError(key + ' 必须为规范的板端绝对路径')
        for key in ('manifest', 'bit'):
            if not valid_path(cfg[key], False):
                raise ValueError(key + ' 必须为 runtime_root 内的相对路径')
            if not isinstance(cfg[key + '_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', cfg[key + '_sha256']):
                raise ValueError(key + '_sha256 必须为 64 位小写 SHA-256')
        legacy = cfg['legacy_preview_paths']
        if not isinstance(legacy, list) or not all(valid_path(value, True) for value in legacy):
            raise ValueError('legacy_preview_paths 必须为已确认预览入口的绝对路径列表')
        return cfg
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f'部署配置无效（{path}）：{exc}') from exc


@lru_cache(maxsize=1)
def deployment_config():
    # One validated snapshot per process. A restart adopts a changed deployment.
    return load_deployment_config()


def deployment_fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def remote_payload(name):
    path = PAYLOAD_DIR / name
    code = path.read_text(encoding='utf-8')
    compile(code, str(path), 'exec')
    return code


def available():
    try:
        cfg = deployment_config()
        url = f"http://{cfg['host']}:{cfg['board_port']}/status.json"
        with build_opener(ProxyHandler({})).open(url, timeout=2) as response:
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
    cfg = deployment_config()
    code = remote_payload('web_preview_stop.py')
    import paramiko
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(cfg['host'], username='xilinx', password='xilinx', timeout=8,
                       look_for_keys=False, allow_agent=False)
        command = (shlex.quote(cfg['python']) + ' -c ' + shlex.quote(code) + ' ' +
                   shlex.quote(json.dumps(cfg)))
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
    cfg = deployment_config()
    code = remote_payload('web_preview_start.py')
    import paramiko

    command = (
        'cd ' + shlex.quote(cfg['board_dir']) + ' || exit 11; '
        'command -v fuser >/dev/null || exit 13; '
        "if fuser /dev/video0 >/dev/null 2>&1; then echo 'Camera is busy' >&2; exit 14; fi; "
        'd=$(mktemp -d /tmp/material-web-preview.XXXXXX) || exit 15; chmod 755 "$d"; '
        'nohup ' + shlex.quote(cfg['python']) + ' -u -c ' + shlex.quote(code) + ' ' +
        shlex.quote(json.dumps(cfg)) +
        ' "$d" >"$d/viewer.log" 2>&1 </dev/null & echo "$d"'
    )
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(cfg['host'], username='xilinx', password='xilinx', timeout=8,
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



PAGE = ROOT / 'scripts' / 'wo0016' / 'dashboard.html'
MODULES = ('camera_only_preview.py', 'mjpeg_preview.py', 'image_debug.py',
           'dashboard_state.py', 'live_monitor_state.py', 'resource_monitor.py',
           'live_calibration.py', 'stream_counter.py', 'lighting_profile.py',
           'scale_parameters.py', 'split_touching.py', 'binary_stats.py', 'packet_encoder.py', 'packet_codec.py')
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


def make_bridge(board_host, update, port=BRIDGE_PORT, board_port=8080, deployment_digest=None):
    if deployment_digest is None:
        deployment_digest = deployment_fingerprint(deployment_config())
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
                self.reply(200, {'version': BRIDGE_VERSION, 'deployment_sha256': deployment_digest})
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
    cfg = deployment_config()
    # Validate both remote payloads before stopping a running preview.
    remote_payload('web_preview_start.py')
    remote_payload('web_preview_stop.py')
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
            command = ('cd ' + shlex.quote(stage) + ' && PYTHONDONTWRITEBYTECODE=1 ' +
                       shlex.quote(cfg['python']) + ' -c ' +
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
            command = ('cd ' + shlex.quote(stage) + ' && PYTHONDONTWRITEBYTECODE=1 ' +
                       shlex.quote(cfg['python']) + ' -c ' +
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
    cfg = deployment_config()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_bool
    mutex = kernel.CreateMutexW(None, False, 'Local\\MaterialCounterWebSync')
    if not mutex:
        raise OSError('无法创建网页服务互斥锁')
    bridge = None
    try:
        if ctypes.get_last_error() == 183:
            return
        bridge = make_bridge(cfg['host'], lambda report: deploy(cfg['host'], cfg['board_dir'], report),
                             board_port=cfg['board_port'], deployment_digest=deployment_fingerprint(cfg))
        bridge.serve_forever()
    finally:
        if bridge is not None:
            bridge.server_close()
        kernel.CloseHandle(mutex)


def start_web_sync():
    pythonw = ROOT / '.venv' / 'Scripts' / 'pythonw.exe'
    subprocess.Popen([str(pythonw), str(Path(__file__).resolve()), '--bridge'],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def wait_for_web_sync(timeout=15):
    expected = hashlib.sha256(PAGE.read_bytes()).hexdigest()
    expected_config = deployment_fingerprint(deployment_config())
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(LOCAL_URL + 'api/bridge-version', timeout=1) as response:
                identity = json.load(response)
                version = identity.get('version')
            with opener.open(LOCAL_URL + 'api/page-version', timeout=1) as response:
                page_hash = json.load(response).get('sha256')
            if version != BRIDGE_VERSION or identity.get('deployment_sha256') != expected_config:
                raise RuntimeError('已有网页桥接使用旧版本或不同部署配置，请退出旧桥接进程后重新双击启动。')
            if page_hash == expected:
                return
        except (OSError, ValueError, AttributeError):
            pass
        time.sleep(0.2)
    raise RuntimeError(f'本机网页服务未就绪，请检查 18765 端口及日志 {BRIDGE_LOG}。')


def set_startup_status(phase, message):
    STARTUP_STATUS.parent.mkdir(parents=True, exist_ok=True)
    temporary = STARTUP_STATUS.with_name(STARTUP_STATUS.name + '.' + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps({'phase': phase, 'message': message}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(STARTUP_STATUS)
    finally:
        temporary.unlink(missing_ok=True)


def default_browser_executable():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice') as key:
            progid = winreg.QueryValueEx(key, 'ProgId')[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r'\shell\open\command') as key:
            command = os.path.expandvars(winreg.QueryValueEx(key, '')[0]).strip()
        match = re.match(r'^"([^"]+)"|^(\S+)', command)
        executable = Path(match.group(1) or match.group(2)) if match else None
        return executable if executable and executable.is_file() else None
    except OSError:
        return None


def _browser_window_title(executable):
    """返回浏览器全部可见窗口的标题（换行连接）；无窗口或检测不可用时返回 ''。"""
    if executable is None or sys.platform != 'win32':
        return ''
    script = ("$t = Get-Process -Name '" + executable.stem + "' -ErrorAction SilentlyContinue | "
              "Where-Object {$_.MainWindowTitle} | ForEach-Object {$_.MainWindowTitle}; "
              "$t -join \"`n\"")
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', script],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.stdout.decode('utf-8', errors='replace').strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def _wait_for_browser_window(executable, timeout=10, title_hint=None):
    """等待浏览器出现可见窗口；检测不可用时保守返回 True。

    title_hint 非空时要求窗口标题包含该提示（页面 <title>），排除崩溃恢复
    对话框等没有页面内容的窗口被误判为成功。
    """
    if executable is None or sys.platform != 'win32':
        return True
    process_name = executable.stem
    script = (
        '$deadline = (Get-Date).AddSeconds(' + str(timeout) + '); '
        'while ((Get-Date) -lt $deadline) { '
        "$w = Get-Process -Name '" + process_name + "' -ErrorAction SilentlyContinue | "
        'Where-Object {$_.MainWindowTitle}; '
        'if ($w) { exit 0 }; '
        'Start-Sleep -Milliseconds 500 }; '
        'exit 1'
    )
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', script],
            capture_output=True, timeout=timeout + 20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode != 0:
            return False
        if title_hint is None:
            return True
        deadline = time.monotonic() + max(timeout, 10)
        while time.monotonic() < deadline:
            title = _browser_window_title(executable)
            if title_hint in title:
                return True
            time.sleep(1)
        return False
    except (OSError, subprocess.SubprocessError):
        return True


def _browser_process_count(executable):
    """返回浏览器当前进程数；检测不可用时返回 None。"""
    if executable is None or sys.platform != 'win32':
        return None
    script = "$p = @(Get-Process -Name '" + executable.stem + "' -ErrorAction SilentlyContinue); $p.Count"
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', script],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _wait_for_browser_exit(executable, timeout=15):
    """等待浏览器进程全部退出以释放配置目录单例锁；超时后放行。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        count = _browser_process_count(executable)
        if count == 0:
            return True
        time.sleep(1)
    return False


def _terminate_browser_processes(executable):
    """强制结束浏览器所有进程以释放坏死的后台单例。"""
    if executable is None:
        return
    try:
        subprocess.run(
            ['taskkill', '/F', '/IM', executable.name],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        pass


def _launch_dedicated_profile(executable, url):
    """用独立配置目录直接启动浏览器，绕开主配置目录的锁竞争与崩溃恢复提示。"""
    profile = RUNTIME_DIR / 'browser-profile'
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [str(executable), '--user-data-dir=' + str(profile), '--no-first-run',
         '--no-default-browser-check', '--hide-crash-restore-bubble', '--new-window', url],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def open_browser(url, report=lambda message: None):
    """用默认浏览器打开页面；浏览器后台单例坏死时自动恢复。

    失败模式：浏览器“启动增强/后台运行”进程持有配置目录单例锁但无法开窗，
    新启动的浏览器进程为避免配置损坏而静默退出（无报错）。恢复方式：先试
    常规启动；无窗口则（在确认浏览器当前无任何可见窗口、不丢失用户页面的
    前提下）结束浏览器进程释放锁，改用独立配置目录直接启动——全新目录无
    锁竞争、无崩溃恢复对话框，冷启动实测可靠。窗口标题未含页面标题时视为
    未成功（排除崩溃恢复对话框被误判）。每步结果记入启动日志。
    """
    executable = default_browser_executable()
    if not executable:
        return webbrowser.open(url)
    arguments = subprocess.list2cmdline([url])
    title_hint = _page_title(url)
    # 通过 Windows Shell 启动，避免直接子进程继承受限的浏览器配置目录访问环境。
    os.startfile(str(executable), 'open', arguments)
    if _wait_for_browser_window(executable, title_hint=title_hint):
        return True
    report('浏览器未响应，正在重启浏览器…')
    set_startup_status('starting', '浏览器后台进程未响应，正在重启浏览器…')
    log_startup('浏览器恢复：窗口未出现，改用独立配置目录启动')
    # 仅当浏览器当前没有任何可见窗口时才清理进程，避免关掉用户正开着的页面。
    if _browser_window_title(executable) == '':
        _terminate_browser_processes(executable)
        _wait_for_browser_exit(executable)
    else:
        log_startup('浏览器恢复：存在可见窗口，跳过进程清理，直接独立配置启动')
    try:
        _launch_dedicated_profile(executable, url)
    except (OSError, subprocess.SubprocessError) as exc:
        log_startup(f'浏览器恢复：独立配置启动失败 {exc}')
        return False
    return _wait_for_browser_window(executable, timeout=20, title_hint=title_hint)


def _page_title(url):
    """获取要打开页面的标题，用于浏览器窗口成功的判定。"""
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            match = re.search(r'<title>(.*?)</title>', response.read().decode('utf-8', errors='replace'), re.S)
            return match.group(1).strip() if match else None
    except (OSError, ValueError):
        return None


def main(report=lambda message: None):
    deployment_config()
    set_startup_status('starting', '正在启动网页服务…')
    report('正在启动本机网页服务…')
    start_web_sync()
    wait_for_web_sync()
    report('正在打开网页…')
    if not open_browser(LOCAL_URL, report):
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


def log_startup(message):
    STARTUP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with STARTUP_LOG.open('a', encoding='utf-8') as stream:
        stream.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} pid={os.getpid()} {message}\n')


if __name__ == '__main__':
    log_startup(f'入口启动 exe={sys.executable} prefix={sys.prefix} args={sys.argv[1:]}')
    if '--bridge' in sys.argv[1:]:
        try:
            run_bridge()
        except Exception:
            BRIDGE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with BRIDGE_LOG.open('a', encoding='utf-8') as stream:
                traceback.print_exc(file=stream)
    else:
        try:
            pythonw = ROOT / '.venv' / 'Scripts' / 'pythonw.exe'
            if not pythonw.is_file():
                raise RuntimeError('未找到项目 Python 环境：' + str(pythonw))
            if Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
                log_startup('切换到项目 Python 环境')
                subprocess.Popen([str(pythonw), str(Path(__file__).resolve())],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            else:
                log_startup('执行网页启动流程')
                run_silently()
                log_startup('网页启动流程结束')
        except Exception:
            STARTUP_LOG.parent.mkdir(parents=True, exist_ok=True)
            with STARTUP_LOG.open('a', encoding='utf-8') as stream:
                traceback.print_exc(file=stream)
