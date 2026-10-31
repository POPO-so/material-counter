import json
import sys
import types
import uuid
from pathlib import Path

import pytest

from scripts.standalone.boot_policy import build_argv, initialize_once
# The boot entry is Linux-only; its backend unit tests do not exercise locking.
_needs_fcntl_stub = sys.platform == "win32" and "fcntl" not in sys.modules
if _needs_fcntl_stub:
    sys.modules["fcntl"] = types.ModuleType("fcntl")
try:
    from scripts.standalone.boot_integrated import BoardBackend
finally:
    if _needs_fcntl_stub:
        del sys.modules["fcntl"]


class MockBackend:
    def __init__(self, *, configure_error=None, verify_error=None):
        self.calls = []
        self.configure_error = configure_error
        self.verify_error = verify_error

    def audit(self):
        self.calls.append("audit")

    def configure(self):
        self.calls.append("configure")
        if self.configure_error:
            raise self.configure_error

    def verify_loaded(self):
        self.calls.append("verify_loaded")
        if self.verify_error:
            raise self.verify_error


def read_marker(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_early_boot_configures_once_then_ready_marker_reuses_without_reload(tmp_path):
    marker = tmp_path / "boot.json"
    boot = str(uuid.uuid4())
    first_backend = MockBackend()

    first = initialize_once(first_backend, marker, boot, 12.5)

    assert first_backend.calls == ["audit", "configure", "verify_loaded"]
    assert first == {"configured": True, "boot_id": boot, "phase": "READY", "mode": "configured"}
    assert read_marker(marker) == {"boot_id": boot, "phase": "READY"}

    second_backend = MockBackend()
    second = initialize_once(second_backend, marker, boot, 500)

    assert second_backend.calls == ["audit", "verify_loaded"]
    assert second["configured"] is False
    assert second["phase"] == "READY"


@pytest.mark.parametrize("uptime", [-0.01, 300.01, float("inf"), float("nan")])
def test_late_or_nonfinite_first_boot_is_rejected_without_configuring(tmp_path, uptime):
    backend = MockBackend()
    marker = tmp_path / "boot.json"

    with pytest.raises(ValueError):
        initialize_once(backend, marker, str(uuid.uuid4()), uptime)

    assert backend.calls == ["audit"]
    assert not marker.exists()


def test_configure_failure_marks_failed_and_same_boot_never_retries(tmp_path):
    marker = tmp_path / "boot.json"
    boot = str(uuid.uuid4())
    first_backend = MockBackend(configure_error=RuntimeError("configure failed"))

    with pytest.raises(RuntimeError, match="configure failed"):
        initialize_once(first_backend, marker, boot, 20)

    assert first_backend.calls == ["audit", "configure"]
    assert read_marker(marker) == {"boot_id": boot, "phase": "FAILED"}

    second_backend = MockBackend()
    with pytest.raises(RuntimeError):
        initialize_once(second_backend, marker, boot, 20)
    assert second_backend.calls == ["audit"]


def test_verify_failure_marks_failed_and_never_configures_again(tmp_path):
    marker = tmp_path / "boot.json"
    boot = str(uuid.uuid4())
    backend = MockBackend(verify_error=RuntimeError("verify failed"))

    with pytest.raises(RuntimeError, match="verify failed"):
        initialize_once(backend, marker, boot, 20)

    assert backend.calls == ["audit", "configure", "verify_loaded"]
    assert read_marker(marker) == {"boot_id": boot, "phase": "FAILED"}

    retry_backend = MockBackend()
    with pytest.raises(RuntimeError):
        initialize_once(retry_backend, marker, boot, 20)
    assert retry_backend.calls == ["audit"]


def test_reuse_loaded_allows_late_boot_but_verify_failure_never_falls_back(tmp_path):
    marker = tmp_path / "boot.json"
    boot = str(uuid.uuid4())
    backend = MockBackend()

    result = initialize_once(backend, marker, boot, 900, reuse_loaded=True)

    assert backend.calls == ["audit", "verify_loaded"]
    assert result["configured"] is False
    assert result["mode"] == "reuse_loaded"
    assert read_marker(marker) == {"boot_id": boot, "phase": "READY"}

    failed_marker = tmp_path / "failed.json"
    failing_backend = MockBackend(verify_error=RuntimeError("verify failed"))
    with pytest.raises(RuntimeError, match="verify failed"):
        initialize_once(failing_backend, failed_marker, str(uuid.uuid4()), 900, reuse_loaded=True)
    assert failing_backend.calls == ["audit", "verify_loaded"]
    assert read_marker(failed_marker)["phase"] == "FAILED"


def test_marker_for_different_boot_is_rejected_after_audit(tmp_path):
    marker = tmp_path / "boot.json"
    marker.write_text(json.dumps({"boot_id": str(uuid.uuid4()), "phase": "READY"}), encoding="utf-8")
    backend = MockBackend()

    with pytest.raises(RuntimeError):
        initialize_once(backend, marker, str(uuid.uuid4()), 10)

    assert backend.calls == ["audit"]


def test_build_argv_builds_dynamic_paths_and_continuous_mode(tmp_path):
    boot = str(uuid.uuid4())
    digest = "aB" * 32
    config = {
        "schema": "material-counter.integrated-boot.v1",
        "mode": "continuous",
        "camera": "/dev/v4l/by-id/usb-camera0",
        "duration_s": 75,
    }

    argv = build_argv(tmp_path, config, boot, tmp_path / "out dir", digest, "/opt/python")

    assert argv == [
        "/opt/python", "-B", "-m", "scripts.standalone.integrated_run", "--execute",
        "--manifest", str(tmp_path / "export" / "display-manifest.json"),
        "--manifest-sha256", digest, "--boot-id", boot,
        "--native-lib", str(tmp_path / "native" / "rle_components_v2.so"),
        "--output", str(tmp_path / "out dir"), "--source", "camera", "--display", "on",
        "--duration", "75", "--frames", "200000", "--camera", config["camera"],
        "--font", str(tmp_path / "fonts" / "DroidSansFallbackFull.ttf"),
        "--latin-font", str(tmp_path / "fonts" / "DejaVuSans.ttf"), "--continuous",
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "other"},
        {"extra": "not allowed"},
        {"mode": "invalid"},
        {"duration_s": True},
        {"duration_s": 0},
        {"duration_s": 7201},
        {"camera": "/dev/video0"},
        {"camera": "/dev/v4l/by-id/nested/camera"},
        {"camera": "/dev/v4l/by-id/../video0"},
    ],
)
def test_build_argv_rejects_invalid_config_or_camera(change, tmp_path):
    config = {
        "schema": "material-counter.integrated-boot.v1",
        "mode": "acceptance",
        "camera": "/dev/v4l/by-id/camera0",
        "duration_s": 60,
    }
    config.update(change)

    with pytest.raises((TypeError, ValueError)):
        build_argv(tmp_path, config, str(uuid.uuid4()), "out", "f" * 64, "python")


