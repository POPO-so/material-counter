import asyncio
import hashlib
import json
import sys
import threading
import time
from pathlib import Path
import urllib.request

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_stream_counter as runner  # noqa: E402


def test_formal_ready_id_selects_a_new_file_without_replacing_legacy_ready():
    boot = "35983d66-bb1d-4a8a-85e5-802170825515"
    base = Path("/var/lib/wo0017") / boot / "b1"
    assert runner.formal_ready_path(boot, "b1") == base / "ready.json"
    assert runner.formal_ready_path(boot, "b1", "b1-empty-04") == base / "ready-b1-empty-04.json"
    for unsafe in ("", "../ready", "a/b", "a\\b", ".", "x" * 33):
        with pytest.raises(ValueError, match="READY id"):
            runner.formal_ready_path(boot, "b1", unsafe)


def test_wo0017_profile_requires_capture_and_independent_one_shot_mode_before_board_access():
    args = runner._parser().parse_args(["--wo0017-profile", "b1"])
    with pytest.raises(ValueError, match="capture.*one-shot"):
        runner.formal_capture_context(args, None)


def test_wo0017_runtime_binding_covers_raw_writer_and_camera_controls():
    sources = runner.formal_runtime_sha256()
    assert {"capture_dataset.py", "live_calibration.py", "run_stream_counter.py",
            "operator_gate_launcher.py", "wo0017_capture_profile.py",
            "wo0015/board_preflight.py", "wo0015/board_once.py"} <= set(sources)
    assert all(len(value) == 64 for value in sources.values())
from run_stream_counter import (  # noqa: E402
    build_synthetic_masks,
    claim_once_guard,
    _dma_buffers_safe_to_release,
    _camera_gray_frames_impl,
    resolve_wo15_dependency_dir,
    run_mock_simulation,
    summarize_stage_timings_ms,
    verify_afi1_readback,
)
from stream_counter import MaskCounter  # noqa: E402


def test_five_rightward_nuts_cross_vertical_line_at_32_pixels_per_frame():
    counter = MaskCounter(count_axis="x", line_x=160, max_step=60,
                          max_lateral_step=10, min_area=32)
    events = []
    for frame in range(60):
        mask = np.zeros((240, 320), dtype=np.uint8)
        for launch in range(0, 50, 10):
            x = 32 + (frame - launch) * 32
            if launch <= frame and 5 <= x <= 314:
                mask[111:122, x-5:x+6] = 255
        events.extend(counter.update(frame, mask).events)
    assert counter.total_count == 5
    assert len({event.track_id for event in events}) == 5
    assert [event.frame_id for event in events] == [5, 15, 25, 35, 45]


def test_x_counter_rejects_lateral_jump_reverse_and_oversized_component():
    from stream_counter import Detection
    def det(x, y=116, area=121):
        return Detection(x-5, x+5, y-5, y+5, area, float(x), float(y))
    for sequence in ([det(128), det(164, 140)], [det(190), det(150), det(128)]):
        counter = MaskCounter(count_axis="x", max_step=60, max_lateral_step=10)
        for frame, detection in enumerate(sequence):
            counter.update_detections(frame, [detection])
        assert counter.total_count == 0
    counter = MaskCounter(count_axis="x", max_area=120)
    assert counter.update_detections(0, [det(128)]).detections == ()


def test_recorded_detections_replay_matches_live_x_counting():
    live = MaskCounter(count_axis="x", max_step=60, max_lateral_step=10)
    replay = MaskCounter(count_axis="x", max_step=60, max_lateral_step=10)
    for frame, x in enumerate([64, 96, 128, 160, 192, 224]):
        mask = np.zeros((240, 320), dtype=np.uint8)
        mask[111:122, x-5:x+6] = 255
        actual = live.update(frame, mask)
        recorded = replay.update_detections(frame, actual.detections)
        assert recorded.events == actual.events
        assert recorded.total_count == actual.total_count
    assert replay.total_count == 1


def test_x_configuration_is_explicit_and_vertical_preview_matches():
    from mjpeg_preview import render_preview_frame
    args = runner._parser().parse_args(["--count-axis", "x", "--line-x", "160",
                                       "--max-step", "60", "--max-lateral-step", "10",
                                       "--max-area", "600"])
    config = runner.counting_configuration(args)
    assert config["count_axis"] == "x" and config["max_area"] == 600
    frame = render_preview_frame(np.zeros((240, 320), dtype=np.uint8),
                                 total_count=0, line_y=120, count_axis="x", line_x=160)
    assert tuple(frame[100, 160]) == (0, 0, 255)
    assert tuple(frame[120, 100]) == (0, 0, 0)


def test_rightward_track_counts_once_when_component_splits_after_crossing():
    from stream_counter import Detection
    def det(x, y):
        return Detection(x-5, x+5, y-3, y+3, 60, float(x), float(y))
    counter = MaskCounter(count_axis="x", max_step=60, max_lateral_step=10)
    for frame, detections in enumerate(([det(96,116)], [det(128,116)],
                                        [det(164,116)], [det(196,112),det(196,120)],
                                        [det(228,112),det(228,120)])):
        counter.update_detections(frame, detections)
    assert counter.total_count == 1


def test_attempt4_permit_binds_counting_config_and_counter_hash(tmp_path):
    args = runner._parser().parse_args(["--count-axis", "x", "--max-step", "60",
                                       "--max-lateral-step", "10"])
    identity = runner.stageb2_attempt_permit_identity(
        ready_identity={"boot_id":"same-boot"}, ready_guard_sha256="a"*64,
        attempt_id="attempt-4", operator_prearm_delay_s=15, runner_sha256="b"*64,
        gate_launcher_sha256="d"*64,
        counting_config=runner.counting_configuration(args), counter_sha256="c"*64)
    path = tmp_path/"permit.json"
    path.write_text(json.dumps({**identity, "permit_status":"AUTHORIZED", "consumed":False}))
    path.chmod(0o600)
    tmp_path.chmod(0o700)
    for changed in ({**identity,"counter_sha256":"e"*64},
                    {**identity,"gate_launcher_sha256":"f"*64},
                    {**identity,"counting_config":{**identity["counting_config"],"max_step":20}}):
        with pytest.raises(RuntimeError, match="does not match"):
            runner.validate_stageb2_attempt_permit(path, changed, expected_uid=path.stat().st_uid)


def test_wo15_dependency_path_prefers_isolated_bundle(tmp_path):
    script_dir = tmp_path / "wo0016"
    bundled = script_dir / "wo0015"
    bundled.mkdir(parents=True)
    (bundled / "board_once.py").write_text("# bundled", encoding="utf-8")
    (tmp_path / "wo0015").mkdir()

    assert resolve_wo15_dependency_dir(script_dir) == bundled


