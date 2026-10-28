"""Deployment validation and remote workflow tests; no board access."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def launcher():
    path = ROOT / 'Python脚本' / '启动网页.pyw'
    module = types.ModuleType('deployment_launcher')
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), module.__dict__)
    return module


def remote(name):
    path = Path(__file__).with_name(name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checked_in_config_preserves_current_hash_pins():
    cfg = launcher().load_deployment_config()
    assert cfg['host'] == '192.168.2.99'
    assert cfg['manifest_sha256'] == 'b81e6ea09cba48d643b3f09ae88b9ea784176c81ebc53799f1af38aedb0cd484'
    assert cfg['bit_sha256'] == 'feadeae19058e23c6bfb443e4f25aade48a90d19c8b64bbbc7fe3219ebcea926'


@pytest.mark.parametrize('key,value', [
    ('host', 'board; touch /tmp/no'), ('board_port', True), ('board_port', 0),
    ('board_port', 65536), ('runtime_root', 'relative'), ('board_dir', '/'),
    ('python', '/usr/../python'), ('manifest', '../manifest.json'),
    ('bit', '/tmp/other.bit'), ('bit_sha256', 'f' * 63),
    ('manifest_sha256', 'not-a-hash'), ('legacy_preview_paths', ['/']),
    ('unexpected', 'typo'),
])
def test_bad_config_is_rejected_before_any_network(key, value, tmp_path):
    module = launcher()
    cfg = module.load_deployment_config()
    cfg[key] = value
    path = tmp_path / 'deployment.json'
    path.write_text(json.dumps(cfg), encoding='utf-8')
    with pytest.raises(RuntimeError, match='部署配置'):
        module.load_deployment_config(path)


@pytest.mark.parametrize('text', ['{}', '[]', '{bad', '{"host":"a","host":"b"}'])
def test_missing_malformed_or_duplicate_config_is_clear(text, tmp_path):
    path = tmp_path / 'deployment.json'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(RuntimeError, match='部署配置'):
        launcher().load_deployment_config(path)


def test_missing_config_is_clear(tmp_path):
    with pytest.raises(RuntimeError, match='部署配置'):
        launcher().load_deployment_config(tmp_path / 'missing.json')


def test_config_changes_alter_bridge_fingerprint():
    module = launcher()
    cfg = module.load_deployment_config()
    digest = module.deployment_fingerprint(cfg)
    cfg['host'] = '192.168.2.100'
    assert module.deployment_fingerprint(cfg) != digest


@pytest.mark.parametrize('changed', ['manifest', 'bit'])
def test_remote_hash_mismatch_fails_before_preflight_or_dma(changed, tmp_path):
    module = remote('web_preview_start')
    cfg = launcher().load_deployment_config()
    cfg['runtime_root'] = str(tmp_path)
    (tmp_path / 'export').mkdir()
    (tmp_path / cfg['manifest']).write_bytes(b'changed manifest')
    (tmp_path / cfg['bit']).write_bytes(b'changed bit')
    for key in ('manifest', 'bit'):
        cfg[key + '_sha256'] = module.hashlib.sha256((tmp_path / cfg[key]).read_bytes()).hexdigest()
    cfg[changed + '_sha256'] = '0' * 64
    with pytest.raises(RuntimeError, match='SHA-256'):
        module.verify_deployment(cfg)


def verified_result():
    return dict(status='GRAY8_PIXEL_VERIFIED', mode='self-test', frames=3,
                pixel_verified_frames=3, buffers_held=False, boot_id='boot-a')


@pytest.mark.parametrize('key,value', [
    ('status', 'FAIL_HOLD'), ('frames', 2), ('pixel_verified_frames', 2),
    ('buffers_held', True), ('buffers_held', 0), ('boot_id', 'boot-b'),
])
def test_remote_rejects_unverified_self_test(key, value):
    module = remote('web_preview_start')
    result = verified_result()
    result[key] = value
    with pytest.raises(RuntimeError, match='自检结果'):
        module.verify_self_test(result, 'boot-a')


def test_self_test_timeout_preserves_process_without_termination(tmp_path, monkeypatch):
    module = remote('web_preview_start')
    class Process:
        def wait(self, timeout):
            raise module.subprocess.TimeoutExpired('self-test', timeout)
        def kill(self):
            pytest.fail('uncertain DMA process must not be killed')
        terminate = kill
    commands = []
    def spawn(command, **kwargs):
        commands.append(command)
        return Process()
    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    cfg = launcher().load_deployment_config()
    cfg['python'] = '/custom/python3'
    with pytest.raises(RuntimeError, match='状态已保留'):
        module.run_self_test(cfg, tmp_path, 'boot-a', True)
    assert commands[0][0] == '/custom/python3'
    assert '--load-overlay' in commands[0]


@pytest.mark.parametrize('camera,port,command', [
    ({31}, {32}, b'known'), ({31, 32}, {31, 32}, b'known'),
    ({31}, {31}, b'/unknown/program\0'),
])
def test_stop_never_signals_unknown_or_mismatched_owner(camera, port, command, tmp_path, monkeypatch):
    module = remote('web_preview_stop')
    cfg = launcher().load_deployment_config()
    proc = tmp_path / '31'
    proc.mkdir()
    (proc / 'cmdline').write_bytes(command)
    monkeypatch.setattr(module, 'PROC_ROOT', tmp_path)
    monkeypatch.setattr(module, 'owners', lambda *args: camera if args[0] == '/dev/video0' else port)
    monkeypatch.setattr(module.os, 'kill', lambda *args: pytest.fail('must preserve unknown owner'))
    with pytest.raises(RuntimeError):
        module.stop_preview(cfg)


def test_stop_known_owner_uses_configured_port_and_waits_for_release(tmp_path, monkeypatch):
    module = remote('web_preview_stop')
    cfg = launcher().load_deployment_config()
    cfg['board_port'] = 8099
    proc = tmp_path / '31'
    proc.mkdir()
    (proc / 'cmdline').write_bytes(cfg['legacy_preview_paths'][0].encode() + b'\0')
    (proc / 'stat').write_text('31 (preview) Z 0')
    calls = []
    signalled = []
    def owners(*args):
        calls.append(args)
        return set() if signalled else {31}
    monkeypatch.setattr(module, 'PROC_ROOT', tmp_path)
    monkeypatch.setattr(module, 'owners', owners)
    monkeypatch.setattr(module.os, 'kill', lambda pid, sig: signalled.append((pid, sig)))
    module.stop_preview(cfg)
    assert signalled == [(31, module.signal.SIGTERM)]
    assert ('-n', 'tcp', '8099') in calls


def test_new_preview_output_name_has_no_attempt_suffix():
    import camera_only_preview as preview
    assert preview.web_output_name('boot-a') == 'camera-preview-boot-a'


@pytest.mark.parametrize('updated', [False, True])
def test_remote_preview_naming_is_compatible_with_already_deployed_module(updated, tmp_path, monkeypatch):
    module = remote('web_preview_start')
    preview = types.SimpleNamespace()
    if updated:
        preview.web_output_name = lambda boot: 'camera-preview-' + boot
    calls = []
    preview.main = lambda args: calls.append(args) or 0
    monkeypatch.setitem(sys.modules, 'camera_only_preview', preview)
    cfg = launcher().load_deployment_config()
    cfg['board_port'] = 8099
    assert module.launch_preview(cfg, tmp_path, 'boot-a') == 0
    args = calls[0]
    output = Path(args[args.index('--output-dir') + 1])
    assert output.name == ('camera-preview-boot-a' if updated else 'camera-preview-boot-a-06')
    assert preview.PREVIEW_PORT == 8099


@pytest.mark.parametrize('existing,serve_error', [(True, False), (False, False), (False, True)])
def test_bridge_always_releases_mutex_and_server(existing, serve_error, monkeypatch):
    module = launcher()
    closed = []
    class Function:
        def __init__(self, callback):
            self.callback = callback
        def __call__(self, *args):
            return self.callback(*args)
    kernel = types.SimpleNamespace(
        CreateMutexW=Function(lambda *args: 123),
        CloseHandle=Function(lambda handle: closed.append(handle)))
    monkeypatch.setattr(module.ctypes, 'WinDLL', lambda *args, **kwargs: kernel)
    monkeypatch.setattr(module.ctypes, 'get_last_error', lambda: 183 if existing else 0)
    class Bridge:
        def serve_forever(self):
            if serve_error:
                raise RuntimeError('serve failed')
        def server_close(self):
            closed.append('server')
    monkeypatch.setattr(module, 'make_bridge', lambda *args, **kwargs: Bridge())
    if serve_error:
        with pytest.raises(RuntimeError, match='serve failed'):
            module.run_bridge()
    else:
        module.run_bridge()
    assert closed == ([123] if existing else ['server', 123])


def test_bridge_identity_rejects_old_config_before_reusing_service(monkeypatch):
    import io
    module = launcher()
    class Opener:
        def open(self, url, timeout):
            if url.endswith('api/bridge-version'):
                result = {'version': module.BRIDGE_VERSION, 'deployment_sha256': 'stale-config'}
            else:
                result = {'sha256': module.hashlib.sha256(module.PAGE.read_bytes()).hexdigest()}
            return io.BytesIO(json.dumps(result).encode())
    monkeypatch.setattr(module, 'build_opener', lambda *args: Opener())
    with pytest.raises(RuntimeError, match='不同部署配置'):
        module.wait_for_web_sync(timeout=1)


def test_invalid_config_prevents_startup_network_or_stop(monkeypatch, tmp_path):
    module = launcher()
    monkeypatch.setattr(module, 'CONFIG_PATH', tmp_path / 'missing.json')
    monkeypatch.setattr(module, 'start_web_sync', lambda: pytest.fail('must validate before launch'))
    for operation in (module.main, module.start_board, module.stop_board):
        with pytest.raises(RuntimeError, match='部署配置'):
            operation()


@pytest.mark.parametrize('operation', ['start_board', 'stop_board'])
def test_ssh_payload_uses_validated_config_and_shell_quoting(operation, monkeypatch):
    import io
    import shlex
    module = launcher()
    cfg = module.load_deployment_config()
    cfg.update(host='board.example', board_dir='/home/xilinx/preview dir',
               python='/opt/pynq runtime/bin/python3', board_port=8099)
    monkeypatch.setattr(module, 'deployment_config', lambda: cfg)
    commands = []
    class Output(io.BytesIO):
        channel = types.SimpleNamespace(recv_exit_status=lambda: 0)
    class Client:
        def load_system_host_keys(self):
            pass
        def set_missing_host_key_policy(self, policy):
            pass
        def connect(self, host, **kwargs):
            assert host == cfg['host']
        def exec_command(self, command, **kwargs):
            commands.append(command)
            return io.StringIO(), Output(b'/tmp/material-web-preview.abc123\n'), Output(b'')
        def close(self):
            pass
    monkeypatch.setitem(sys.modules, 'paramiko', types.SimpleNamespace(SSHClient=Client, RejectPolicy=object))
    getattr(module, operation)()
    outer = shlex.split(commands[0])
    assert outer[:6] == ['sudo', '-S', '-p', '', 'sh', '-c']
    inner = shlex.split(outer[6])
    index = inner.index('-c')
    code = inner[index + 1]
    assert inner[index - 2 if operation == 'start_board' else index - 1] == cfg['python']
    compile(code, '<ssh-payload>', 'exec')
    assert json.loads(inner[index + 2]) == cfg