@pytest.mark.parametrize("digest", ["f" * 63, "g" * 64])
def test_build_argv_rejects_invalid_digest(digest, tmp_path):
    config = {
        "schema": "material-counter.integrated-boot.v1",
        "mode": "acceptance",
        "camera": "/dev/v4l/by-id/camera0",
        "duration_s": 60,
    }
    with pytest.raises(ValueError):
        build_argv(tmp_path, config, str(uuid.uuid4()), "out", digest, "python")


def test_build_argv_normalizes_boot_uuid_and_acceptance_has_no_continuous_flag(tmp_path):
    boot = uuid.uuid4()
    config = {
        "schema": "material-counter.integrated-boot.v1",
        "mode": "acceptance",
        "camera": "/dev/v4l/by-id/camera0",
        "duration_s": 60,
    }

    argv = build_argv(tmp_path, config, "{" + str(boot).upper() + "}", "out", "f" * 64, "python")

    assert argv[argv.index("--boot-id") + 1] == str(boot)
    assert "--continuous" not in argv


@pytest.mark.parametrize("download_error", [None, RuntimeError("download failed")])
@pytest.mark.parametrize("relative_bit_path", [False, True])
def test_board_backend_downloads_into_runtime_and_restores_cwd(
    tmp_path, monkeypatch, download_error, relative_bit_path
):
    release = tmp_path / "release"
    runtime = tmp_path / "runtime" / "session-1"
    original_cwd = tmp_path / "caller"
    release.mkdir()
    runtime.mkdir(parents=True)
    original_cwd.mkdir()
    bit = original_cwd / "design.bit" if relative_bit_path else release / "design.bit"
    bit.write_bytes(b"bitstream")
    backend_bit = Path("design.bit") if relative_bit_path else bit
    seen_paths = []

    class FakeOverlay:
        def __init__(self, bit_path, download):
            seen_paths.append(bit_path)
            assert download is True
            Path("loaded.xclbin").write_bytes(b"generated xclbin")
            if download_error:
                raise download_error

    class FakeClocks:
        pass

    monkeypatch.setitem(sys.modules, "pynq", types.SimpleNamespace(Overlay=FakeOverlay, Clocks=FakeClocks))
    backend = BoardBackend(release, {"bit": backend_bit}, {}, runtime)
    monkeypatch.chdir(original_cwd)

    if download_error:
        with pytest.raises(RuntimeError, match="download failed"):
            backend.configure()
    else:
        backend.configure()

    assert Path.cwd() == original_cwd
    assert seen_paths == [str(bit.resolve())]
    assert (runtime / "loaded.xclbin").read_bytes() == b"generated xclbin"
    assert not (release / "loaded.xclbin").exists()