def test_wo15_dependency_path_falls_back_to_repository_sibling(tmp_path):
    script_dir = tmp_path / "scripts" / "wo0016"
    sibling = tmp_path / "scripts" / "wo0015"
    script_dir.mkdir(parents=True)
    sibling.mkdir()

    assert resolve_wo15_dependency_dir(script_dir) == sibling


def test_offline_simulation_counts_two_crossings_and_marks_no_board_access():
    payload = run_mock_simulation(frame_count=8, line_y=120, min_area=12)

    assert payload["status"] == "MOCK_PASS"
    assert payload["frames_processed"] == 8
    assert payload["total_count"] == 2
    assert payload["ground_truth_count"] == 2
    assert payload["board_accessed"] is False
    assert payload["pynq_imported"] is False
    assert payload["accuracy_percent"] == 100.0
    assert payload["preview_jpeg_sha256"]


def test_synthetic_scene_contains_two_objects_and_an_area_filtered_noise_blob():
    masks = build_synthetic_masks(frame_count=8)
    counter = MaskCounter(line_y=120, min_area=12, max_step=24)

    results = [counter.update(index, mask) for index, mask in enumerate(masks)]

    assert results[-1].total_count == 2
    assert all(mask.dtype == np.uint8 and mask.shape == (240, 320) for mask in masks)


def test_one_shot_guard_is_exclusive_and_records_exact_identity(tmp_path):
    path = tmp_path / "stream-counter-consumed.json"
    identity = {"work_order": "WO-0016", "boot_id": "test-boot",
                "manifest_sha256": "a" * 64}

    receipt = claim_once_guard(path, identity)

    assert receipt == identity | {"consumed": True}
    with pytest.raises(FileExistsError):
        claim_once_guard(path, identity)


def test_stageb2_ready_guard_is_validated_without_consuming_or_rewriting(tmp_path):
    path = tmp_path / "stageb2-ready.json"
    identity = {
        "schema": 1,
        "work_order": "WO-0016",
        "stage": "wo0016-stageB2-nuts",
        "boot_id": "test-boot",
        "manifest_sha256": "a" * 64,
        "bit_sha256": "b" * 64,
        "hwh_sha256": "c" * 64,
        "max_frames": 165,
        "expected_count": 5,
        "material_label": "M6_nut_no_washer",
        "stabilization_delay_s": 3.0,
    }
    original = json.dumps(identity | {"guard_status": "READY", "consumed": False},
                          sort_keys=True).encode() + b"\n"
    path.write_bytes(original)
    path.chmod(0o600)
    path.parent.chmod(0o700)
    owner_uid = path.stat().st_uid

    receipt = runner.validate_non_consuming_guard(
        path, identity, expected_uid=owner_uid
    )

    assert receipt["guard"]["consumed"] is False
    assert receipt["guard"]["guard_status"] == "READY"
    assert receipt["sha256"] == hashlib.sha256(original).hexdigest()
    assert path.read_bytes() == original


@pytest.mark.parametrize("mutation", [
    {"boot_id": "other-boot"},
    {"manifest_sha256": "d" * 64},
    {"consumed": True},
    {"guard_status": "CONSUMED"},
])
def test_stageb2_ready_guard_rejects_nonmatching_or_consumed_records(tmp_path, mutation):
    path = tmp_path / "stageb2-ready.json"
    identity = {
        "schema": 1,
        "work_order": "WO-0016",
        "stage": "wo0016-stageB2-nuts",
        "boot_id": "test-boot",
        "manifest_sha256": "a" * 64,
        "bit_sha256": "b" * 64,
        "hwh_sha256": "c" * 64,
        "max_frames": 165,
        "expected_count": 5,
        "material_label": "M6_nut_no_washer",
        "stabilization_delay_s": 3.0,
    }
    record = identity | {"guard_status": "READY", "consumed": False} | mutation
    path.write_text(json.dumps(record), encoding="utf-8")
    path.chmod(0o600)
    path.parent.chmod(0o700)

    with pytest.raises(RuntimeError, match="stage B-2 READY guard"):
        runner.validate_non_consuming_guard(
            path, identity, expected_uid=path.stat().st_uid
        )


def test_stageb2_no_consume_cli_requires_an_explicit_flag_and_settle_time():
    args = runner._parser().parse_args([
        "--mode", "board", "--no-consume-guard", "--stabilization-delay-s", "3",
        "--attempt-id", "attempt-2", "--operator-prearm-delay-s", "15",
        "--max-frames", "165", "--expected-count", "5",
        "--material-label", "M6_nut_no_washer",
    ])

    assert args.no_consume_guard is True
    assert args.stabilization_delay_s == 3.0
    assert args.max_frames == 165
    assert args.ground_truth_count == 5
    assert args.attempt_id == "attempt-2"
    assert args.operator_prearm_delay_s == 15.0


def test_stageb2_attempt_permit_is_private_boot_bound_and_read_only(tmp_path):
    ready_identity = runner.stageb2_guard_identity(
        boot_id="boot-1", manifest_sha256="a" * 64, bit_sha256="b" * 64,
        hwh_sha256="c" * 64, max_frames=165, expected_count=5,
        material_label="M6_nut_no_washer", stabilization_delay_s=3.0,
    )
    identity = runner.stageb2_attempt_permit_identity(
        ready_identity=ready_identity,
        ready_guard_sha256="d" * 64,
        attempt_id="attempt-2",
        operator_prearm_delay_s=15.0,
        runner_sha256="e" * 64,
        gate_launcher_sha256="f" * 64,
    )
    original = json.dumps(
        identity | {"permit_status": "AUTHORIZED", "consumed": False},
        sort_keys=True,
    ).encode() + b"\n"
    path = tmp_path / "attempt-2-permit.json"
    path.write_bytes(original)
    path.chmod(0o600)
    path.parent.chmod(0o700)

    receipt = runner.validate_stageb2_attempt_permit(
        path, identity, expected_uid=path.stat().st_uid
    )

    assert receipt["permit"]["attempt_id"] == "attempt-2"
    assert receipt["permit"]["consumed"] is False
    assert receipt["permit"]["runner_sha256"] == "e" * 64
    assert receipt["permit"]["gate_launcher_sha256"] == "f" * 64
    assert receipt["sha256"] == hashlib.sha256(original).hexdigest()
    assert path.read_bytes() == original


def test_stageb2_attempt_permit_rejects_wrong_attempt_and_unsafe_ids():
    ready_identity = runner.stageb2_guard_identity(
        boot_id="boot-1", manifest_sha256="a" * 64, bit_sha256="b" * 64,
        hwh_sha256="c" * 64, max_frames=165, expected_count=5,
        material_label="M6_nut_no_washer", stabilization_delay_s=3.0,
    )
    with pytest.raises(ValueError, match="attempt id"):
        runner.stageb2_attempt_permit_identity(
            ready_identity=ready_identity, ready_guard_sha256="d" * 64,
            attempt_id="../attempt-2", operator_prearm_delay_s=15.0,
            runner_sha256="e" * 64,
            gate_launcher_sha256="f" * 64,
        )


