"""Checks for independent deployment configuration and desktop control client."""
from pathlib import Path
import json
import sys

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
