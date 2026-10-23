import asyncio
import hashlib
import json
import sys
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_stream_counter as runner  # noqa: E402
from run_stream_counter import (  # noqa: E402
    build_synthetic_masks,
    claim_once_guard,
    _bounded_board_operation,
    _dma_buffers_safe_to_release,
    _release_buffers,
    BufferReleaseUncertain,
    camera_gray_frames,
    resolve_wo15_dependency_dir,
    run_mock_simulation,
    summarize_stage_timings_ms,
    verify_afi1_readback,
)
from stream_counter import MaskCounter  # noqa: E402


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


def test_stageb_pynq_adapter_uses_explicit_embedded_device_without_enumeration(monkeypatch):
    class NoEnumerationMeta(type):
        @property
        def devices(cls):
            pytest.fail("Stage B must not trigger PYNQ global device enumeration")

    class FakeDevice(metaclass=NoEnumerationMeta):
        pass

    mmio_calls = []
    allocation_calls = []
    overlay_calls = []
    pynq = types.SimpleNamespace(__file__="/fake/pynq/__init__.py", Device=FakeDevice)

    def mmio(base, size, *, device):
        mmio_calls.append((base, size, device))
        return object()

    def overlay(bitfile, *, download, device):
        overlay_calls.append((bitfile, download, device))
        return object()

    pynq.MMIO = mmio
    pynq.Overlay = overlay
    device = types.SimpleNamespace(
        name="embedded_xrt0",
        allocate=lambda **kwargs: allocation_calls.append(kwargs) or "buffer",
    )
    plan = types.SimpleNamespace(bit_path="/fake/wo15.bit", bit_sha256="a" * 64)
    api = runner.PynqStageBBoardAPI(
        plan, stage=runner.WO16_STAGE, cma_ranges=(), pynq_module=pynq, device=device,
        pynq_load_elapsed_ms=4.25,
    )
    monkeypatch.setattr(runner.preflight, "audit_process_ownership",
                        lambda: {"findings": [], "denied": []})
    monkeypatch.setattr(runner.preflight, "read_fpga_manager",
                        lambda: {"state": "operating", "flags": "0"})
    monkeypatch.setattr(runner.preflight, "read_boot_id",
                        lambda: runner.preflight.AUTHORIZED_BOOT_ID)
    monkeypatch.setattr(runner.preflight, "inspect_pynq_global_state",
                        lambda *args, **kwargs: {"status": "PASS"})
    monkeypatch.setattr(runner.preflight, "read_kernel_log", lambda: [])

    audit = api.audit_environment(require_empty_shutdown=False, require_loaded_bit=True)
    api.open_mmio()
    assert api.allocate(17) == "buffer"
    api.create_overlay("/fake/wo15.bit")

    assert audit["pynq_context"] == {
        "backend": "embedded_xrt0", "device_enumeration": "SKIPPED",
        "load_elapsed_ms": 4.25,
    }
    assert len(mmio_calls) == 2
    assert all(call[2] is device for call in mmio_calls)
    assert allocation_calls == [{"shape": (17,), "dtype": "u1"}]
    assert overlay_calls == [("/fake/wo15.bit", False, device)]


def test_tier2_physical_address_gate_requires_the_authorized_pair():
    assert runner.validate_tier2_physical_addresses(0x15880000, 0x15860000) == {
        "mm2s": "0x15880000", "s2mm": "0x15860000",
    }
    with pytest.raises(RuntimeError, match="Tier 2 physical DMA address gate failed"):
        runner.validate_tier2_physical_addresses(0x15890000, 0x15860000)


def test_board_run_guards_are_profile_scoped_and_boot_namespaced():
    smoke = runner.guard_path_for_run(3, "smoke3")
    batch_a = runner.guard_path_for_run(30, "stageb-batch")
    batch_b = runner.guard_path_for_run(1800, "stageb-batch")

    assert smoke.name == "smoke3-consumed.json"
    assert f"boot-{runner.preflight.AUTHORIZED_BOOT_ID}" in str(smoke)
    assert batch_a.name == "stageb-batch-consumed.json"
    assert batch_b == batch_a
    assert batch_a.parent == smoke.parent
    with pytest.raises(ValueError, match="exactly three frames"):
        runner.guard_path_for_run(30, "smoke3")
    with pytest.raises(ValueError, match="unknown board run profile"):
        runner.guard_path_for_run(3, "arbitrary")