def test_board_mode_requires_operator_gate_launcher_before_preflight(capsys):
    result = runner.main(["--mode", "board"], operator_gate_sha256=None)
    payload = json.loads(capsys.readouterr().err)

    assert result == 2
    assert payload["status"] == "BOARD_STREAM_REJECTED"
    assert "operator gate launcher" in payload["error"].lower()
    assert payload["board_accessed"] is False
    assert payload["pynq_imported"] is False


def test_public_launcher_hash_alone_cannot_bypass_operator_gate(capsys):
    launcher = Path(runner.SCRIPT_DIR) / "operator_gate_launcher.py"
    launcher_sha256 = hashlib.sha256(launcher.read_bytes()).hexdigest()

    result = runner.main(["--mode", "board"], operator_gate_sha256=launcher_sha256)
    payload = json.loads(capsys.readouterr().err)

    assert result == 2
    assert payload["status"] == "BOARD_STREAM_REJECTED"
    assert "live operator control server" in payload["error"].lower()
    assert payload["board_accessed"] is False
    assert payload["pynq_imported"] is False


def test_operator_gate_object_must_be_registered_to_live_control_server(monkeypatch, capsys):
    from operator_gate_launcher import OperatorGate, make_server

    monkeypatch.setattr("operator_gate_launcher.secrets.token_urlsafe", lambda _n: "test-token")
    gate = OperatorGate()
    launcher = Path(runner.SCRIPT_DIR) / "operator_gate_launcher.py"
    launcher_sha256 = hashlib.sha256(launcher.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="live operator control server"):
        runner.validate_operator_gate_launcher_sha256(launcher_sha256, gate)

    server = make_server(gate, port=0)
    try:
        assert runner.validate_operator_gate_launcher_sha256(launcher_sha256, gate) == launcher_sha256
    finally:
        server.server_close()
    with pytest.raises(ValueError, match="live operator control server"):
        runner.validate_operator_gate_launcher_sha256(launcher_sha256, gate)
    capsys.readouterr()


def test_public_camera_stream_rejects_missing_operator_gate_before_opening_device():
    opened = []

    class FakeCv2:
        CAP_V4L2 = 1

        @staticmethod
        def VideoCapture(device, backend):
            opened.append((device, backend))
            raise AssertionError("camera must not be opened without a live gate session")

    async def consume():
        return [frame async for frame in runner.camera_gray_frames(
            "/dev/video-test", max_frames=1, cv2_module=FakeCv2,
            operator_gate=None,
        )]

    with pytest.raises(ValueError, match="live operator control server"):
        asyncio.run(consume())
    assert opened == []


def test_camera_stream_waits_for_operator_gate_before_first_sample(monkeypatch):
    monkeypatch.setattr(runner, "require_standard_camera_controls", lambda device: None)
    from operator_gate_launcher import OperatorGate, make_server

    monkeypatch.setattr("operator_gate_launcher.secrets.token_urlsafe", lambda _n: "test-token")
    gate = OperatorGate(handoff_timeout_s=2.0, countdown_s=0.0)
    server = make_server(gate, port=0)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    milestones = []
    frames_result = []
    errors = []
    read_count = [0]

    class Gate:
        def wait_for_operator(self):
            waited.set()

    class Capture:
        def __init__(self):
            self.properties = {}

        def isOpened(self):
            return True

        def set(self, prop, value):
            self.properties[prop] = value
            return True

        def get(self, prop):
            if prop == FakeCv2.CAP_PROP_FOURCC:
                return FakeCv2.VideoWriter_fourcc(*"MJPG")
            if prop == FakeCv2.CAP_PROP_FRAME_WIDTH:
                return 640
            if prop == FakeCv2.CAP_PROP_FRAME_HEIGHT:
                return 400
            if prop == FakeCv2.CAP_PROP_FPS:
                return 60
            return self.properties[prop]

        def read(self):
            read_count[0] += 1
            return True, np.zeros((400, 640), dtype=np.uint8)

        def release(self):
            return None

    class FakeCv2:
        CAP_V4L2 = 1
        CAP_PROP_FOURCC = 2
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5
        CAP_PROP_BUFFERSIZE = 6
        INTER_NEAREST = 0

        @staticmethod
        def VideoCapture(_device, _backend):
            return Capture()

        @staticmethod
        def VideoWriter_fourcc(*chars):
            return sum(ord(char) << (8 * index) for index, char in enumerate(chars))

        @staticmethod
        def resize(_image, size, interpolation):
            assert size == (320, 240)
            assert interpolation == FakeCv2.INTER_NEAREST
            return np.zeros((240, 320), dtype=np.uint8)

    def on_milestone(name, _at):
        if name == "FRAME_0_CAPTURE_START_FEED_NOW":
            assert gate.snapshot()["phase"] == "COUNTDOWN", (
                "sampling began before operator authentication"
            )
        milestones.append(name)

    async def consume():
        return [
            frame async for frame in runner.camera_gray_frames(
                "/dev/video-test", max_frames=1, cv2_module=FakeCv2,
                operator_gate=gate, milestone_callback=on_milestone,
                stabilization_delay_s=0.0,
            )
        ]

    def consume_in_thread():
        try:
            frames_result.extend(asyncio.run(consume()))
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=consume_in_thread, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 1.5
        while gate.snapshot()["phase"] != "READY" and time.monotonic() < deadline:
            time.sleep(0.005)
        assert gate.snapshot()["phase"] == "READY"
        assert read_count[0] == 2, "only two warm-up reads may precede authorization"
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/start",
            data=b'{"operator_passcode":"test-token"}', method="POST",
            headers={"Content-Type": "application/json"})
        assert urllib.request.urlopen(request).status == 200
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert errors == []
        assert len(frames_result) == 1
        assert read_count[0] == 3
        assert milestones.index("CAMERA_OPEN_READY_FOR_OPERATOR") < milestones.index(
            "CAMERA_OPEN_AND_STABILIZING_START"
        ) < milestones.index("FRAME_0_CAPTURE_START_FEED_NOW")
    finally:
        if worker.is_alive():
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/start",
                    data=b'{"operator_passcode":"test-token"}', method="POST",
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(request, timeout=0.5).close()
            except BaseException:
                pass
            worker.join(timeout=2)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_stageb2_no_consume_cli_rejects_missing_attempt_id_before_board_access(capsys):
    result = runner.main(["--mode", "board", "--no-consume-guard"])
    payload = json.loads(capsys.readouterr().err)

    assert result == 2
    assert payload["status"] == "BOARD_STREAM_REJECTED"
    assert "unique --attempt-id" in payload["error"]
    assert payload["board_accessed"] is False
    assert payload["pynq_imported"] is False


