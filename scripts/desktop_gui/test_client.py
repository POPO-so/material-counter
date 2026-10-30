"""Checks for independent deployment configuration and desktop control client."""
from pathlib import Path
import json
import logging
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controller import DesktopController, load_config, validate_config
import controller


def sample_config():
    path = Path(__file__).resolve().parents[2] / "Python脚本" / "RLE桌面部署.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def test_config_is_loaded_from_existing_deployment_without_web_launcher():
    config = load_config()
    assert config["host"] == sample_config()["host"]
    assert config["board_port"] == sample_config()["board_port"]
    assert config["protocol"] == "RLE_PACKET_V1"
    assert config["fpga_resources"]["lut"] == [5809, 53200]


@pytest.mark.parametrize("key,value", [
    ("board_port", 0), ("host", "bad host"), ("manifest", "../other.json"),
    ("bit_sha256", "unchecked"), ("runtime_root", "/tmp/../etc"),
])
def test_invalid_configuration_is_rejected_before_board_operations(key, value):
    config = sample_config()
    config[key] = value
    with pytest.raises(ValueError):
        validate_config(config)


def test_legacy_config_without_protocol_remains_valid():
    config = sample_config()
    config.pop("protocol")
    config.pop("fpga_resources")
    assert validate_config(config)["protocol"] == "GRAY8_PACKET_V1"


def test_stop_refuses_to_control_a_different_session(monkeypatch):
    class Root:
        def after(self, *_args):
            pass
    app = DesktopController(Root(), sample_config())
    app.session = {"id": "ours", "token": "token", "dir": "/ours", "pid": 12}
    calls = []
    def request(_config, _token, method, path, payload=None, **kwargs):
        calls.append((method, path))
        return {"session_id": "someone-else"}
    monkeypatch.setattr(controller, "_request", request)
    with pytest.raises(RuntimeError, match="会话"):
        app._stop()
    assert calls == [("GET", "/status.json")]
    assert app.session["id"] == "ours"


def test_stop_accepts_already_stopped_matching_session(monkeypatch):
    class Root:
        def after(self, *_args):
            pass
    app = DesktopController(Root(), sample_config())
    app.session = {"id": "ours", "token": "token", "dir": "/ours", "pid": 12}
    def disconnected(*_args, **_kwargs):
        raise ConnectionRefusedError("service already stopped")
    monkeypatch.setattr(controller, "_request", disconnected)
    monkeypatch.setattr(controller, "read_remote_result", lambda _config, _path: {
        "session_id": "ours", "status": "STOPPED", "standard_profile_restored": True, "total_count": 4})
    app._stop()
    assert app.session is None


def test_log_event_redacts_secret_fields(caplog):
    caplog.set_level(logging.INFO)
    controller._log_event(
        "test.redaction", session_id="session-1", password="pw-hidden",
        token="token-hidden", authorization="Bearer header-hidden",
    )

    record = caplog.records[-1]
    assert "desktop_event=" in record.message
    assert '"session_id": "session-1"' in record.message
    assert "pw-hidden" not in record.message
    assert "token-hidden" not in record.message
    assert "header-hidden" not in record.message


def test_ssh_failure_logs_stage_and_exception_type(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    class Client:
        def load_system_host_keys(self):
            pass
        def set_missing_host_key_policy(self, _policy):
            pass
        def connect(self, *_args, **_kwargs):
            raise TimeoutError("sensitive password text")

    fake_paramiko = SimpleNamespace(
        SSHClient=Client, RejectPolicy=lambda: object(),
    )
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)

    with pytest.raises(TimeoutError):
        controller._ssh(sample_config(), session_id="session-ssh")

    messages = "\n".join(record.message for record in caplog.records)
    assert "ssh.failure" in messages
    assert "TimeoutError" in messages
    assert "session-ssh" in messages
    assert "sensitive password text" not in messages


def test_http_403_log_contains_status_without_token(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    class Response:
        status = 403
        def read(self):
            return b'{"error":"forbidden"}'

    class Connection:
        def __init__(self, *_args, **_kwargs):
            pass
        def request(self, *_args, **_kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass

    monkeypatch.setattr(controller.http.client, "HTTPConnection", Connection)
    with pytest.raises(RuntimeError):
        controller._request(
            sample_config(), "secret-token", "GET", "/status.json",
            expected_session_id="session-403",
        )

    messages = "\n".join(record.message for record in caplog.records)
    assert "http.response" in messages
    assert "403" in messages
    assert "/status.json" in messages
    assert "secret-token" not in messages


def test_readiness_session_mismatch_logs_both_ids(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    class Response:
        status = 200
        def read(self):
            return b'{"session_id":"other-session"}'

    class Connection:
        def __init__(self, *_args, **_kwargs):
            pass
        def request(self, *_args, **_kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass

    monkeypatch.setattr(controller.http.client, "HTTPConnection", Connection)
    with pytest.raises(RuntimeError, match="会话"):
        controller._request(
            sample_config(), "secret-token", "GET", "/status.json",
            expected_session_id="expected-session",
        )

    messages = "\n".join(record.message for record in caplog.records)
    assert "expected-session" in messages
    assert "other-session" in messages
    assert "secret-token" not in messages


def test_status_poll_mismatch_logs_expected_and_actual_session(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    class Root:
        def after(self, *_args):
            pass

    app = DesktopController(Root(), sample_config())
    app.session = {"id": "expected-session", "token": "private-token", "dir": "/session", "pid": 12}
    monkeypatch.setattr(controller, "_request", lambda *_args, **_kwargs: {
        "session_id": "actual-session",
    })
    monkeypatch.setattr(controller.time, "sleep", lambda _duration: app.shutdown.set())

    app._poll("expected-session")

    messages = "\n".join(record.message for record in caplog.records)
    assert '"event": "status.session_mismatch"' in messages
    assert "expected-session" in messages
    assert "actual-session" in messages
    assert "private-token" not in messages


def test_successful_status_poll_stays_structurally_quiet(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    class Root:
        def after(self, *_args):
            pass

    app = DesktopController(Root(), sample_config())
    app.session = {"id": "same-session", "token": "private-token", "dir": "/session", "pid": 12}
    monkeypatch.setattr(controller, "_request", lambda *_args, **_kwargs: {
        "session_id": "same-session", "telemetry": {"phase": "running"},
    })
    monkeypatch.setattr(controller.time, "sleep", lambda _duration: app.shutdown.set())

    app._poll("same-session")

    assert not any("desktop_event=" in record.message for record in caplog.records)


def test_frame_session_mismatch_log_omits_frame_content(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    class Root:
        def after(self, *_args):
            pass

    class Response:
        status = 200
        def close(self):
            pass

    class Connection:
        def __init__(self, *_args, **_kwargs):
            pass
        def request(self, *_args, **_kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass

    app = DesktopController(Root(), sample_config())
    app.session = {"id": "expected-frame-session", "token": "private-token", "dir": "/session", "pid": 12}
    monkeypatch.setattr(controller.http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(controller, "read_frame", lambda _response: {
        "session_id": "actual-frame-session", "frame_id": 8,
        "image": b"private-frame-bytes",
    })
    monkeypatch.setattr(controller.time, "sleep", lambda _duration: app.shutdown.set())

    app._receive("expected-frame-session")

    messages = "\n".join(record.message for record in caplog.records)
    assert '"event": "frames.session_mismatch"' in messages
    assert "expected-frame-session" in messages
    assert "actual-frame-session" in messages
    assert "private-frame-bytes" not in messages
