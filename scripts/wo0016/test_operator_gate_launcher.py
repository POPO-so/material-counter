import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

import operator_gate_launcher
from operator_gate_launcher import OperatorGate, make_server


def test_script_entry_uses_the_same_registered_gate_module_as_runner(tmp_path):
    launcher = Path(__file__).with_name("operator_gate_launcher.py")
    program = r'''
import runpy, sys
sys.path.insert(0, sys.argv[1])
import run_stream_counter as runner
def check_gate(argv, *, operator_gate_sha256, operator_gate):
    runner.validate_operator_gate_launcher_sha256(operator_gate_sha256, operator_gate)
    print("SCRIPT_GATE_IDENTITY_PASS")
    return 0
runner.main = check_gate
launcher, attempt = sys.argv[2:4]
sys.argv = [launcher, "--attempt-dir", attempt, "--control-port", "0", "--",
            "--mode", "mock", "--evidence-json", attempt + "/result.json"]
runpy.run_path(launcher, run_name="__main__")
'''
    completed = subprocess.run(
        [sys.executable, "-c", program, str(launcher.parent), str(launcher), str(tmp_path)],
        capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    assert "SCRIPT_GATE_IDENTITY_PASS" in completed.stdout


def test_failed_executor_is_reported_as_failed_instead_of_sampling_complete():
    gate = OperatorGate()
    gate.finish({"status": "FAILED", "frames_processed": 0})
    assert gate.snapshot()["phase"] == "FAILED"


def test_executor_without_explicit_success_status_is_failed():
    for result in ({}, {"status": None}, {"status": "UNKNOWN"}):
        gate = OperatorGate()
        gate.finish(result)
        assert gate.snapshot()["phase"] == "FAILED"


def test_preflight_standby_waits_for_authenticated_click_without_countdown(monkeypatch):
    gate, server, thread, url = start_server(monkeypatch, require_permit_sha256=True)
    gate.bind_permit_sha256("a" * 64)
    finished = threading.Event()
    waiter = threading.Thread(target=lambda: (gate.wait_for_preflight_start(), finished.set()), daemon=True)
    waiter.start()
    try:
        for _ in range(100):
            if gate.snapshot()["phase"] == "READY":
                break
            time.sleep(.005)
        assert not finished.is_set()
        assert gate.deadline is None
        assert post_start(url, "secret") == 200
        assert finished.wait(1)
        assert gate.snapshot()["phase"] == "PREFLIGHT"
        assert gate.deadline is None
    finally:
        stop_server(server, thread)


def test_b1_standby_page_instructs_empty_belt_instead_of_material_feeding():
    gate = OperatorGate()
    server = make_server(gate, port=0, profile="b1", standby=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/").read().decode()
        assert "B1 · Empty Belt" in page
        assert "115 FRAMES" in page
        assert "B1 全程保持空带" in page
        assert "M6 Material Run" not in page
        assert "立即投料" not in page
        assert "第 '+s.feed_number" not in page
    finally:
        stop_server(server, thread)


def test_physical_calibration_standby_page_is_one_shot_and_has_no_formal_permit():
    gate = OperatorGate(require_control_binding=True)
    gate.bind_control_sha256("e" * 64, label="calibration lock")
    server = make_server(gate, port=0, profile="calibration", standby=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/").read().decode()
        state = json.load(urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/status"))
        assert "Physical Calibration" in page
        assert "300 FRAMES" in page
        assert "NO FORMAL PERMIT" in page
        assert "Calibration lock · SHA-256" in page
        assert "NO FORMAL PERMIT" in page
        assert "s.control_binding_sha256" in page
        assert 'id="passcode"' in page
        assert "本标定页面不要求操作口令" not in page
        assert state["control_binding_label"] == "calibration lock"
        assert state["permit_sha256"] is None
    finally:
        stop_server(server, thread)


def test_physical_calibration_requires_a_single_use_passcode(monkeypatch):
    passcode = "fresh-calibration-code"
    monkeypatch.setattr("operator_gate_launcher.secrets.token_urlsafe",
                        lambda _length: passcode)
    gate = OperatorGate(require_control_binding=True)
    gate.bind_control_sha256("f" * 64, label="calibration lock")
    server = make_server(gate, port=0, profile="calibration", standby=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        page = urllib.request.urlopen(url).read().decode()
        assert 'id="passcode"' in page
        assert "本标定页面不要求操作口令" not in page
        assert "仅监听本机回环" in page
        gate.mark_ready()
        for candidate in (None, "wrong-code"):
            try:
                post_start(url, candidate)
            except urllib.error.HTTPError as error:
                assert error.code == 401
            else:
                raise AssertionError("calibration start must reject a missing or wrong passcode")
        assert post_start(url, passcode) == 200
        try:
            post_start(url, passcode)
        except urllib.error.HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("a second calibration start must be rejected")
    finally:
        stop_server(server, thread)


def test_physical_calibration_audits_worker_before_opening_control_listener(
        tmp_path, monkeypatch):
    calls = []

    def reject_worker(*_args, **_kwargs):
        calls.append("worker")
        raise RuntimeError("worker environment audit failed")

    def unexpected_listener(*_args, **_kwargs):
        calls.append("listener")
        raise AssertionError("8081 listener must not open after a failed runtime audit")

    monkeypatch.setattr(operator_gate_launcher, "verify_worker_environment",
                        reject_worker, raising=False)
    monkeypatch.setattr(operator_gate_launcher, "make_server", unexpected_listener)

    with pytest.raises(RuntimeError, match="worker environment audit failed"):
        operator_gate_launcher.main([
            "--attempt-dir", str(tmp_path), "--control-port", "0",
            "--standby-before-preflight", "--", "--mode", "board",
            "--physical-calibration", "--worker-python", sys.executable,
        ])

    assert calls == ["worker"]


def test_physical_calibration_without_passcode_rejects_non_loopback_host(monkeypatch):
    monkeypatch.setattr(
        "operator_gate_launcher.secrets.token_urlsafe",
        lambda _length: "test-only-code",
    )

    class NoBindServer(operator_gate_launcher.ThreadingHTTPServer):
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("must reject the host before binding a socket")

    monkeypatch.setattr("operator_gate_launcher.ThreadingHTTPServer", NoBindServer)
    try:
        make_server(OperatorGate(), port=0, host="0.0.0.0", profile="calibration")
    except ValueError as error:
        assert "loopback" in str(error)
    else:
        raise AssertionError("no-passcode calibration control must remain loopback-only")


def start_server(monkeypatch, passcode="secret", **gate_kwargs):
    monkeypatch.setattr("operator_gate_launcher.secrets.token_urlsafe", lambda _n: passcode)
    gate = OperatorGate(**gate_kwargs)
    server = make_server(gate, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return gate, server, thread, f"http://127.0.0.1:{server.server_port}"


def post_start(url, passcode):
    body = (b"{}" if passcode is None else
            json.dumps({"operator_passcode": passcode}).encode())
    request = urllib.request.Request(
        url + "/start", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(request).status


def stop_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_gate_requires_camera_ready_and_accepts_only_one_authenticated_click(monkeypatch):
    gate, server, thread, url = start_server(monkeypatch)
    try:
        try:
            post_start(url, "wrong")
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("incorrect passcode must be rejected")
        assert gate.snapshot()["phase"] == "INITIALIZING"

        try:
            post_start(url, "secret")
        except urllib.error.HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("a correct passcode must wait until the camera is ready")
        gate.mark_ready()
        assert gate.snapshot()["phase"] == "READY"
        assert post_start(url, "secret") == 200
        try:
            post_start(url, "secret")
        except urllib.error.HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("a second click must not start another attempt")
        assert gate._start_event.is_set()
    finally:
        stop_server(server, thread)


def test_browser_countdown_and_feed_ticks_use_board_monotonic_time(monkeypatch):
    current = [100.0]
    gate, server, thread, url = start_server(
        monkeypatch, clock=lambda: current[0], countdown_s=18.0)
    errors = []

    def wait_for_click():
        try:
            gate.wait_for_operator(timeout_s=2.0)
        except BaseException as error:
            errors.append(error)

    waiter = threading.Thread(target=wait_for_click, daemon=True)
    waiter.start()
    try:
        deadline = time.monotonic() + 1.0
        while gate.snapshot()["phase"] != "READY" and time.monotonic() < deadline:
            time.sleep(0.005)
        assert gate.snapshot()["phase"] == "READY"
        assert post_start(url, "secret") == 200
        waiter.join(timeout=2)
        assert not waiter.is_alive()
        assert errors == []
        current[0] = 105.2
        assert gate.snapshot()["seconds_remaining"] == 13
        gate.milestone("FRAME_0_CAPTURE_START_FEED_NOW", 118.0)
        current[0] = 120.1
        assert gate.snapshot()["feed_number"] == 3
        gate.finish({"status": "BOARD_STREAM_COMPLETE", "total_count": 5, "frames_processed": 165})
        assert gate.snapshot()["phase"] == "COMPLETE"
        assert gate.snapshot()["result"]["total_count"] == 5
    finally:
        stop_server(server, thread)


def test_feed_number_advances_every_three_quarters_second_after_frame_zero():
    current = [100.0]
    gate = OperatorGate(clock=lambda: current[0])
    gate.milestone("FRAME_0_CAPTURE_START_FEED_NOW", 100.0)

    for elapsed, expected in (
        (0.0, 1), (0.74, 1), (0.75, 2), (1.49, 2),
        (1.5, 3), (2.24, 3), (2.25, 4), (3.0, 5), (3.5, 5),
    ):
        current[0] = 100.0 + elapsed
        assert gate.snapshot()["feed_number"] == expected


def test_ready_gate_times_out_without_starting_countdown():
    gate = OperatorGate()
    try:
        gate.wait_for_operator(timeout_s=0.01)
    except RuntimeError as error:
        assert "operator start" in str(error)
    else:
        raise AssertionError("gate must fail closed when no operator clicks")
    assert not gate._start_event.is_set()


def test_raw_event_cannot_forge_authorized_browser_request():
    gate = OperatorGate()
    gate._start_event.set()
    assert not hasattr(gate, "start_event")
    try:
        gate.wait_for_operator(timeout_s=0.01)
    except RuntimeError as error:
        assert "authorized browser request" in str(error)
    else:
        raise AssertionError("a raw event must not authorize sampling")


def test_control_page_does_not_disclose_passcode_and_click_starts_once(monkeypatch):
    gate, server, thread, url = start_server(monkeypatch, "test-passcode")
    try:
        page = urllib.request.urlopen(url).read().decode()
        assert "test-passcode" not in page
        assert 'type="password"' in page
        assert "15 秒准备 + 3 秒稳姿" in page
        assert "165 帧" in page
        assert "FRAME_0 后 0~3.5 秒内投完 5 颗" in page
        assert "约 0.75 秒/颗" in page
        assert "放在皮带纵向正中央，远离 ROI 上下边缘" in page
        state = json.load(urllib.request.urlopen(url + "/status"))
        assert state["phase"] == "INITIALIZING"
        gate.mark_ready()
        assert post_start(url, "test-passcode") == 200
        try:
            post_start(url, "test-passcode")
        except urllib.error.HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("a second click must not start another attempt")
    finally:
        stop_server(server, thread)


def test_dashboard_binds_permit_hash_without_disclosing_passcode(monkeypatch):
    digest = "a" * 64
    gate, server, thread, url = start_server(monkeypatch, "out-of-band-secret")
    try:
        gate.bind_permit_sha256(digest)
        page = urllib.request.urlopen(url).read().decode()
        status = json.load(urllib.request.urlopen(url + "/status"))

        assert "out-of-band-secret" not in page
        assert "out-of-band-secret" not in json.dumps(status)
        assert "s.permit_sha256" in page
        assert status["permit_sha256"] == digest
        assert digest not in page
    finally:
        stop_server(server, thread)


def test_permit_hash_binding_rejects_non_sha256_values():
    gate = OperatorGate()
    for value in ("", "not-a-digest", "A" * 64, "a" * 63):
        try:
            gate.bind_permit_sha256(value)
        except ValueError:
            pass
        else:
            raise AssertionError("only a lowercase SHA-256 digest may be bound")


def test_stageb2_gate_cannot_enter_ready_before_permit_hash_is_bound():
    gate = OperatorGate(require_permit_sha256=True)
    try:
        gate.mark_ready()
    except RuntimeError as error:
        assert "permit SHA-256" in str(error)
    else:
        raise AssertionError("a Stage B-2 gate must fail closed without permit binding")
    gate.bind_permit_sha256("b" * 64)
    gate.mark_ready()
    assert gate.snapshot()["phase"] == "READY"
    try:
        gate.bind_permit_sha256("c" * 64)
    except RuntimeError as error:
        assert "already bound" in str(error)
    else:
        raise AssertionError("permit identity must not be rebound during the run")


def test_start_rejects_missing_and_wrong_passcodes_without_starting_gate(monkeypatch):
    gate, server, thread, url = start_server(monkeypatch, "correct-passcode")
    gate.mark_ready()
    try:
        for candidate in (None, "wrong-passcode"):
            try:
                post_start(url, candidate)
            except urllib.error.HTTPError as error:
                assert error.code == 401
            else:
                raise AssertionError("missing and incorrect passcodes must be rejected")
            assert gate.snapshot()["phase"] == "READY"
            assert not gate._start_event.is_set()
    finally:
        stop_server(server, thread)


def test_control_server_defaults_to_loopback(monkeypatch):
    gate, server, thread, _url = start_server(monkeypatch, "test-passcode")
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        stop_server(server, thread)


def test_host_argument_defaults_to_loopback_and_accepts_explicit_override():
    from operator_gate_launcher import _parser

    parser = _parser()
    default = parser.parse_args(["--attempt-dir", "."])
    override = parser.parse_args(["--attempt-dir", ".", "--host", "0.0.0.0"])

    assert default.host == "127.0.0.1"
    assert override.host == "0.0.0.0"


def test_probe_health_failure_aborts_operator_wait_without_starting_sampling():
    gate = OperatorGate()
    def unhealthy():
        raise RuntimeError('diagnostic sampler lost during operator wait')
    gate.probe_health_check = unhealthy
    try:
        gate.wait_for_operator(timeout_s=0.01)
    except RuntimeError as error:
        assert 'diagnostic sampler lost' in str(error)
    else:
        raise AssertionError('health failure must abort the probe handoff')
    assert not gate._start_event.is_set()


def test_probe_launcher_pins_control_surface_and_attempt_directory(tmp_path):
    from types import SimpleNamespace
    from operator_gate_launcher import validate_probe_launcher_options
    run = SimpleNamespace(evidence_json=str(tmp_path / 'result.json'))
    args = SimpleNamespace(host='127.0.0.1', control_port=8081, handoff_timeout_s=300,
                           standby_before_preflight=False, attempt_dir=tmp_path)
    validate_probe_launcher_options(args, run)
    for name, value in [('host', '0.0.0.0'), ('control_port', 8082),
                        ('handoff_timeout_s', 301), ('standby_before_preflight', True),
                        ('attempt_dir', tmp_path / 'other')]:
        changed = SimpleNamespace(**{**vars(args), name: value})
        try:
            validate_probe_launcher_options(changed, run)
        except ValueError as error:
            assert 'diagnostic launcher' in str(error)
        else:
            raise AssertionError('probe launcher option drift was accepted')
