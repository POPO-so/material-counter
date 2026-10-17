import asyncio
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_stream_counter import (  # noqa: E402
    build_synthetic_masks,
    claim_once_guard,
    _dma_buffers_safe_to_release,
    camera_gray_frames,
    run_mock_simulation,
    verify_afi1_readback,
)
from stream_counter import MaskCounter  # noqa: E402


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
    with pytest.raises(RuntimeError, match="camera read timed out"):
        asyncio.run(consume_one())
    assert time.perf_counter() - started < 0.2
    assert released.wait(0.2)


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
