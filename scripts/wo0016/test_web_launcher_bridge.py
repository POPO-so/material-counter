"""Integration tests for the self-contained Windows web launcher bridge."""
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def load_web_launcher():
    path = Path(__file__).resolve().parents[2] / "Python脚本" / "启动网页.pyw"
    module = types.ModuleType("web_launcher")
    module.__file__ = str(path)
    source = path.read_text(encoding="utf-8")
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


class RunningServer:
    def __init__(self, server):
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self.server

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


@pytest.mark.parametrize("path", [
    "/api/calibration", "/api/debug-mode", "/api/resource-monitor",
    "/api/reset-count", "/api/view-mode", "/api/stop",
])
def test_bridge_proxies_all_post_routes_and_preserves_upstream_response(path):
    launcher = load_web_launcher()
    requests = []
    request_body = b'{"value":"\xc3\xa9"}'

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers.get("Host"), self.headers.get("Origin"),
                             self.headers.get("Content-Type"), self.rfile.read(int(self.headers["Content-Length"]))))
            body = b'{"accepted":true}'
            self.send_response(207)
            self.send_header("Content-Type", "application/vnd.material+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    bridge = launcher.make_bridge("127.0.0.1", lambda report: None, port=0,
                                  board_port=upstream.server_port)
    with RunningServer(upstream), RunningServer(bridge):
        from http.client import HTTPConnection
        conn = HTTPConnection("127.0.0.1", bridge.server_port, timeout=3)
        conn.request("POST", path, body=request_body, headers={
            "Origin": f"http://127.0.0.1:{bridge.server_port}",
            "Host": f"127.0.0.1:{bridge.server_port}",
            "Content-Type": "application/json",
        })
        response = conn.getresponse()
        result_body = response.read()
        conn.close()

    assert response.status == 207
    assert response.getheader("Content-Type") == "application/vnd.material+json"
    assert result_body == b'{"accepted":true}'
    assert requests == [(path, f"127.0.0.1:{upstream.server_port}",
                         f"http://127.0.0.1:{upstream.server_port}", "application/json", request_body)]


@pytest.mark.parametrize("origin,content_type,length,expected_status", [
    ("http://attacker.invalid", "application/json", "2", 403),
    (None, "text/plain", "2", 400),
    (None, "application/json", "0", 400),
    (None, "application/json", "513", 400),
    (None, "application/json", "bogus", 400),
])
def test_bridge_rejects_invalid_post_without_contacting_upstream(
        origin, content_type, length, expected_status):
    launcher = load_web_launcher()
    contacted = threading.Event()

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            contacted.set()
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    bridge = launcher.make_bridge("127.0.0.1", lambda report: None, port=0,
                                  board_port=upstream.server_port)
    with RunningServer(upstream), RunningServer(bridge):
        from http.client import HTTPConnection
        conn = HTTPConnection("127.0.0.1", bridge.server_port, timeout=3)
        headers = {"Host": f"127.0.0.1:{bridge.server_port}", "Content-Type": content_type,
                   "Content-Length": length}
        if origin is None:
            headers["Origin"] = f"http://127.0.0.1:{bridge.server_port}"
        else:
            headers["Origin"] = origin
        # No unread payload on a rejected request: Windows may otherwise reset
        # the socket while the client is still reading the rejection response.
        conn.request("POST", "/api/calibration", body=b"", headers=headers)
        response = conn.getresponse()
        response.read()
        conn.close()

    assert response.status == expected_status
    assert not contacted.is_set()


@pytest.mark.parametrize("stream_path", ["/stream.mjpg", "/binary.mjpg"])
def test_mjpeg_proxy_streams_before_eof_and_survives_early_client_disconnect(stream_path):
    launcher = load_web_launcher()
    first = b"--frame\r\nContent-Type: image/jpeg\r\n\r\nfirst-frame\r\n"
    second = b"--frame\r\nContent-Type: image/jpeg\r\n\r\nsecond-frame\r\n"
    class Gate:
        def __init__(self):
            self.first_sent = threading.Event()
            self.allow_eof = threading.Event()
            self.upstream_eof = threading.Event()

    gates = [Gate(), Gate()]
    request_lock = threading.Lock()
    stream_requests = 0

    class Upstream(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            nonlocal stream_requests
            if self.path == stream_path:
                with request_lock:
                    gate = gates[stream_requests]
                    stream_requests += 1
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(first)
                self.wfile.flush()
                gate.first_sent.set()
                gate.allow_eof.wait(timeout=5)
                try:
                    self.wfile.write(second)
                    self.wfile.flush()
                except OSError:
                    pass
                finally:
                    gate.upstream_eof.set()
                    self.close_connection = True
                return
            payload = b"still-alive"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    bridge = launcher.make_bridge("127.0.0.1", lambda report: None, port=0,
                                  board_port=upstream.server_port)
    with RunningServer(upstream), RunningServer(bridge):
        from http.client import HTTPConnection
        try:
            conn = HTTPConnection("127.0.0.1", bridge.server_port, timeout=3)
            conn.request("GET", stream_path, headers={"Host": f"127.0.0.1:{bridge.server_port}"})
            response = conn.getresponse()
            assert response.status == 200
            assert gates[0].first_sent.wait(timeout=2)
            assert not gates[0].upstream_eof.is_set()

            def read_exact(stream, size):
                result = bytearray()
                while len(result) < size:
                    part = stream.read(size - len(result))
                    if not part:
                        break
                    result.extend(part)
                return bytes(result)

            assert read_exact(response, len(first)) == first
            assert not gates[0].upstream_eof.is_set()
            gates[0].allow_eof.set()
            assert response.read() == second
            conn.close()
            assert gates[0].upstream_eof.wait(timeout=2)

            # Close the next client while the upstream is still holding the stream open.
            disconnected = HTTPConnection("127.0.0.1", bridge.server_port, timeout=3)
            disconnected.request("GET", stream_path,
                                 headers={"Host": f"127.0.0.1:{bridge.server_port}"})
            disconnected_response = disconnected.getresponse()
            assert disconnected_response.status == 200
            assert gates[1].first_sent.wait(timeout=2)
            assert read_exact(disconnected_response, 8) == first[:8]
            assert not gates[1].upstream_eof.is_set()
            disconnected_response.close()
            disconnected.close()
            gates[1].allow_eof.set()
            assert gates[1].upstream_eof.wait(timeout=2)

            check = HTTPConnection("127.0.0.1", bridge.server_port, timeout=3)
            check.request("GET", "/status.json",
                          headers={"Host": f"127.0.0.1:{bridge.server_port}"})
            health = check.getresponse()
            assert health.status == 200
            assert health.read() == b"still-alive"
            check.close()
        finally:
            for gate in gates:
                gate.allow_eof.set()


def test_bridge_returns_503_when_board_connection_fails():
    launcher = load_web_launcher()
    from http.client import HTTPConnection
    import socket
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        unused_port = unused.getsockname()[1]
    bridge = launcher.make_bridge("127.0.0.1", lambda report: None, port=0, board_port=unused_port)
    with RunningServer(bridge):
        conn = HTTPConnection("127.0.0.1", bridge.server_port, timeout=3)
        conn.request("GET", "/status.json", headers={"Host": f"127.0.0.1:{bridge.server_port}"})
        response = conn.getresponse()
        body = response.read()
        conn.close()
    assert response.status == 503
    assert b"board unavailable" in body
