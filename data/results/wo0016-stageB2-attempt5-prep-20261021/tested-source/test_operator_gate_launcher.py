import threading
import json
import urllib.error
import urllib.request

from operator_gate_launcher import OperatorGate, make_server


def test_gate_requires_camera_ready_and_accepts_only_one_click():
    gate = OperatorGate(clock=lambda: 100.0)
    assert gate.request_start() is False
    gate.mark_ready()
    assert gate.snapshot()["phase"] == "READY"
    assert gate.request_start() is True
    assert gate.request_start() is False
    assert gate.start_event.is_set()


def test_browser_countdown_and_feed_ticks_use_board_monotonic_time():
    current = [100.0]
    gate = OperatorGate(clock=lambda: current[0])
    gate.mark_ready()
    gate.request_start()
    gate.begin_countdown(18.0)
    current[0] = 105.2
    assert gate.snapshot()["seconds_remaining"] == 13
    gate.milestone("FRAME_0_CAPTURE_START_FEED_NOW", 118.0)
    current[0] = 120.1
    assert gate.snapshot()["feed_number"] == 3
    gate.finish({"total_count": 5, "frames_processed": 165})
    assert gate.snapshot()["phase"] == "COMPLETE"
    assert gate.snapshot()["result"]["total_count"] == 5


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
    assert not gate.start_event.is_set()


def test_control_page_click_starts_only_the_ready_single_attempt():
    gate = OperatorGate()
    server = make_server(gate, port=0, token="test-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        page = urllib.request.urlopen(url).read().decode()
        assert "15 秒准备 + 3 秒稳姿" in page
        assert "165 帧" in page
        assert "FRAME_0 后 0~3.5 秒内投完 5 颗" in page
        assert "约 0.75 秒/颗" in page
        assert "放在皮带纵向正中央，远离 ROI 上下边缘" in page
        state = json.load(urllib.request.urlopen(url + "/status"))
        assert state["phase"] == "INITIALIZING"
        gate.mark_ready()
        request = urllib.request.Request(url + "/start", method="POST",
                                         headers={"X-Operator-Token": "test-token"})
        assert urllib.request.urlopen(request).status == 200
        try:
            urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("a second click must not start another attempt")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
