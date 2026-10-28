"""Independent desktop lifecycle, SSH deployment and single-frame receiver."""
from __future__ import annotations

import http.client
import json
import logging
import os
from pathlib import Path, PurePosixPath
import queue
import re
import secrets
import shlex
import socket
import threading
import time
import uuid
from collections import deque

from frame_protocol import read_frame

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "Python脚本" / "RLE桌面部署.json"
HERE = Path(__file__).resolve().parent
MODULES = ("camera_only_preview.py", "mjpeg_preview.py", "image_debug.py",
           "dashboard_state.py", "live_monitor_state.py", "resource_monitor.py",
           "live_calibration.py", "stream_counter.py", "lighting_profile.py",
           "scale_parameters.py", "split_touching.py", "binary_stats.py",
           "packet_encoder.py")
PACKAGE_FILES = (
    ("scripts/rle/__init__.py", ROOT / "scripts/rle/__init__.py"),
    ("scripts/rle/protocol.py", ROOT / "scripts/rle/protocol.py"),
    ("scripts/rle/components.py", ROOT / "scripts/rle/components.py"),
    ("scripts/rle/counter.py", ROOT / "scripts/rle/counter.py"),
    ("scripts/rle/transport.py", ROOT / "scripts/rle/transport.py"),
    ("scripts/rle/artifacts.py", ROOT / "scripts/rle/artifacts.py"),
    ("scripts/rle/run_counter.py", ROOT / "scripts/rle/run_counter.py"),
    ("scripts/rle/native/rle_components.cpp", ROOT / "scripts/rle/native/rle_components.cpp"),
    ("scripts/wo0015/board_preflight.py", ROOT / "scripts/wo0015/board_preflight.py"),
    ("scripts/wo0015/board_once.py", ROOT / "scripts/wo0015/board_once.py"),
    ("scripts/wo0016/stream_counter.py", ROOT / "scripts/wo0016/stream_counter.py"),
    ("scripts/wo0016/split_touching.py", ROOT / "scripts/wo0016/split_touching.py"),
    ("scripts/wo0016/binary_stats.py", ROOT / "scripts/wo0016/binary_stats.py"),
)


def validate_config(value):
    required = {"host", "board_port", "board_dir", "runtime_root", "python", "pynq_init",
                "manifest", "manifest_sha256", "bit", "bit_sha256", "legacy_preview_paths"}
    optional = {"protocol", "fpga_resources"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise ValueError("部署配置字段缺失或未知")
    host = value["host"]
    if (not isinstance(host, str) or len(host) > 253 or not host or
            any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in host.split("."))):
        raise ValueError("板卡地址无效")
    if type(value["board_port"]) is not int or not 1 <= value["board_port"] <= 65535:
        raise ValueError("端口必须在 1..65535")
    for key in ("board_dir", "runtime_root", "python", "pynq_init", "manifest", "bit"):
        raw = value[key]
        if not isinstance(raw, str) or any(c in raw for c in ("\\", "\0", "\n", "\r")):
            raise ValueError(key + " 路径无效")
        path = PurePosixPath(raw)
        absolute = key not in ("manifest", "bit")
        if path.is_absolute() != absolute or str(path) != raw or raw in ("/", ".") or ".." in path.parts:
            raise ValueError(key + " 路径不规范")
    for key in ("manifest_sha256", "bit_sha256"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{64}", value[key]):
            raise ValueError(key + " 哈希无效")
    if not isinstance(value["legacy_preview_paths"], list):
        raise ValueError("legacy_preview_paths 无效")
    result = dict(value)
    protocol = result.setdefault("protocol", "GRAY8_PACKET_V1")
    if protocol not in ("GRAY8_PACKET_V1", "RLE_PACKET_V1"):
        raise ValueError("protocol 无效")
    resources = result.get("fpga_resources")
    if resources is not None:
        if not isinstance(resources, dict):
            raise ValueError("fpga_resources 无效")
        for key in ("lut", "ff", "bram", "dsp", "lutram", "iob", "bufg", "mmcm", "pll"):
            pair = resources.get(key)
            if pair is None:
                continue
            if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                    or any(type(item) not in (int, float) for item in pair)
                    or pair[0] < 0 or pair[1] <= 0 or pair[0] > pair[1]):
                raise ValueError("fpga_resources." + key + " 无效")
    return result


def load_config(path=CONFIG):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("重复配置字段 " + key)
            result[key] = value
        return result
    return validate_config(json.loads(Path(path).read_text(encoding="utf-8-sig",), object_pairs_hook=unique))


