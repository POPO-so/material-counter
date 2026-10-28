import sys
from pathlib import Path

import numpy as np
import pytest
import camera_only_preview as preview

sys.path.insert(0, str(Path(__file__).resolve().parent))

from camera_only_preview import profile_matches, software_gray_binary  # noqa: E402


def test_camera_preview_generates_same_threshold_128_binary_pip():
    for value, expected in ((127, 0), (128, 0), (129, 255)):
        camera_gray = np.full((400, 640), value, dtype=np.uint8)

        gray, mask = software_gray_binary(camera_gray)

        assert gray.shape == (240, 320)
        assert gray.dtype == np.uint8
        assert mask.shape == (240, 320)
        assert mask.dtype == np.uint8
        assert np.all(mask == expected)


def test_preview_reader_decodes_raw_mjpeg_directly_to_gray_and_rejects_bgr():
    gray = np.full((400, 640), 137, dtype=np.uint8)
    ok, encoded = preview.cv2.imencode(".jpg", gray)
    assert ok

    class Capture:
        def __init__(self, packet):
            self.packet = packet
        def read(self):
            return True, self.packet

    decoded = preview.read_frame(Capture(encoded.reshape(-1)))
    assert decoded.shape == (400, 640) and decoded.dtype == np.uint8
    assert np.max(np.abs(decoded.astype(np.int16) - 137)) <= 2
    with pytest.raises(RuntimeError, match="raw uint8 MJPEG"):
        preview.read_frame(Capture(np.zeros((400, 640, 3), dtype=np.uint8)))


def test_camera_preview_requires_exact_live_manual_v4l2_profile():
    profile = """Width/Height      : 640/400
Pixel Format      : 'MJPG'
Frames per second: 60.000 (60/1)
brightness: 0
auto_exposure: 1 (Manual Mode)
exposure_time_absolute: 50
"""

    assert profile_matches(profile)
    assert not profile_matches(profile.replace("brightness: 0", "brightness: 1"))
    assert not profile_matches(profile.replace("exposure_time_absolute: 50",
                                               "exposure_time_absolute: 51"))


def test_calibration_memory_policy_warns_for_dma_reserve_but_checks_real_memory():
    snapshot = {"cma_free_kib": 30000, "mem_available_kib": 200000, "process_rss_kib": 60000}
    assert preview.require_preview_memory(snapshot, calibration_mode=True) == "CMA_LOW: STOP CALIBRATION BEFORE DMA"
    with pytest.raises(RuntimeError, match="CMA"):
        preview.require_preview_memory(snapshot, calibration_mode=False)
    with pytest.raises(RuntimeError, match="CMA"):
        preview.require_preview_memory(snapshot, calibration_mode=True, startup=True)


def test_calibration_still_has_a_cma_emergency_floor():
    snapshot = {"cma_free_kib": 16383, "mem_available_kib": 200000, "process_rss_kib": 60000}
    with pytest.raises(RuntimeError, match="CMA"):
        preview.require_preview_memory(snapshot, calibration_mode=True)
    snapshot["cma_free_kib"] = 16384
    assert preview.require_preview_memory(snapshot, calibration_mode=True).startswith("CMA_LOW")


@pytest.mark.parametrize("field,value,error", [
    ("mem_available_kib", 65535, "MemAvailable"),
    ("process_rss_kib", 196609, "RSS"),
    ("cma_free_kib", -1, "invalid"),
])
def test_calibration_memory_policy_stops_on_actual_pressure(field, value, error):
    snapshot = {"cma_free_kib": 40000, "mem_available_kib": 200000, "process_rss_kib": 60000}
    snapshot[field] = value
    with pytest.raises(RuntimeError, match=error):
        preview.require_preview_memory(snapshot, calibration_mode=True)