def test_board_run_profiles_bound_frames_and_runtime():
    assert runner.validate_board_run_limits("smoke3", 3, 30.0).name == "smoke3-consumed.json"
    assert runner.validate_board_run_limits("stageb-batch", 1800, 30.0).name == "stageb-batch-consumed.json"
    for profile, frames, runtime in (
        ("smoke3", 4, 30.0),
        ("stageb-batch", 1801, 30.0),
        ("stageb-batch", 30, 30.1),
    ):
        with pytest.raises(ValueError):
            runner.validate_board_run_limits(profile, frames, runtime)
    with pytest.raises(ValueError, match="timeout_s"):
        runner.validate_board_run_limits("stageb-batch", 30, 30.0, timeout_s=5.1)


def test_fps_uses_elapsed_intervals_between_observed_frames():
    assert runner.average_observed_fps(frame_count=1, elapsed_s=0.1) is None
    assert runner.average_observed_fps(frame_count=2, elapsed_s=0.1) == pytest.approx(10.0)


def test_stageb_manifest_pins_current_boot_wo15_manifest_gate_and_executor_hashes(tmp_path):
    executor_hashes = {
        name: runner.preflight.sha256_file(runner.STAGEB_EXECUTOR_PATHS[name])
        for name in runner.STAGEB_EXECUTOR_FILES
    }
    payload = {
        "schema": 1,
        "work_order": "WO-0016",
        "stage": "calibration-batch-preview",
        "boot_id": runner.preflight.AUTHORIZED_BOOT_ID,
        "wo15_manifest_sha256": "a" * 64,
        "tier2_dma_addresses": {"mm2s": 0x15880000, "s2mm": 0x15860000},
        "executor_sha256": executor_hashes,
    }
    path = tmp_path / "stageb-runtime-manifest.json"
    raw = json.dumps(payload, sort_keys=True, indent=2).encode() + b"\n"
    path.write_bytes(raw)

    result = runner.validate_stageb_manifest(
        path, hashlib.sha256(raw).hexdigest(),
        wo15_manifest_sha256="a" * 64,
        boot_id=runner.preflight.AUTHORIZED_BOOT_ID,
    )

    assert result["manifest_sha256"] == hashlib.sha256(raw).hexdigest()
    with pytest.raises(RuntimeError, match="boot_id"):
        runner.validate_stageb_manifest(
            path, hashlib.sha256(raw).hexdigest(),
            wo15_manifest_sha256="a" * 64, boot_id="stale-boot",
        )


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


def test_board_smoke_profile_rejects_any_frame_count_other_than_three(tmp_path):
    with pytest.raises(ValueError, match="exactly three frames"):
        runner.require_board_opt_in(
            execute=True, authorize_stage=runner.WO16_STAGE, approve_once=True,
            manifest="run-manifest.json", manifest_sha256="a" * 64, max_frames=4,
            run_profile="smoke3", max_runtime_s=30.0,
            timeout_s=5.0,
            evidence_json=str(tmp_path / "evidence.json"),
            stageb_manifest="stageb-runtime-manifest.json",
            stageb_manifest_sha256="b" * 64,
            analysis_json=str(tmp_path / "analysis.json"),
        )


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