def connection_error(exc):
    # Paramiko wraps per-address socket errors in NoValidConnectionsError.
    nested = getattr(exc, "errors", None)
    if isinstance(nested, dict) and any(
            isinstance(error, PermissionError) or getattr(error, "winerror", None) == 10013
            for error in nested.values()):
        return "当前运行环境限制了网络访问，请在正常桌面环境或允许网络的环境运行"
    if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 10013:
        return "当前运行环境限制了网络访问，请在正常桌面环境或允许网络的环境运行"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "连接超时：请检查网线、板卡电源和地址"
    if isinstance(exc, ConnectionRefusedError):
        return "板卡拒绝连接：请检查 SSH 或桌面采集服务"
    return str(exc)


def _ssh(config):
    import paramiko
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(config["host"], username="xilinx", password="xilinx", timeout=8,
                   look_for_keys=False, allow_agent=False)
    return client


def _request(config, token, method, path, payload=None, timeout=4):
    connection = http.client.HTTPConnection(config["host"], config["board_port"], timeout=timeout)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": "Bearer " + token}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        if response.status != 200:
            raise RuntimeError(data.get("error", f"HTTP {response.status}"))
        return data
    finally:
        connection.close()


def deploy(config, session_id, token):
    """Upload an isolated immutable session directory; never replace another owner."""
    remote_root = "/home/xilinx/.material-counter-desktop"
    remote_dir = remote_root + "/" + session_id
    if not re.fullmatch(r"[0-9a-f]{32}", session_id):
        raise ValueError("invalid session identity")
    client = _ssh(config)
    try:
        with client.open_sftp() as sftp:
            try:
                sftp.mkdir(remote_root, mode=0o700)
            except IOError:
                if not sftp.stat(remote_root):
                    raise
            sftp.mkdir(remote_dir, mode=0o700)
            files = [(HERE / name, name) for name in (
                "board_entry.py", "desktop_board.py", "frame_protocol.py",
                "rle_desktop_board.py")]
            files += [(ROOT / "scripts" / "wo0016" / name, name) for name in MODULES]
            files += [(ROOT / "scripts" / "gray8" / "packet_codec.py", "packet_codec.py")]
            packaged = [(local, name) for name, local in PACKAGE_FILES]
            all_files = files + packaged
            for local, _name in all_files:
                content = local.read_bytes()
                if local.suffix == ".py":
                    compile(content, str(local), "exec")
            directories = set()
            for _, remote_name in packaged:
                directories.update(
                    str(parent) for parent in PurePosixPath(remote_name).parents
                    if str(parent) != "."
                )
            for name in sorted(directories, key=lambda item: (item.count("/"), item)):
                sftp.mkdir(remote_dir + "/" + name, mode=0o700)
            for local, name in all_files:
                sftp.put(str(local), remote_dir + "/" + name)
            remote_config = dict(config, session_id=session_id, token=token)
            config_path = remote_dir + "/config.json"
            with sftp.open(config_path, "w") as stream:
                stream.write(json.dumps(remote_config))
            sftp.chmod(config_path, 0o600)
        runner = ("import os,subprocess,sys; f=open(sys.argv[1],'ab',buffering=0); "
                  "p=subprocess.Popen([sys.argv[2],'-u',sys.argv[3],sys.argv[4]], "
                  "stdin=subprocess.DEVNULL,stdout=f,stderr=f,start_new_session=True); print(p.pid)")
        args = [config["python"], "-c", runner, remote_dir + "/viewer.log", config["python"],
                remote_dir + "/board_entry.py", remote_dir + "/config.json"]
        command = "sudo -S -p '' " + " ".join(shlex.quote(arg) for arg in args)
        stdin, stdout, stderr = client.exec_command(command, timeout=20)
        stdin.write("xilinx\n")
        stdin.flush()
        output = stdout.read().decode("utf-8", "replace").strip()
        error = stderr.read().decode("utf-8", "replace").strip()
        code = stdout.channel.recv_exit_status()
        if code or not output.isdigit():
            raise RuntimeError("板端启动命令失败：" + (error or output or str(code)))
        return remote_dir, int(output)
    finally:
        client.close()


def read_remote_result(config, remote_dir):
    client = _ssh(config)
    try:
        with client.open_sftp() as sftp:
            try:
                with sftp.open(remote_dir + "/result.json", "r") as f:
                    return json.loads(f.read().decode("utf-8"))
            except IOError:
                return None
    finally:
        client.close()