def test_preview_memory_snapshot_reads_cma_available_and_process_rss(monkeypatch):
    texts = {"/proc/meminfo": "MemAvailable: 200000 kB\nCmaFree: 40000 kB\n",
             "/proc/self/status": "Name: python3\nVmRSS: 60000 kB\n"}
    monkeypatch.setattr(preview.Path, "read_text", lambda path, **kw: texts[str(path).replace('\\','/')])
    assert preview.preview_memory_snapshot() == {
        "cma_free_kib": 40000, "mem_available_kib": 200000, "process_rss_kib": 60000}


def test_drop_to_xilinx_keeps_only_camera_device_group(monkeypatch):
    calls = []
    device = type("Device", (), {"st_gid": 44})()
    user = type("User", (), {"pw_uid": 1000, "pw_gid": 100})()
    fake_pwd = type("Pwd", (), {"getpwnam": staticmethod(lambda name: user)})()
    monkeypatch.setattr(preview, "pwd", fake_pwd)
    real_stat = preview.os.stat
    monkeypatch.setattr(preview.os, "stat", lambda path, *args, **kwargs:
                        device if str(path) == preview.CAMERA_PATH else real_stat(path, *args, **kwargs))
    monkeypatch.setattr(preview.os, "setgroups", lambda groups: calls.append(("setgroups", groups)), raising=False)
    monkeypatch.setattr(preview.os, "setgid", lambda gid: calls.append(("setgid", gid)), raising=False)
    monkeypatch.setattr(preview.os, "setuid", lambda uid: calls.append(("setuid", uid)), raising=False)
    monkeypatch.setattr(preview.os, "geteuid", lambda: user.pw_uid, raising=False)

    preview.drop_to_xilinx()

    assert calls == [("setgroups", [device.st_gid]), ("setgid", user.pw_gid),
                     ("setuid", user.pw_uid)]