def test_emit_milestone_writes_jsonl_and_flushes_terminal(capsys, tmp_path):
    path = tmp_path / "milestones.jsonl"

    runner._emit_milestone(
        "CAMERA_OPEN_AND_STABILIZING_START", 123.5, path,
        boot_id="boot-1",
    )

    assert "[MILESTONE] CAMERA_OPEN_AND_STABILIZING_START" in capsys.readouterr().out
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record == {
        "boot_id": "boot-1",
        "milestone": "CAMERA_OPEN_AND_STABILIZING_START",
        "monotonic": 123.5,
    }


def test_countdown_uses_monotonic_time_and_reports_each_second(monkeypatch, capsys):
    class FakeClock:
        now = 10.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = FakeClock()
    monkeypatch.setattr(runner.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(runner.time, "sleep", clock.sleep)

    runner._countdown_wait(3.0, label="STABILIZATION")

    assert capsys.readouterr().out.splitlines() == [
        "[STABILIZATION] 3 seconds remaining",
        "[STABILIZATION] 2 seconds remaining",
        "[STABILIZATION] 1 seconds remaining",
    ]
    assert clock.now == 13.0


def test_stageb2_guard_identity_fails_closed_when_profile_values_differ():
    with pytest.raises(ValueError, match="restricted to Stage B-2"):
        runner.stageb2_guard_identity(
            boot_id="boot",
            manifest_sha256="a" * 64,
            bit_sha256="b" * 64,
            hwh_sha256="c" * 64,
            max_frames=150,
            expected_count=5,
            material_label="M6_nut_no_washer",
            stabilization_delay_s=3.0,
        )


def test_each_completed_frame_requires_exact_mm2s_and_s2mm_dmasr():
    import board_preflight

    class Readback:
        def __init__(self, statuses):
            self.statuses = statuses

        def read32(self, base, offset):
            assert base == board_preflight.DMA_BASE
            return self.statuses[offset]

    records = []
    runner.verify_frame_dmasr(Readback({0x04: 0x1002, 0x34: 0x1002}), 7, records)
    assert records == [{"frame_id": 7, "mm2s": "0x00001002", "s2mm": "0x00001002"}]

    with pytest.raises(RuntimeError, match="frame 8 DMA status mismatch"):
        runner.verify_frame_dmasr(Readback({0x04: 0x1002, 0x34: 0x1000}), 8, records)


def test_reverse_direction_mock_sequence_matches_its_separate_ground_truth():
    payload = run_mock_simulation(frame_count=8, line_y=120, direction=-1, min_area=12)

    assert payload["status"] == "MOCK_PASS"
    assert payload["total_count"] == payload["ground_truth_count"] == 2


def test_expected_count_and_material_label_are_available_to_stage_b_cli():
    args = runner._parser().parse_args([
        "--mode", "board", "--expected-count", "6",
        "--material-label", "M6_nut_and_washer", "--measure-preview-client",
    ])

    assert args.ground_truth_count == 6
    assert args.material_label == "M6_nut_and_washer"
    assert args.measure_preview_client is True
    assert runner._count_accuracy_fraction(5, 6) == pytest.approx(5 / 6)
    assert runner._count_accuracy_fraction(3, 0) == -2.0
    legacy_args = runner._parser().parse_args(["--ground-truth-count", "4"])
    assert legacy_args.ground_truth_count == 4


def test_mock_result_serializes_expected_count_material_and_fractional_accuracy():
    payload = run_mock_simulation(
        frame_count=8, expected_count=3, material_label="M6_nut_and_washer"
    )

    assert payload["total_count"] == 2
    assert payload["expected_count"] == payload["ground_truth_count"] == 3
    assert payload["material_label"] == "M6_nut_and_washer"
    assert payload["accuracy"] == pytest.approx(1.0 - 1 / 3)


def test_roi_subtimings_summary_keeps_named_stages_and_sample_count():
    counter = MaskCounter()
    mask = np.zeros((240, 320), dtype=np.uint8)
    results = [counter.update(frame_id, mask) for frame_id in range(2)]

    summary = runner.summarize_roi_subtimings_ms(results)

    assert set(summary) == {
        "validate_ms", "projection_ms", "ccl_ms", "track_ms",
        "validate_timing_samples", "projection_timing_samples",
        "ccl_timing_samples", "track_timing_samples",
        "ccl_invocations", "ccl_avg_ms_when_invoked", "roi_foreground_pixels_max",
    }
    assert all(summary[f"{name}_timing_samples"] == 2 for name in
               ("validate", "projection", "ccl", "track"))
    assert all(summary[name] >= 0 for name in
               ("validate_ms", "projection_ms", "ccl_ms", "track_ms"))
    assert summary["ccl_invocations"] == 0
    assert summary["ccl_avg_ms_when_invoked"] is None
    assert summary["roi_foreground_pixels_max"] == 0


def test_board_mode_without_one_shot_flags_rejects_before_pynq_import(capsys, tmp_path):
    from run_stream_counter import main
    import board_once

    result = main(["--mode", "board", "--output-json", str(tmp_path / "result.json")])
    output = capsys.readouterr()
    payload = __import__("json").loads(output.err)

    assert result == 2
    assert payload["status"] == "BOARD_STREAM_REJECTED"
    assert payload["board_accessed"] is False
    assert payload["pynq_imported"] is False
    assert board_once._PYNQ_IMPORTED is False


def test_stage1_afi1_gate_requires_the_exact_readback_values():
    snapshot = {
        "afi1_status": "READ",
        "registers": {
            "AFI1_RDCHAN_CTRL": {"value": 0x00000001},
            "AFI1_WRCHAN_CTRL": {"value": 0x00000F01},
        },
    }

    assert verify_afi1_readback(snapshot) == {
        "AFI1_RDCHAN_CTRL": "0x00000001",
        "AFI1_WRCHAN_CTRL": "0x00000f01",
    }
    snapshot["registers"]["AFI1_WRCHAN_CTRL"]["value"] = 0x00000001
    with pytest.raises(RuntimeError, match="expected exact Stage 1 value"):
        verify_afi1_readback(snapshot)


def test_dma_buffers_release_requires_both_channels_idle_and_error_free():
    import board_once
    import board_preflight

    class StatusAPI:
        def __init__(self, statuses):
            self.statuses = statuses

        def read32(self, base, offset):
            assert base == board_preflight.DMA_BASE
            return self.statuses[offset]

    dma = object()
    idle = board_once.DMA_IDLE
    assert _dma_buffers_safe_to_release(
        StatusAPI({0x34: idle, 0x04: idle}), dma, None
    )
    assert not _dma_buffers_safe_to_release(
        StatusAPI({0x34: idle, 0x04: 0}), dma, None
    )
    assert not _dma_buffers_safe_to_release(
        StatusAPI({0x34: idle | board_once.DMA_ERROR_MASK, 0x04: idle}), dma, None
    )


def test_dma_buffers_stay_held_when_stream_may_still_be_active():
    from types import SimpleNamespace

    assert not _dma_buffers_safe_to_release(
        object(), object(), SimpleNamespace(dma_may_be_active=True)
    )


def test_camera_read_timeout_returns_without_waiting_for_a_stuck_v4l2_thread():
    released = threading.Event()

    class StuckCapture:
        def __init__(self):
            self.properties = {}

        def isOpened(self):
            return True

        def set(self, prop, value):
            self.properties[prop] = value
            return True

        def get(self, prop):
            if prop == FakeCv2.CAP_PROP_FOURCC:
                return FakeCv2.VideoWriter_fourcc(*"MJPG")
            return self.properties[prop]

        def read(self):
            released.wait()
            return False, None

        def release(self):
            released.set()

    class FakeCv2:
        CAP_V4L2 = 1
        CAP_PROP_FOURCC = 2
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5

        @staticmethod
        def VideoCapture(device, backend):
            assert device == "/dev/video-test"
            assert backend == FakeCv2.CAP_V4L2
            return StuckCapture()

        @staticmethod
        def VideoWriter_fourcc(*chars):
            return sum(ord(char) << (8 * index) for index, char in enumerate(chars))

    async def consume_one():
        async for _ in _camera_gray_frames_impl("/dev/video-test", max_frames=1,
                                          cv2_module=FakeCv2, read_timeout_s=0.02):
            pytest.fail("the stuck camera must not produce a frame")

    started = time.perf_counter()
    with pytest.raises(RuntimeError, match="warm-up read timed out"):
        asyncio.run(consume_one())
    assert time.perf_counter() - started < 0.2
    assert released.wait(0.2)


def test_camera_timeout_is_bounded_when_release_cannot_unblock_native_read():
    unblock_read = threading.Event()
    read_started = threading.Event()
    release_calls = []

    class StuckCapture:
        def __init__(self):
            self.properties = {}

        def isOpened(self):
            return True

        def set(self, prop, value):
            self.properties[prop] = value
            return True

        def get(self, prop):
            if prop == FakeCv2.CAP_PROP_FOURCC:
                return FakeCv2.VideoWriter_fourcc(*"MJPG")
            return self.properties[prop]

        def read(self):
            read_started.set()
            unblock_read.wait()
            return False, None

        def release(self):
            release_calls.append(True)

    class FakeCv2:
        CAP_V4L2 = 1
        CAP_PROP_FOURCC = 2
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5
        CAP_PROP_BUFFERSIZE = 6

        @staticmethod
        def VideoCapture(device, backend):
            return StuckCapture()

        @staticmethod
        def VideoWriter_fourcc(*chars):
            return sum(ord(char) << (8 * index) for index, char in enumerate(chars))

    async def consume_one():
        async for _ in _camera_gray_frames_impl("/dev/video-test", max_frames=1,
                                          cv2_module=FakeCv2, read_timeout_s=0.02):
            pytest.fail("the blocked camera must not produce a frame")

    started = time.perf_counter()
    with pytest.raises(RuntimeError, match="warm-up read timed out") as exc_info:
        asyncio.run(consume_one())
    elapsed = time.perf_counter() - started

    assert read_started.is_set()
    assert release_calls == [True]
    assert elapsed < 0.2
    assert any("worker did not stop" in note for note in getattr(exc_info.value, "__notes__", []))
    unblock_read.set()
    for thread in threading.enumerate():
        if thread.name == "wo0016-camera-prefetch":
            thread.join(timeout=0.2)
            assert not thread.is_alive()


def test_camera_mode_fails_closed_when_v4l2_negotiates_another_format():
    released = threading.Event()

    class WrongModeCapture:
        def isOpened(self):
            return True

        def set(self, prop, value):
            return True

        def get(self, prop):
            if prop == FakeCv2.CAP_PROP_FOURCC:
                return FakeCv2.VideoWriter_fourcc(*"YUYV")
            return 640 if prop == FakeCv2.CAP_PROP_FRAME_WIDTH else 400

        def read(self):
            pytest.fail("capture.read must not run after a format mismatch")

        def release(self):
            released.set()

    class FakeCv2:
        CAP_V4L2 = 1
        CAP_PROP_FOURCC = 2
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5

        @staticmethod
        def VideoCapture(device, backend):
            return WrongModeCapture()

        @staticmethod
        def VideoWriter_fourcc(*chars):
            return sum(ord(char) << (8 * index) for index, char in enumerate(chars))

    async def consume_one():
        async for _ in _camera_gray_frames_impl("/dev/video-test", max_frames=1,
                                          cv2_module=FakeCv2, read_timeout_s=0.1):
            pytest.fail("the negotiated format mismatch must stop capture")

    with pytest.raises(RuntimeError, match="negotiated 'YUYV' 640x400"):
        asyncio.run(consume_one())
    assert released.wait(0.2)


def test_camera_handle_is_opened_once_and_capture_timing_covers_each_frame():
    opened = []
    released = []
    set_calls = []
    resize_calls = []
    steady_start_time = []
    capture_metadata = {}
    milestones = []

    class Capture:
        def __init__(self):
            self.properties = {}
            self.reads = 0

        def isOpened(self):
            return True

        def set(self, prop, value):
            set_calls.append((prop, value))
            self.properties[prop] = value
            return True

        def get(self, prop):
            if prop == FakeCv2.CAP_PROP_FOURCC:
                return FakeCv2.VideoWriter_fourcc(*"MJPG")
            return self.properties[prop]

        def read(self):
            self.reads += 1
            if self.reads <= 2:
                assert not steady_start_time
            else:
                assert len(steady_start_time) == 1
                assert milestones[-1][0] == "FRAME_0_CAPTURE_START_FEED_NOW"
            frame = np.empty((400, 640, 3), dtype=np.uint8)
            frame[:, :, 0] = 77
            frame[:, :, 1] = 12
            frame[:, :, 2] = 200
            return True, frame

        def release(self):
            released.append(True)

    class FakeCv2:
        CAP_V4L2 = 1
        CAP_PROP_FOURCC = 2
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5
        INTER_NEAREST = 6
        CAP_PROP_BUFFERSIZE = 7

        @staticmethod
        def VideoCapture(device, backend):
            opened.append((device, backend))
            return Capture()

        @staticmethod
        def VideoWriter_fourcc(*chars):
            return sum(ord(char) << (8 * index) for index, char in enumerate(chars))

        @staticmethod
        def resize(gray, dimensions, interpolation):
            resize_calls.append((gray.ndim, dimensions, interpolation, int(gray[0, 0])))
            return np.full((dimensions[1], dimensions[0]), gray[0, 0], dtype=np.uint8)

    capture_timings_ms = []

    async def consume():
        return [frame async for frame in _camera_gray_frames_impl(
            "/dev/video-test", max_frames=2, cv2_module=FakeCv2,
            capture_timings_ms=capture_timings_ms,
                milestone_callback=lambda name, at: milestones.append((name, at)),
            steady_start_time=steady_start_time,
            capture_metadata=capture_metadata)]

    frames = asyncio.run(consume())

    assert opened == [("/dev/video-test", FakeCv2.CAP_V4L2)]
    assert released == [True]
    assert len(frames) == len(capture_timings_ms) == 2
    assert all(frame.shape == (240, 320) and frame.dtype == np.uint8 for frame in frames)
    assert all(np.all(frame == 77) for frame in frames)
    assert all(sample >= 0 for sample in capture_timings_ms)
    assert set_calls == [
        (FakeCv2.CAP_PROP_FOURCC, FakeCv2.VideoWriter_fourcc(*"MJPG")),
        (FakeCv2.CAP_PROP_FRAME_WIDTH, 640),
        (FakeCv2.CAP_PROP_FRAME_HEIGHT, 400),
        (FakeCv2.CAP_PROP_FPS, 60),
        (FakeCv2.CAP_PROP_BUFFERSIZE, 1),
    ]
    assert resize_calls == [(2, (320, 240), FakeCv2.INTER_NEAREST, 77)] * 2
    assert len(steady_start_time) == 1
    assert capture_metadata == {
        "camera_fps_requested": 60,
        "camera_fps_actual": 60.0,
        "camera_warmup_frames": 2,
        "camera_buffer_size": 1,
        "camera_prefetch_depth": 1,
        "camera_width": 640,
        "camera_height": 400,
        "camera_output_width": 320,
        "camera_output_height": 240,
        "camera_open_monotonic": capture_metadata["camera_open_monotonic"],
    }
    assert [name for name, _ in milestones] == [
        "CAMERA_OPENED",
        "CAMERA_OPEN_READY_FOR_OPERATOR",
        "CAMERA_OPEN_AND_STABILIZING_START",
        "FRAME_0_CAPTURE_START_FEED_NOW",
    ]
    assert milestones[0][1] <= milestones[-1][1]


def test_camera_prefetches_next_ordered_frame_and_joins_before_release():
    next_read_started = threading.Event()
    allow_next_read_to_finish = threading.Event()
    read_lock = threading.Lock()
    active_reads = 0
    max_active_reads = 0
    release_active_reads = []

    class Capture:
        def __init__(self):
            self.properties = {}
            self.reads = 0

        def isOpened(self):
            return True

        def set(self, prop, value):
            self.properties[prop] = value
            return True

        def get(self, prop):
            if prop == FakeCv2.CAP_PROP_FOURCC:
                return FakeCv2.VideoWriter_fourcc(*"MJPG")
            return self.properties[prop]

        def read(self):
            nonlocal active_reads, max_active_reads
            with read_lock:
                self.reads += 1
                read_number = self.reads
                active_reads += 1
                max_active_reads = max(max_active_reads, active_reads)
            try:
                if read_number == 4:
                    next_read_started.set()
                    if not allow_next_read_to_finish.wait(timeout=1.0):
                        return False, None
                frame = np.full((400, 640, 3), read_number, dtype=np.uint8)
                return True, frame
            finally:
                with read_lock:
                    active_reads -= 1

        def release(self):
            with read_lock:
                release_active_reads.append(active_reads)

    class FakeCv2:
        CAP_V4L2 = 1
        CAP_PROP_FOURCC = 2
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5
        INTER_NEAREST = 6
        CAP_PROP_BUFFERSIZE = 7

        @staticmethod
        def VideoCapture(device, backend):
            return Capture()

        @staticmethod
        def VideoWriter_fourcc(*chars):
            return sum(ord(char) << (8 * index) for index, char in enumerate(chars))

        @staticmethod
        def resize(gray, dimensions, interpolation):
            return np.full((dimensions[1], dimensions[0]), gray[0, 0], dtype=np.uint8)

    async def consume_two():
        frames = _camera_gray_frames_impl("/dev/video-test", max_frames=2,
                                    cv2_module=FakeCv2, read_timeout_s=0.5)
        first = await anext(frames)
        # Simulate work on frame 0 while the one-slot camera worker reads frame 1.
        assert await asyncio.to_thread(next_read_started.wait, 0.2)
        allow_next_read_to_finish.set()
        second = await anext(frames)
        with pytest.raises(StopAsyncIteration):
            await anext(frames)
        return first, second

    first, second = asyncio.run(consume_two())

    assert first.shape == second.shape == (240, 320)
    assert (int(first[0, 0]), int(second[0, 0])) == (3, 4)
    assert max_active_reads == 1
    assert release_active_reads == [0]


def test_camera_close_joins_an_inflight_prefetch_before_releasing_device():
    next_read_started = threading.Event()
    allow_next_read_to_finish = threading.Event()
    active_reads = 0
    released_with_active_reads = []

    class Capture:
        def __init__(self):
            self.properties = {}
            self.reads = 0

        def isOpened(self):
            return True

        def set(self, prop, value):
            self.properties[prop] = value
            return True

        def get(self, prop):
            if prop == FakeCv2.CAP_PROP_FOURCC:
                return FakeCv2.VideoWriter_fourcc(*"MJPG")
            return self.properties[prop]

        def read(self):
            nonlocal active_reads
            self.reads += 1
            read_number = self.reads
            active_reads += 1
            try:
                if read_number == 4:
                    next_read_started.set()
                    assert allow_next_read_to_finish.wait(timeout=1.0)
                return True, np.full((400, 640, 3), read_number, dtype=np.uint8)
            finally:
                active_reads -= 1

        def release(self):
            released_with_active_reads.append(active_reads)

    class FakeCv2:
        CAP_V4L2 = 1
        CAP_PROP_FOURCC = 2
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5
        INTER_NEAREST = 6
        CAP_PROP_BUFFERSIZE = 7

        @staticmethod
        def VideoCapture(device, backend):
            return Capture()

        @staticmethod
        def VideoWriter_fourcc(*chars):
            return sum(ord(char) << (8 * index) for index, char in enumerate(chars))

        @staticmethod
        def resize(gray, dimensions, interpolation):
            return np.full((dimensions[1], dimensions[0]), gray[0, 0], dtype=np.uint8)

    async def close_after_first_frame():
        frames = _camera_gray_frames_impl("/dev/video-test", max_frames=2,
                                    cv2_module=FakeCv2, read_timeout_s=0.5)
        first = await anext(frames)
        assert int(first[0, 0]) == 3
        assert await asyncio.to_thread(next_read_started.wait, 0.2)
        close_task = asyncio.create_task(frames.aclose())
        await asyncio.sleep(0.02)
        assert not released_with_active_reads
        allow_next_read_to_finish.set()
        await close_task

    asyncio.run(close_after_first_frame())

    assert released_with_active_reads == [0]


def test_camera_rejects_v4l2_fps_below_50_before_starting_stream():
    class SlowCapture:
        def __init__(self):
            self.properties = {}

        def isOpened(self):
            return True

        def set(self, prop, value):
            self.properties[prop] = value
            return True

        def get(self, prop):
            if prop == FakeCv2.CAP_PROP_FOURCC:
                return FakeCv2.VideoWriter_fourcc(*"MJPG")
            if prop == FakeCv2.CAP_PROP_FPS:
                return 30.0
            return 640 if prop == FakeCv2.CAP_PROP_FRAME_WIDTH else 400

        def read(self):
            pytest.fail("a slow negotiated camera mode must stop before streaming")

        def release(self):
            pass

    class FakeCv2:
        CAP_V4L2 = 1
        CAP_PROP_FOURCC = 2
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5

        @staticmethod
        def VideoCapture(device, backend):
            return SlowCapture()

        @staticmethod
        def VideoWriter_fourcc(*chars):
            return sum(ord(char) << (8 * index) for index, char in enumerate(chars))

    async def consume_one():
        async for _ in _camera_gray_frames_impl("/dev/video-test", max_frames=1,
                                          cv2_module=FakeCv2, read_timeout_s=0.1):
            pytest.fail("the slow camera must not produce a stream frame")

    with pytest.raises(
        RuntimeError,
        match="negotiated .* fps 30.0; at least 50 fps is required",
    ):
        asyncio.run(consume_one())


def test_stage_timing_summary_reports_per_frame_ms_and_sample_counts():
    summary = summarize_stage_timings_ms({
        "capture": [1.0, 3.0], "encode": [2.0],
        "dma": [4.0, 6.0], "packer_check": [0.5, 1.5],
        "roi_count": [2.0], "mjpeg_encode": [1.0], "mjpeg_handoff": [40.0],
        "postprocess": [],
    })

    assert summary == {
        "avg_capture_ms": 2.0, "capture_timing_samples": 2,
        "avg_encode_ms": 2.0, "encode_timing_samples": 1,
        "avg_dma_ms": 5.0, "dma_timing_samples": 2,
        "avg_rx_sync_ms": None, "rx_sync_timing_samples": 0,
        "avg_rx_copy_ms": None, "rx_copy_timing_samples": 0,
        "avg_packer_check_ms": 1.0, "packer_check_timing_samples": 2,
        "avg_roi_count_ms": 2.0, "roi_count_timing_samples": 1,
        "avg_mjpeg_encode_ms": 1.0, "mjpeg_encode_timing_samples": 1,
        "avg_mjpeg_handoff_ms": 40.0, "mjpeg_handoff_timing_samples": 1,
        "avg_mjpeg_background_encode_ms": None,
        "mjpeg_background_encode_timing_samples": 0,
        "avg_postprocess_ms": None, "postprocess_timing_samples": 0,
    }


# Attempt 5 regressions: actual counting remains independent of expected_count.
def _nut_component(x, y=116, area=40):
    from stream_counter import Detection
    return Detection(int(x)-2, int(x)+2, int(y)-4, int(y)+4,
                     area, float(x), float(y))


def test_split_nut_fourteen_pixel_lobes_count_once_before_crossing():
    counter = MaskCounter(count_axis="x", max_step=60, component_cluster_dx=24,
                          component_cluster_dy=12)
    events = []
    for f, x in enumerate((90, 122, 154, 186, 218)):
        mask = np.zeros((240,320), dtype=np.uint8)
        mask[112:121,x-2:x+3] = 255
        mask[112:121,x+12:x+17] = 255
        result = counter.update(f, mask)
        assert len(result.raw_detections) == 2
        assert len(result.detections) == 1
        events.extend(result.events)
    assert counter.total_count == 1 and len(events) == 1
    assert len({m['track_id'] for m in counter.last_associations}) == 1


def test_cluster_weighted_geometry_and_no_transitive_chain():
    counter = MaskCounter(count_axis="x", component_cluster_dx=24, component_cluster_dy=12)
    result = counter.update_detections(0, [_nut_component(100,area=20),
        _nut_component(114,area=40),_nut_component(128,area=20)])
    assert len(result.detections) == 2  # 100-114-128 cannot chain into one 28px group
    merged = result.detections[0]
    assert merged.area == 60
    assert merged.centroid_x == pytest.approx((100*20+114*40)/60)
    assert (merged.x_min,merged.x_max,merged.y_min,merged.y_max) == (98,116,112,120)


def test_clustering_preserves_separate_lanes_and_distant_nuts():
    counter = MaskCounter(count_axis="x", component_cluster_dx=24, component_cluster_dy=12)
    assert len(counter.update_detections(0, [_nut_component(100),
        _nut_component(125),_nut_component(100,130)]).detections) == 3


@pytest.mark.parametrize('delay,y,expected', [(3,131,1),(4,131,2),(3,132,2)])
def test_cooldown_inclusive_frame_and_corridor_boundaries(delay,y,expected):
    counter = MaskCounter(count_axis="x", max_step=60, cooldown_frames=3,
                          cooldown_lateral=15)
    # First track finishes before a distinct upstream-born track arrives.
    counter.update_detections(0, [_nut_component(130)])
    counter.update_detections(1, [_nut_component(170)])
    for f in range(2,delay+2):
        det = [_nut_component(130,y)] if f == delay else []
        if f == delay+1:
            det = [_nut_component(170,y)]
        counter.update_detections(f,det)
    assert counter.total_count == expected


def test_confirmed_top_edge_gap_crossing_is_inferred_and_bounded():
    counter = MaskCounter(count_axis="x", max_step=60, max_lateral_step=18,
                          edge_max_missing=4)
    def edge(x):
        from stream_counter import Detection
        return Detection(x-4,x+4,96,99,20,float(x),97.0)
    counter.update_detections(96,[edge(87)])
    counter.update_detections(97,[edge(106)])
    for f in range(98,102):
        assert not counter.update_detections(f,[]).events
    result=counter.update_detections(102,[edge(250)])
    assert result.total_count == 1 and len(result.events) == 1
    assert result.events[0].inferred_gap is True
    assert counter.last_associations[0]['previous_frame'] == 97
    assert counter.last_associations[0]['kind'] == 'edge_gap_inferred'


@pytest.mark.parametrize('case', ['nonedge','expired','unconfirmed','ambiguous'])
def test_edge_gap_recovery_rejects_unproved_or_ambiguous_candidates(case):
    from stream_counter import Detection
    def edge(x):
        return Detection(x-4,x+4,96,99,20,float(x),97.0)
    counter = MaskCounter(count_axis="x", max_step=60, edge_max_missing=4)
    factory = _nut_component if case == 'nonedge' else edge
    counter.update_detections(0,[factory(87)])
    counter.update_detections(1,[] if case == 'unconfirmed' else [factory(106)])
    end = 7 if case == 'expired' else 6
    for f in range(2,end): counter.update_detections(f,[])
    candidates=[factory(250)]
    if case == 'ambiguous': candidates.append(factory(230))
    assert counter.update_detections(end,candidates).total_count == 0


def test_attempt5_options_are_validated_and_bound_in_permit():
    flags=['--component-cluster-dx','24','--component-cluster-dy','12',
           '--component-cluster-gap','16','--prediction-max-missing','2',
           '--cooldown-frames','3','--cooldown-lateral','15','--edge-max-missing','4',
           '--max-lateral-step','18','--count-axis','x']
    config=runner.counting_configuration(runner._parser().parse_args(flags))
    assert {k:config[k] for k in ('component_cluster_dx','component_cluster_dy',
        'component_cluster_gap','prediction_max_missing','cooldown_frames',
        'cooldown_lateral','edge_max_missing')} == dict(
        component_cluster_dx=24,component_cluster_dy=12,cooldown_frames=3,
        component_cluster_gap=16,prediction_max_missing=2,cooldown_lateral=15,
        edge_max_missing=4)
    for kw in ({'component_cluster_dx':-1},{'component_cluster_gap':-1},
               {'prediction_max_missing':-1},{'cooldown_frames':-1},{'edge_max_missing':-1}):
        with pytest.raises(ValueError): MaskCounter(**kw)


def test_cluster_profile_keeps_one_track_as_nut_enters_image_border():
    from stream_counter import Detection
    counter=MaskCounter(count_axis="x",max_step=60,component_cluster_dx=24,component_cluster_dy=12)
    first=counter.update_detections(42,[Detection(0,3,105,121,59,1.3389830508,112.8983050847)])
    second=counter.update_detections(43,[Detection(20,41,101,127,273,30.0256410256,113.3553113553)])
    assert len(first.detections) == len(second.detections) == 1
    assert counter.last_associations[0]['kind'] == 'matched'
    assert counter.last_associations[0]['track_id'] == 1
    assert counter.last_associations[0]['previous_frame'] == 42


def test_edge_gap_rejects_motion_inconsistent_reappearance():
    from stream_counter import Detection
    def edge(x): return Detection(x-3,x+3,96,99,20,float(x),97.)
    counter=MaskCounter(count_axis="x",max_step=60,max_lateral_step=18,edge_max_missing=4)
    counter.update_detections(0,[edge(87)])
    counter.update_detections(1,[edge(106)])
    for f in range(2,6): counter.update_detections(f,[])
    # Fits max_step*dt but is > one max_step away from prior velocity prediction.
    assert counter.update_detections(6,[edge(310)]).total_count == 0


def test_same_frame_cooldown_suppression_cannot_count_later():
    counter=MaskCounter(count_axis="x",max_step=60,cooldown_frames=3,cooldown_lateral=15)
    events=[];suppressed=[]
    for f,x in enumerate((100,132,166,198,230,262)):
        result=counter.update_detections(f,[_nut_component(x),_nut_component(x+14)])
        events.extend(result.events);suppressed.extend(result.suppressed_events)
    assert counter.total_count == len(events) == len(suppressed) == 1
    assert events[0].frame_id == suppressed[0].frame_id


def test_replay_uses_raw_ccl_to_avoid_reclustering_merged_observations():
    from dataclasses import asdict
    from replay_recorded_components import replay
    config=dict(count_axis="x",component_cluster_dx=24,component_cluster_dy=12)
    live=MaskCounter(**config)
    result=live.update_detections(0,[_nut_component(100),_nut_component(114),_nut_component(128)])
    assert len(result.detections) == 2
    repeated=replay([asdict(result)],config)
    assert repeated['frame_totals'][0]['accepted_components'] == 2

def test_optional_camera_capture_metadata_identifies_sequence_and_host_timestamps():
    class Capture:
        def __init__(self):
            self.reads = 0
        def isOpened(self):
            return True
        def set(self, *args):
            return True
        def get(self, prop):
            fourcc = sum(ord(char) << (8 * index)
                         for index, char in enumerate("MJPG"))
            return {2: fourcc, 3: 640, 4: 400, 5: 60.0}.get(prop, 1)
        def read(self):
            self.reads += 1
            return True, np.full((400, 640), 40 + self.reads, dtype=np.uint8)
        def release(self):
            return None

    class FakeCv2:
        CAP_V4L2 = 1
        CAP_PROP_FOURCC = 2
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5
        CAP_PROP_BUFFERSIZE = 6
        INTER_NEAREST = 7
        @staticmethod
        def VideoCapture(device, backend):
            return Capture()
        @staticmethod
        def VideoWriter_fourcc(*chars):
            return sum(ord(char) << (8 * index) for index, char in enumerate(chars))
        @staticmethod
        def resize(gray, dimensions, interpolation):
            return np.full((dimensions[1], dimensions[0]), gray[0, 0], dtype=np.uint8)

    from stream_counter import FrameInput
    async def collect():
        return [frame async for frame in runner._camera_gray_frames_impl(
            "/dev/video-capture-test", max_frames=2, cv2_module=FakeCv2,
            include_capture_metadata=True)]
    frames = asyncio.run(collect())
    assert all(isinstance(frame, FrameInput) for frame in frames)
    assert [frame.source_sequence for frame in frames] == [0, 1]
    assert [int(frame.gray[0, 0]) for frame in frames] == [43, 44]
    assert all(frame.capture_monotonic_ns > 0 and frame.capture_wall_time_ns > 0
               and frame.camera_read_ms >= 0 for frame in frames)


def test_capture_cli_is_disabled_by_default():
    args = runner._parser().parse_args(["--mode", "mock"])
    assert args.capture_dir is None
    assert args.capture_max_bytes == 96 * 1024 * 1024


def test_stageb2_attempt_permit_binds_optional_capture_destination_and_limits(tmp_path):
    ready_identity = runner.stageb2_guard_identity(
        boot_id="boot-1", manifest_sha256="a" * 64, bit_sha256="b" * 64,
        hwh_sha256="c" * 64, max_frames=165, expected_count=5,
        material_label="M6_nut_no_washer", stabilization_delay_s=3.0,
    )
    identity = runner.stageb2_attempt_permit_identity(
        ready_identity=ready_identity, ready_guard_sha256="d" * 64,
        attempt_id="capture-1", operator_prearm_delay_s=15.0,
        runner_sha256="e" * 64, gate_launcher_sha256="f" * 64,
        capture_config={"enabled": True,
                        "output_directory": str((tmp_path / "new-capture").resolve()),
                        "max_bytes": 64 * 1024 * 1024,
                        "queue_depth": 2, "schema_version": 1},
    )
    assert identity["capture_config"]["output_directory"].endswith("new-capture")
    assert identity["capture_config"]["max_bytes"] == 64 * 1024 * 1024
    with pytest.raises(ValueError, match="capture configuration"):
        runner.stageb2_attempt_permit_identity(
            ready_identity=ready_identity, ready_guard_sha256="d" * 64,
            attempt_id="capture-2", operator_prearm_delay_s=15.0,
            runner_sha256="e" * 64, gate_launcher_sha256="f" * 64,
            capture_config={"enabled": True, "output_directory": "relative",
                            "max_bytes": 10, "queue_depth": 2},
        )