class DesktopController:
    def __init__(self, root, config):
        self.root, self.config = root, validate_config(config)
        self.view = None
        self.events = queue.SimpleQueue()
        self.frame_lock = threading.Lock()
        self.latest_frame = None
        self.frame_times = deque(maxlen=60)
        self.session = None
        self.paused = threading.Event()
        self.shutdown = threading.Event()
        self.busy = threading.Lock()
        self.latest_status = {}
        self.root.after(50, self._pump)

    def attach(self, view):
        self.view = view
        self._status({})

    def _message(self, message):
        logging.info("%s", message)
        self.events.put(("message", str(message)))

    def _status(self, data):
        self.events.put(("status", data))

    def _pump(self):
        if self.shutdown.is_set():
            return
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "destroy":
                self.shutdown.set()
                self.root.destroy()
                return
            if self.view is None:
                continue
            if kind == "message":
                self.view.show_message(data)
            elif kind == "status":
                status = dict(data)
                status["connected"] = bool(data.get("telemetry"))
                with self.frame_lock:
                    if len(self.frame_times) > 1 and time.monotonic() - self.frame_times[-1] <= 5:
                        span = self.frame_times[-1] - self.frame_times[0]
                        preview = dict(status.get("preview") or {})
                        preview["fps"] = (len(self.frame_times) - 1) / span if span > 0 else None
                        status["preview"] = preview
                caps = dict(status.get("capabilities") or {})
                caps["start"] = self.session is None and not self.busy.locked()
                caps["update_backend"] = self.session is not None and not self.busy.locked()
                status["capabilities"] = caps
                self.view.show_status(status)
            elif kind == "close_failed":
                self.view.closing = False
                self.view.show_message(data)
                self._status(self.latest_status)
        with self.frame_lock:
            frame, self.latest_frame = self.latest_frame, None
        if frame is not None and self.view is not None and not self.paused.is_set():
            self.view.show_frame(frame)
        self.root.after(50, self._pump)

    def _operation(self, func):
        if not self.busy.acquire(blocking=False):
            self._message("另一个操作正在进行")
            return
        def work():
            try:
                func()
            except Exception as exc:
                logging.exception("桌面操作失败")
                self._message("操作失败：" + connection_error(exc))
            finally:
                self.busy.release()
                self._status(self.latest_status)
        threading.Thread(target=work, daemon=True).start()

    def start(self):
        self._operation(self._start)

    def _start(self):
        if self.session is not None:
            raise RuntimeError("本 GUI 已拥有运行任务")
        session_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        self._message("正在通过 SSH 部署独立桌面服务并验证板卡")
        remote_dir, pid = deploy(self.config, session_id, token)
        self.session = {"id": session_id, "token": token, "dir": remote_dir, "pid": pid}
        deadline = time.monotonic() + 120
        last_result_check = 0
        while time.monotonic() < deadline:
            try:
                status = _request(self.config, token, "GET", "/status.json", timeout=2)
                if status.get("session_id") != session_id:
                    raise RuntimeError("端口属于其他任务，不接管")
                self.latest_status = status
                self._status(status)
                source = status.get("telemetry", {}).get("source")
                self._message("采集已启动；计算来源：" + (
                    "PL 游程 + PS 区域分析/跟踪" if source == "pl_rle" else "软件阈值"))
                threading.Thread(target=self._poll, args=(session_id,), daemon=True).start()
                threading.Thread(target=self._receive, args=(session_id,), daemon=True).start()
                return
            except (OSError, http.client.HTTPException):
                pass
            if time.monotonic() - last_result_check >= 5:
                last_result_check = time.monotonic()
                result = read_remote_result(self.config, remote_dir)
                if result and result.get("status") == "FAILED":
                    self.session = None
                    raise RuntimeError("板端启动失败：" + result.get("error", "请检查 viewer.log"))
                if result and result.get("status") == "FAIL_HOLD":
                    raise RuntimeError("DMA 所有权未确认，板端已保留缓冲区；请检查 "
                                       + remote_dir + "/viewer.log，不自动重启")
            time.sleep(0.5)
        raise RuntimeError("板端启动超时，服务状态未确认；请检查 " + remote_dir + "/viewer.log")

    def _poll(self, session_id):
        while self.session and self.session["id"] == session_id and not self.shutdown.is_set():
            session = self.session
            try:
                status = _request(self.config, session["token"], "GET", "/status.json")
                if self.session is not session:
                    break
                if status.get("session_id") != session_id:
                    raise RuntimeError("会话身份不一致")
                self.latest_status = status
                self._status(status)
            except Exception as exc:
                self._message("状态连接中断：" + connection_error(exc))
                self._status({})
            time.sleep(1.5)

    def _receive(self, session_id):
        while self.session and self.session["id"] == session_id and not self.shutdown.is_set():
            if self.paused.is_set():
                time.sleep(0.1)
                continue
            connection = http.client.HTTPConnection(self.config["host"], self.config["board_port"], timeout=3)
            response = None
            try:
                connection.request("GET", "/api/frames", headers={"Authorization": "Bearer " + self.session["token"]})
                response = connection.getresponse()
                if response.status != 200:
                    raise RuntimeError(f"图像连接返回 HTTP {response.status}")
                last_frame = -1
                while not self.paused.is_set() and self.session and self.session["id"] == session_id:
                    frame = read_frame(response)
                    if frame["session_id"] != session_id or frame["frame_id"] <= last_frame:
                        raise RuntimeError("帧会话或序号不一致")
                    last_frame = frame["frame_id"]
                    with self.frame_lock:
                        self.latest_frame = frame
                        self.frame_times.append(time.monotonic())
            except Exception as exc:
                if not self.paused.is_set() and self.session and self.session["id"] == session_id:
                    self._message("图像连接中断，正在重连：" + connection_error(exc))
                    time.sleep(1)
            finally:
                if response is not None:
                    response.close()
                connection.close()

    def _control(self, path, payload):
        if self.session is None:
            raise RuntimeError("当前没有运行任务")
        return _request(self.config, self.session["token"], "POST", path, payload)

    def reset(self):
        self._operation(lambda: (self._control("/api/reset-count", {}), self._message("计数清零已提交")))

    def stop(self):
        self._operation(self._stop)

    def _stop(self):
        if self.session is None:
            return
        session = self.session
        self._message("正在安全停止采集，等待相机释放与结果保存")
        try:
            status = _request(self.config, session["token"], "GET", "/status.json")
        except (OSError, http.client.HTTPException):
            result = read_remote_result(self.config, session["dir"])
            if (result and result.get("session_id") == session["id"]
                    and result.get("status") == "STOPPED"
                    and result.get("standard_profile_restored") is True):
                self.session = None
                self.latest_status = {}
                self._message(f"采集已停止，最终计数 {result.get('total_count', '--')}；结果：{session['dir']}/result.json")
                return
            raise
        if status.get("session_id") != session["id"]:
            raise RuntimeError("端口会话不匹配，拒绝停止其他任务")
        self._control("/api/stop", {})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = read_remote_result(self.config, session["dir"])
            if result and result.get("status") in ("STOPPED", "FAILED", "FAIL_HOLD"):
                if result.get("status") != "STOPPED" or result.get("standard_profile_restored") is not True:
                    raise RuntimeError("板端停止未确认：" + json.dumps(result, ensure_ascii=False))
                self.session = None
                self.latest_status = {}
                self._message(f"采集已停止，最终计数 {result.get('total_count', '--')}；结果：{session['dir']}/result.json")
                return
            time.sleep(0.5)
        raise RuntimeError("停止结果未确认；未强制终止板端进程")

    def update_backend(self):
        self._operation(lambda: (self._stop(), self._start()))

    def set_preview_paused(self, paused):
        self.paused.set() if paused else self.paused.clear()
        if paused:
            with self.frame_lock:
                self.latest_frame = None
                self.frame_times.clear()

    def set_debug(self, enabled):
        self._operation(lambda: self._control("/api/debug-mode", {"enabled": enabled}))

    def set_resource(self, enabled, mode):
        self._operation(lambda: self._control("/api/resource-monitor", {"enabled": enabled, "mode": mode}))

    def calibrate(self, payload):
        self._operation(lambda: (self._control("/api/calibration", payload), self._message("相机参数已应用并回读确认")))

    def close(self):
        if not self.busy.acquire(blocking=False):
            self.events.put(("close_failed", "操作正在进行，完成后可再次关闭"))
            return
        def work():
            try:
                self._stop()
                self.events.put(("destroy", None))
            except Exception as exc:
                self.events.put(("close_failed", "停止未确认，窗口保持打开：" + connection_error(exc)))
            finally:
                self.busy.release()
        threading.Thread(target=work, daemon=True).start()
