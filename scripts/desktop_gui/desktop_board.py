"""Single-camera desktop transport and the existing software counter on PYNQ."""
from __future__ import annotations

from dataclasses import asdict
import json
import threading
import time

from live_monitor_state import LiveMonitorState


class DesktopRuntime:
    """Process every camera frame; publish bounded snapshots only for viewers."""

    def __init__(self, session_id, preprocessor, counter, hub, *, clock=time.monotonic):
        self.session_id = session_id
        self.preprocessor = preprocessor
        self.counter = counter
        self.hub = hub
        self.clock = clock
        self.state = LiveMonitorState(mode="mock", clock=clock)
        self.frame_id = 0
        self.last_publish = None
        self.debug_enabled = False
        self.calibration_controls = None
        self.resources = None
        self.control_lock = threading.Lock()
        self._pending_calibration = None

    @property
    def has_pending_calibration(self):
        with self.control_lock:
            return self._pending_calibration is not None

    def _apply_pending_calibration(self):
        with self.control_lock:
            pending = self._pending_calibration
            self._pending_calibration = None
        if pending is None:
            return
        try:
            pending["result"] = self.calibration_controls.update(pending["payload"])
        except Exception as exc:
            pending["error"] = exc
        finally:
            pending["done"].set()

    def process_frame(self, source):
        captured_at_ns = time.time_ns()
        self._apply_pending_calibration()
        gray, mask = self.preprocessor(source)
        result = self.counter.update(self.frame_id, mask)
        displayed = self.state.complete_frame(result)
        now = self.clock()
        if self.hub.client_count and (self.last_publish is None or now - self.last_publish >= 0.05):
            self.hub.publish({"session_id": self.session_id, "frame_id": result.frame_id,
                              "source": "software", "gray": gray, "mask": mask,
                              "result": asdict(displayed),
                              "config": {"line_x": self.counter.line_x,
                                         "count_axis": self.counter.count_axis,
                                         "roi_top": result.roi_top,
                                         "roi_bottom": result.roi_bottom,
                                         "threshold": self.preprocessor.threshold,
                                         "mapping": self.preprocessor.mapping},
                              "capture_time_ns": captured_at_ns})
            self.last_publish = now
        self.frame_id += 1
        return displayed

    def status(self):
        raw = self.state.snapshot()
        telemetry = raw["dashboard"]
        telemetry["camera"] = "frames_received" if not telemetry["stale"] else "unknown"
        telemetry["source"] = "software"
        telemetry["mode"] = "camera"
        running = telemetry["phase"] == "running"
        return {"schema": 1, "session_id": self.session_id, "telemetry": telemetry,
                "runtime_status": raw["status"],
                "preview": {"width": 320, "height": 240},
                "resource_monitor": self.resources.snapshot() if self.resources else None,
                "camera_controls": (dict(self.calibration_controls.values)
                                    if self.calibration_controls else None),
                "debug": {"enabled": self.debug_enabled},
                "capabilities": {"start": False, "stop": running, "reset": running,
                                 "view_mode": True, "calibration": self.calibration_controls is not None}}

    def control(self, action, payload):
        if action in ("reset", "stop", "view"):
            return self.state.control(action, payload)
        if action == "debug":
            if set(payload) != {"enabled"} or type(payload["enabled"]) is not bool:
                raise ValueError("expected boolean enabled")
            self.debug_enabled = payload["enabled"]
            return {"enabled": self.debug_enabled}
        if action == "resource" and self.resources is not None:
            if set(payload) != {"enabled", "mode"}:
                raise ValueError("expected enabled and mode")
            return self.resources.control(enabled=payload["enabled"], mode=payload["mode"])
        if action == "calibration" and self.calibration_controls is not None:
            pending = {"payload": payload, "done": threading.Event()}
            with self.control_lock:
                if self._pending_calibration is not None:
                    raise RuntimeError("相机参数更新正在等待帧边界")
                self._pending_calibration = pending
            if not pending["done"].wait(4):
                with self.control_lock:
                    if self._pending_calibration is pending:
                        self._pending_calibration = None
                raise TimeoutError("相机参数更新超时，状态待确认")
            if "error" in pending:
                raise pending["error"]
            return pending["result"]
        raise ValueError("unknown or unavailable control")


class DesktopServer:
    """HTTP control and one framed binary stream, protected by a session token."""

    def __init__(self, runtime, token, host="127.0.0.1", port=0):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import hmac
        import socket
        import select

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def authorized(self):
                value = self.headers.get("Authorization", "")
                return hmac.compare_digest(value, "Bearer " + token)

            def reply(self, code, data):
                payload = json.dumps(data, allow_nan=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
                self.close_connection = True

            def do_GET(self):
                if not self.authorized():
                    return self.reply(403, {"error": "unauthorized"})
                if self.path == "/status.json":
                    return self.reply(200, runtime.status())
                if self.path != "/api/frames":
                    return self.reply(404, {"error": "not found"})
                self.send_response(200)
                self.send_header("Content-Type", "application/x-material-frames-v1")
                self.send_header("Connection", "close")
                self.end_headers()
                self.connection.settimeout(2)
                sequence = 0
                try:
                    with runtime.hub.client():
                        while not runtime.state.stop.is_set():
                            item = runtime.hub.wait_next(sequence, timeout_s=1.0)
                            if item is None:
                                if select.select([self.connection], [], [], 0)[0] and not self.connection.recv(1, socket.MSG_PEEK):
                                    break
                                continue
                            sequence, packet = item
                            self.wfile.write(packet)
                            self.wfile.flush()
                except (OSError, ConnectionError):
                    pass
                self.close_connection = True

            def do_POST(self):
                if not self.authorized():
                    return self.reply(403, {"error": "unauthorized"})
                actions = {"/api/reset-count": "reset", "/api/stop": "stop",
                           "/api/view-mode": "view", "/api/debug-mode": "debug",
                           "/api/resource-monitor": "resource", "/api/calibration": "calibration"}
                if self.path not in actions:
                    return self.reply(404, {"error": "not found"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 512:
                        raise ValueError("invalid request length")
                    self.connection.settimeout(2)
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ValueError("JSON object required")
                    result = runtime.control(actions[self.path], payload)
                    self.reply(200, result)
                except (ValueError, TypeError) as exc:
                    self.reply(400, {"error": str(exc)})
                except Exception as exc:
                    self.reply(503, {"error": str(exc)})

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self):
        return self._server.server_address[1]

    def start(self):
        self._thread.start()
        return self

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3)