def test_dma_safety_read_times_out_when_mmio_call_is_stuck(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(runner, "MAX_IO_TIMEOUT_S", 0.02)

    class StuckStatusAPI:
        def read32(self, base, offset):
            started.set()
            release.wait(timeout=1.0)
            return 0x1002

    started_at = time.perf_counter()
    assert not _dma_buffers_safe_to_release(StuckStatusAPI(), object(), None)
    assert started.is_set()
    assert time.perf_counter() - started_at < 0.2
    release.set()


def test_timed_out_board_operation_marks_dma_ownership_uncertain():
    from types import SimpleNamespace

    started = threading.Event()
    release = threading.Event()
    stream = SimpleNamespace(dma_may_be_active=False)

    async def exercise():
        with pytest.raises(TimeoutError):
            await _bounded_board_operation(
                lambda: (started.set(), release.wait(timeout=1.0)),
                timeout_s=0.02,
                thread_name="test-stuck-mmio",
                stream=stream,
            )

    started_at = time.perf_counter()
    asyncio.run(exercise())
    assert started.is_set()
    assert time.perf_counter() - started_at < 0.2
    assert stream.dma_may_be_active
    release.set()


def test_dma_buffers_stay_held_when_stream_may_still_be_active():
    from types import SimpleNamespace

    assert not _dma_buffers_safe_to_release(
        object(), object(), SimpleNamespace(dma_may_be_active=True)
    )


def test_cma_buffer_release_is_bounded_and_keeps_uncertain_release_unmarked(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(runner, "MAX_IO_TIMEOUT_S", 0.02)

    class StuckFreeAPI:
        def __init__(self):
            self.calls = 0

        def free(self, _buffer):
            self.calls += 1
            started.set()
            release.wait(timeout=1.0)

    api = StuckFreeAPI()
    buffer = object()
    released_ids = set()
    started_at = time.perf_counter()
    with pytest.raises(BufferReleaseUncertain, match="did not complete cleanly"):
        _release_buffers(api, (buffer,), released_ids)

    assert started.is_set()
    assert api.calls == 1
    assert id(buffer) not in released_ids
    assert time.perf_counter() - started_at < 0.2
    release.set()


def test_successful_buffer_release_collects_garbage_after_explicit_free(monkeypatch):
    events = []
    monkeypatch.setattr(runner.gc, "collect", lambda: events.append("gc") or 4)

    class ReleaseAPI:
        def free(self, buffer):
            events.append(("free", buffer))

    first = object()
    second = object()
    _release_buffers(ReleaseAPI(), (first, second))

    assert events == [("free", first), ("free", second), "gc"]


@pytest.mark.parametrize("blocked_operation", ["open", "is_opened", "set", "get"])
def test_camera_initialization_calls_do_not_block_async_camera_stream(blocked_operation):
    call_started = threading.Event()
    finish_call = threading.Event()
    camera_released = threading.Event()

    def wait_if_blocked(operation):
        if blocked_operation == operation:
            call_started.set()
            finish_call.wait(timeout=1.0)

    class LateCapture:
        def isOpened(self):
            wait_if_blocked("is_opened")
            return True

        def set(self, prop, value):
            wait_if_blocked("set")
            return True

        def get(self, prop):
            wait_if_blocked("get")
            if prop == FakeCv2.CAP_PROP_FOURCC:
                return FakeCv2.VideoWriter_fourcc(*"MJPG")
            return {
                FakeCv2.CAP_PROP_FRAME_WIDTH: 640,
                FakeCv2.CAP_PROP_FRAME_HEIGHT: 400,
                FakeCv2.CAP_PROP_FPS: 60,
            }[prop]

        def release(self):
            camera_released.set()

    class FakeCv2:
        CAP_V4L2 = 1
        CAP_PROP_FOURCC = 2
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5
        CAP_PROP_BUFFERSIZE = 6
        INTER_NEAREST = 0

        @staticmethod
        def VideoCapture(device, backend):
            wait_if_blocked("open")
            return LateCapture()

        @staticmethod
        def VideoWriter_fourcc(*chars):
            return sum(ord(char) << (8 * index) for index, char in enumerate(chars))

        @staticmethod
        def resize(image, size, interpolation):
            return image

    async def consume_one():
        async for _ in camera_gray_frames(
            "/dev/video-test", max_frames=1, cv2_module=FakeCv2, read_timeout_s=0.02
        ):
            pytest.fail("camera initialization exceeding its deadline must not yield")

    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        asyncio.run(consume_one())
    elapsed = time.perf_counter() - started

    assert call_started.is_set()
    assert elapsed < 0.2
    finish_call.set()
    assert camera_released.wait(0.3)


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
        async for _ in camera_gray_frames("/dev/video-test", max_frames=1,
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
        async for _ in camera_gray_frames("/dev/video-test", max_frames=1,
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
        async for _ in camera_gray_frames("/dev/video-test", max_frames=1,
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
        return [frame async for frame in camera_gray_frames(
            "/dev/video-test", max_frames=2, cv2_module=FakeCv2,
            capture_timings_ms=capture_timings_ms,
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
    }


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
        frames = camera_gray_frames("/dev/video-test", max_frames=2,
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
        frames = camera_gray_frames("/dev/video-test", max_frames=2,
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
        async for _ in camera_gray_frames("/dev/video-test", max_frames=1,
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