def test_query_v4l2_runs_directly_without_sudo(monkeypatch):
    calls = []
    monkeypatch.setattr(preview.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(preview.subprocess, "run", lambda command, **kwargs: calls.append(command)
                        or type("Result", (), {"stdout": "profile"})())

    assert preview.query_v4l2() == "profile"
    assert calls == [["v4l2-ctl", "-d", preview.CAMERA_PATH, "--get-fmt-video",
                      "--get-parm", "--get-ctrl=auto_exposure,exposure_time_absolute,brightness"]]


def test_apply_v4l2_controls_runs_directly_and_verifies_readback(monkeypatch):
    calls = []
    values = {"brightness": 2, "exposure_time_absolute": 55}
    readback = """Width/Height      : 640/400
Pixel Format      : 'MJPG'
Frames per second: 60.000 (60/1)
brightness: 2
auto_exposure: 1 (Manual Mode)
exposure_time_absolute: 55
"""

    def fake_run(command, **kwargs):
        calls.append(command)
        return type("Result", (), {"stdout": "set" if "--set-ctrl=" in " ".join(command)
                                    else readback})()

    monkeypatch.setattr(preview.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(preview.subprocess, "run", fake_run)

    assert preview.apply_v4l2_controls(values) == values
    assert calls[0] == ["v4l2-ctl", "-d", preview.CAMERA_PATH,
                        "--set-ctrl=brightness=2,exposure_time_absolute=55"]
    assert calls[1][0] == "v4l2-ctl"
    assert all(command[0] != "sudo" for command in calls)


def load_web_launcher():
    import types
    path = Path(__file__).resolve().parents[2] / "Python脚本" / "启动网页.pyw"
    module = types.ModuleType("web_launcher")
    module.__file__ = str(path)
    source = path.read_text(encoding="utf-8")
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def test_web_launcher_reuses_ready_preview_before_opening(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    monkeypatch.setattr(launcher, "STARTUP_STATUS", tmp_path / "status.json")
    calls = []
    monkeypatch.setattr(launcher, "available", lambda: True)
    monkeypatch.setattr(launcher, "start_board", lambda: calls.append("start"))
    monkeypatch.setattr(launcher, "start_web_sync", lambda: calls.append("sync"))
    monkeypatch.setattr(launcher, "wait_for_web_sync", lambda: calls.append("ready"))
    monkeypatch.setattr(launcher, "open_browser", lambda url, report=None: calls.append("open") or True)
    messages = []
    launcher.main(messages.append)
    assert calls == ["sync", "ready", "open"]
    assert messages[-1] == "网页已就绪"


def test_web_launcher_reports_failure_without_opening(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    monkeypatch.setattr(launcher, "STARTUP_STATUS", tmp_path / "status.json")
    calls = []
    monkeypatch.setattr(launcher, "available", lambda: True)
    monkeypatch.setattr(launcher, "start_web_sync", lambda: None)
    def fail_wait():
        raise RuntimeError("local service failed")
    monkeypatch.setattr(launcher, "wait_for_web_sync", fail_wait)
    monkeypatch.setattr(launcher, "open_browser", lambda url, report=None: calls.append("open"))
    with pytest.raises(RuntimeError, match="local service failed"):
        launcher.main(lambda message: None)
    assert calls == []


def test_web_launcher_starts_bridge_as_pyw_without_console(monkeypatch):
    launcher = load_web_launcher()
    calls = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda command, **kwargs: calls.append((command, kwargs)))
    launcher.start_web_sync()
    command, options = calls[0]
    assert command[0].endswith("pythonw.exe")
    assert command[1].endswith("启动网页.pyw")
    assert command[2:] == ["--bridge"]
    assert options["stdout"] is launcher.subprocess.DEVNULL


def test_web_launcher_opens_browser_before_starting_offline_board(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    monkeypatch.setattr(launcher, "STARTUP_STATUS", tmp_path / "status.json")
    calls = []
    ready = iter([False, True])
    monkeypatch.setattr(launcher, "available", lambda: next(ready))
    monkeypatch.setattr(launcher, "start_web_sync", lambda: calls.append("sync"))
    monkeypatch.setattr(launcher, "wait_for_web_sync", lambda: calls.append("ready"))
    monkeypatch.setattr(launcher, "open_browser", lambda url, report=None: calls.append("open") or True)
    monkeypatch.setattr(launcher, "start_board", lambda: calls.append("board") or "/tmp/run")
    launcher.main()
    assert calls == ["sync", "ready", "open", "board"]


def test_web_launcher_silent_entry_logs_failure(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    log = tmp_path / "startup.log"
    monkeypatch.setattr(launcher, "STARTUP_LOG", log)
    monkeypatch.setattr(launcher, "STARTUP_STATUS", tmp_path / "status.json")
    monkeypatch.setattr(launcher, "main", lambda: (_ for _ in ()).throw(RuntimeError("startup failed")))
    launcher.run_silently()
    assert "startup failed" in log.read_text(encoding="utf-8")


def test_startup_status_endpoint_reads_current_state(monkeypatch, tmp_path):
    import json
    import threading
    from urllib.request import urlopen
    launcher = load_web_launcher()
    monkeypatch.setattr(launcher, "STARTUP_STATUS", tmp_path / "status.json")
    launcher.set_startup_status("starting", "正在启动板端预览")
    server = launcher.make_bridge("127.0.0.1", lambda report: None, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/startup-status", timeout=2) as response:
            assert json.load(response) == {"phase": "starting", "message": "正在启动板端预览"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_web_launcher_reports_error_in_browser_status(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    monkeypatch.setattr(launcher, "STARTUP_LOG", tmp_path / "startup.log")
    monkeypatch.setattr(launcher, "STARTUP_STATUS", tmp_path / "status.json")
    monkeypatch.setattr(launcher, "main", lambda: (_ for _ in ()).throw(RuntimeError("board failed")))
    launcher.run_silently()
    import json
    status = json.loads(launcher.STARTUP_STATUS.read_text(encoding="utf-8"))
    assert status == {"phase": "error", "message": "board failed"}


def test_web_launcher_opens_default_browser_through_windows_shell(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    browser = tmp_path / 'browser.exe'
    browser.touch()
    calls = []
    monkeypatch.setattr(launcher, 'default_browser_executable', lambda: browser)
    monkeypatch.setattr(launcher.os, 'startfile', lambda *args: calls.append(args))
    monkeypatch.setattr(launcher, '_wait_for_browser_window', lambda executable, timeout=10, title_hint=None: True)
    monkeypatch.setattr(launcher, '_page_title', lambda url: None)
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('browser must use Windows Shell'))
    assert launcher.open_browser('http://127.0.0.1:18765/') is True
    assert calls == [(str(browser), 'open', 'http://127.0.0.1:18765/')]


def test_web_launcher_does_not_hide_browser_shell_failure(monkeypatch):
    launcher = load_web_launcher()
    monkeypatch.setattr(launcher, 'default_browser_executable', lambda: Path('browser.exe'))
    def fail(*args):
        raise OSError('browser launch denied')
    monkeypatch.setattr(launcher.os, 'startfile', fail)
    with pytest.raises(OSError, match='browser launch denied'):
        launcher.open_browser('http://127.0.0.1:18765/')


def test_web_launcher_recovers_when_browser_window_never_appears(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    browser = tmp_path / 'browser.exe'
    browser.touch()
    log = tmp_path / 'startup.log'
    status = tmp_path / 'status.json'
    startfile_calls = []
    killed = []
    reports = []
    dedicated = []
    monkeypatch.setattr(launcher, 'STARTUP_LOG', log)
    monkeypatch.setattr(launcher, 'STARTUP_STATUS', status)
    monkeypatch.setattr(launcher, 'default_browser_executable', lambda: browser)
    monkeypatch.setattr(launcher.os, 'startfile', lambda *args: startfile_calls.append(args))
    monkeypatch.setattr(launcher, '_wait_for_browser_window',
                        lambda executable, timeout=10, title_hint=None: bool(dedicated))
    monkeypatch.setattr(launcher, '_browser_window_title', lambda executable: '')
    monkeypatch.setattr(launcher, '_terminate_browser_processes', lambda executable: killed.append(executable))
    monkeypatch.setattr(launcher, '_wait_for_browser_exit', lambda executable, timeout=15: True)
    monkeypatch.setattr(launcher, '_launch_dedicated_profile', lambda executable, url: dedicated.append((executable, url)))
    monkeypatch.setattr(launcher, '_page_title', lambda url: 'FPGA 物料计数控制看板')
    monkeypatch.setattr(launcher.time, 'sleep', lambda seconds: None)
    assert launcher.open_browser('http://127.0.0.1:18765/', reports.append) is True
    assert len(startfile_calls) == 1
    assert startfile_calls[0] == (str(browser), 'open', 'http://127.0.0.1:18765/')
    assert killed == [browser]
    assert dedicated == [(browser, 'http://127.0.0.1:18765/')]
    assert reports == ['浏览器未响应，正在重启浏览器…']
    assert '浏览器恢复' in log.read_text(encoding='utf-8')
    import json
    assert json.loads(status.read_text(encoding='utf-8'))['message'].startswith('浏览器后台进程未响应')


def test_web_launcher_keeps_user_windows_and_skips_kill(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    browser = tmp_path / 'browser.exe'
    browser.touch()
    killed = []
    dedicated = []
    monkeypatch.setattr(launcher, 'STARTUP_LOG', tmp_path / 'startup.log')
    monkeypatch.setattr(launcher, 'STARTUP_STATUS', tmp_path / 'status.json')
    monkeypatch.setattr(launcher, 'default_browser_executable', lambda: browser)
    monkeypatch.setattr(launcher.os, 'startfile', lambda *args: None)
    monkeypatch.setattr(launcher, '_wait_for_browser_window', lambda executable, timeout=10, title_hint=None: bool(dedicated))
    # 浏览器已有可见窗口（用户正在使用）：不得清理进程。
    monkeypatch.setattr(launcher, '_browser_window_title', lambda executable: '其他页面 - Microsoft Edge')
    monkeypatch.setattr(launcher, '_terminate_browser_processes', lambda executable: killed.append(executable))
    monkeypatch.setattr(launcher, '_launch_dedicated_profile', lambda executable, url: dedicated.append(url))
    monkeypatch.setattr(launcher, '_page_title', lambda url: None)
    monkeypatch.setattr(launcher.time, 'sleep', lambda seconds: None)
    assert launcher.open_browser('http://127.0.0.1:18765/', lambda message: None) is True
    assert killed == []
    assert dedicated == ['http://127.0.0.1:18765/']


def test_web_launcher_returns_false_when_all_launch_paths_fail(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    browser = tmp_path / 'browser.exe'
    browser.touch()
    killed = []
    monkeypatch.setattr(launcher, 'STARTUP_LOG', tmp_path / 'startup.log')
    monkeypatch.setattr(launcher, 'STARTUP_STATUS', tmp_path / 'status.json')
    monkeypatch.setattr(launcher, 'default_browser_executable', lambda: browser)
    monkeypatch.setattr(launcher.os, 'startfile', lambda *args: None)
    monkeypatch.setattr(launcher, '_wait_for_browser_window', lambda executable, timeout=10, title_hint=None: False)
    monkeypatch.setattr(launcher, '_browser_window_title', lambda executable: '')
    monkeypatch.setattr(launcher, '_terminate_browser_processes', lambda executable: killed.append(executable))
    monkeypatch.setattr(launcher, '_wait_for_browser_exit', lambda executable, timeout=15: True)
    monkeypatch.setattr(launcher, '_launch_dedicated_profile', lambda executable, url: None)
    monkeypatch.setattr(launcher, '_page_title', lambda url: None)
    monkeypatch.setattr(launcher.time, 'sleep', lambda seconds: None)
    assert launcher.open_browser('http://127.0.0.1:18765/', lambda message: None) is False
    assert killed == [browser]


def test_web_launcher_window_wait_treats_missing_probe_as_success(monkeypatch):
    launcher = load_web_launcher()
    monkeypatch.setattr(launcher.subprocess, 'run', lambda *args, **kwargs: pytest.fail('probe must not run'))
    assert launcher._wait_for_browser_window(None) is True


def test_web_launcher_window_wait_requires_page_title_when_hint_given(monkeypatch):
    launcher = load_web_launcher()
    probe = type('Result', (), {'returncode': 0})()
    monkeypatch.setattr(launcher.subprocess, 'run', lambda *args, **kwargs: probe)
    titles = iter(['无标题 - Microsoft Edge', 'FPGA 物料计数控制看板 - Microsoft Edge'])
    monkeypatch.setattr(launcher, '_browser_window_title', lambda executable: next(titles))
    monkeypatch.setattr(launcher.time, 'sleep', lambda seconds: None)
    browser = Path('msedge.exe')
    assert launcher._wait_for_browser_window(browser, timeout=1, title_hint='FPGA 物料计数控制看板') is True
    titles = iter(['无标题 - Microsoft Edge'])
    monkeypatch.setattr(launcher, '_browser_window_title', lambda executable: next(titles, '无标题 - Microsoft Edge'))
    assert launcher._wait_for_browser_window(browser, timeout=0.2, title_hint='FPGA 物料计数控制看板') is False


def test_web_launcher_dedicated_profile_launches_with_isolated_profile(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    browser = tmp_path / 'browser.exe'
    browser.touch()
    calls = []
    monkeypatch.setattr(launcher, 'RUNTIME_DIR', tmp_path)
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda command, **kwargs: calls.append((command, kwargs)))
    launcher._launch_dedicated_profile(browser, 'http://127.0.0.1:18765/')
    command, options = calls[0]
    assert command[0] == str(browser)
    assert '--user-data-dir=' + str(tmp_path / 'browser-profile') in command
    assert '--hide-crash-restore-bubble' in command
    assert command[-1] == 'http://127.0.0.1:18765/'
    assert (tmp_path / 'browser-profile').is_dir()
    assert options.get('creationflags')


def test_web_launcher_browser_exit_wait_times_out_and_releases(monkeypatch):
    launcher = load_web_launcher()
    counts = iter([3, 1, 0])
    monkeypatch.setattr(launcher, '_browser_process_count', lambda executable: next(counts))
    monkeypatch.setattr(launcher.time, 'sleep', lambda seconds: None)
    assert launcher._wait_for_browser_exit(Path('browser.exe')) is True
    # 计数探测持续报 2（无法归零）时超时放行，不永久阻塞。
    import time as real_time
    monkeypatch.setattr(launcher, '_browser_process_count', lambda executable: 2)
    monkeypatch.setattr(launcher.time, 'sleep', lambda seconds: None)
    start = real_time.monotonic()
    result = launcher._wait_for_browser_exit(Path('browser.exe'), timeout=0.05)
    assert result is False
    assert real_time.monotonic() - start < 10


def test_web_launcher_status_location_does_not_depend_on_current_directory(monkeypatch, tmp_path):
    launcher = load_web_launcher()
    monkeypatch.chdir(tmp_path)
    assert launcher.STARTUP_STATUS == launcher.ROOT / '.runtime' / 'material-counter-startup-status.json'


def test_preview_publishes_board_resource_health(monkeypatch):
    calls = []
    class Sampler:
        def update_health(self, **kwargs):
            calls.append(kwargs)
        def snapshot(self):
            return {"resource_health": {"cpu_percent": 42.0, "cma_total_kib": 100}}
    state = {}
    preview.update_resource_health(state, Sampler(), {"cma_free_kib": 25})
    assert calls == [{"cma_free_kib": 25}]
    assert state["resource_health"] == {"cpu_percent": 42.0, "cma_total_kib": 100}


def test_preview_resource_monitor_starts_disabled_and_can_stop():
    monitor = preview.create_resource_monitor()
    try:
        assert monitor.snapshot()['enabled'] is False
        monitor.control(enabled=True, mode='detail')
        assert monitor.snapshot()['enabled'] is True
        monitor.control(enabled=False, mode='normal')
        assert monitor.snapshot() == {'enabled': False, 'mode': 'normal', 'metrics': {}}
    finally:
        monitor.close()


def test_backend_update_bridge_rejects_other_origins_and_dispatches_once():
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    module = load_web_launcher()
    calls = []
    server = module.make_bridge("192.168.2.99", lambda report: calls.append("update"), port=0)
    import threading
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/update"
    try:
        def post(origin):
            return urlopen(Request(url, data=b"{}", method="POST", headers={"Origin": origin, "Content-Type": "application/json"}), timeout=3)
        with pytest.raises(HTTPError) as error:
            post("http://other.invalid")
        assert error.value.code == 403
        with post(f"http://127.0.0.1:{server.server_port}") as response:
            assert response.status == 202
        thread_deadline = __import__('time').monotonic() + 2
        while not calls and __import__('time').monotonic() < thread_deadline:
            __import__('time').sleep(.01)
        assert calls == ["update"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_backend_deploy_does_not_stop_preview_when_board_check_fails(tmp_path, monkeypatch):
    import importlib.util
    from types import SimpleNamespace
    updater = load_web_launcher()
    source = tmp_path / "scripts" / "wo0016"
    source.mkdir(parents=True)
    for name in updater.MODULES:
        target = tmp_path / "scripts" / "gray8" / name if name == "packet_codec.py" else source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("pass\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    monkeypatch.setattr(updater, "available", lambda: True)
    monkeypatch.setattr(updater, "stop_board", lambda: calls.append("stopped"))
    class Output:
        channel = SimpleNamespace(recv_exit_status=lambda: 1)
        def __init__(self, value): self.value = value
        def read(self): return self.value
    class Sftp:
        files = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def mkdir(self, *args): pass
        def remove(self, path): self.files.pop(path, None)
        def rmdir(self, path): pass
        def open(self, path, mode):
            import io
            if "r" in mode: return io.BytesIO(self.files[path])
            class Writer(io.BytesIO):
                def close(inner):
                    self.files[path] = inner.getvalue()
                    super().close()
            return Writer()
    class Client:
        def load_system_host_keys(self): pass
        def set_missing_host_key_policy(self, policy): pass
        def connect(self, *args, **kwargs): pass
        def open_sftp(self): return Sftp()
        def exec_command(self, *args, **kwargs): return None, Output(b""), Output(b"bad syntax")
        def close(self): pass
    monkeypatch.setitem(sys.modules, "paramiko", SimpleNamespace(SSHClient=Client, RejectPolicy=lambda: None))
    with pytest.raises(RuntimeError, match="旧服务仍在运行"):
        updater.deploy("board", "/board", lambda message: None)
    assert calls == []


def test_local_dashboard_uses_local_page_and_proxies_board_status(tmp_path, monkeypatch):
    import importlib.util
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.request import urlopen
    module = load_web_launcher()
    page = tmp_path / "dashboard.html"
    page.write_text("<head></head><body>local page</body>", encoding="utf-8")
    monkeypatch.setattr(module, "PAGE", page)
    class Board(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"board":true}')
        def log_message(self, *args): pass
    board = ThreadingHTTPServer(("127.0.0.1", 0), Board)
    local = module.make_bridge("127.0.0.1", lambda report: None, port=0, board_port=board.server_port)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (board, local)]
    for thread in threads: thread.start()
    try:
        base = f"http://127.0.0.1:{local.server_port}"
        with urlopen(base + "/", timeout=2) as response: assert b"local page" in response.read()
        with urlopen(base + "/status.json", timeout=2) as response: assert response.read() == b'{"board":true}'
    finally:
        for server in (local, board): server.shutdown(); server.server_close()
        for thread in threads: thread.join()


def test_preview_fpga_report_requires_same_boot_verified_self_test(tmp_path):
    import json
    evidence = tmp_path / 'self-test.json'
    evidence.write_text(json.dumps({'status':'GRAY8_PIXEL_VERIFIED','mode':'self-test','frames':3,'pixel_verified_frames':3,'buffers_held':False,'boot_id':'boot-a','manifest_sha256':'b81e6ea09cba48d643b3f09ae88b9ea784176c81ebc53799f1af38aedb0cd484'}))
    assert preview.preview_fpga_resources(evidence,'boot-a')['lut'] == [2777, 53200]
    assert preview.preview_fpga_resources(evidence,'other-boot') is None
    evidence.unlink()
    assert preview.preview_fpga_resources(evidence,'boot-a') is None


def test_preview_startup_reclaims_cache_before_enforcing_cma_reserve(monkeypatch):
    samples = iter([{"cma_free_kib": 32724}, {"cma_free_kib": 42000}])
    writes = []
    monkeypatch.setattr(preview, "preview_memory_snapshot", lambda: next(samples))
    monkeypatch.setattr(preview.os, "sync", lambda: writes.append("sync"), raising=False)
    monkeypatch.setattr(preview.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(preview.Path, "write_text", lambda self, value: writes.append((str(self).replace('\\', '/'), value)))
    assert preview.startup_memory_snapshot()["cma_free_kib"] == 42000
    assert writes == ["sync", ("/proc/sys/vm/drop_caches", "3\n")]


def test_update_requires_its_own_start_log_and_detects_failure(monkeypatch):
    import importlib.util
    import io
    updater = load_web_launcher()
    class Sftp:
        def __init__(self, log): self.log = log
        def open(self, path, mode): return io.BytesIO(self.log)
    launcher = {"available": lambda: True}
    with pytest.raises(RuntimeError, match="启动失败"):
        updater.wait_for_preview(launcher, Sftp(b"Traceback (most recent call last)"), "/tmp/new", timeout=1)
    updater.wait_for_preview(launcher, Sftp(b"CAMERA_HUD_PREVIEW_READY"), "/tmp/new", timeout=1)
