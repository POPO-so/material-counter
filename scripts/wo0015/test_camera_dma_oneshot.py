"""Offline tests for the guarded OV9281-to-PL one-frame bridge."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import board_once
import board_preflight as preflight
from camera_dma_oneshot import (
    CameraHistoryAdapter,
    capture_v4l2_once,
    load_gray_file,
    prepare_camera_bundle,
    require_camera_authorization,
    resize_gray8,
    validate_prior_step3_evidence,
)
from test_board_preflight import make_manifest


def _write_pgm(path: Path, image: np.ndarray) -> None:
    path.write_bytes(f"P5\n{image.shape[1]} {image.shape[0]}\n255\n".encode() + image.tobytes())


def _make_manifest(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    return make_manifest(root, frame_count=3)


def test_gray_pgm_load_and_bilinear_resize_are_deterministic(tmp_path):
    source = np.array([[0, 255], [255, 0]], dtype=np.uint8)
    path = tmp_path / "ov9281.pgm"
    _write_pgm(path, source)

    loaded = load_gray_file(path)
    resized = resize_gray8(loaded, width=3, height=3)

    assert np.array_equal(loaded, source)
    assert resized.shape == (3, 3)
    assert resized.dtype == np.uint8
    assert resized[1, 1] == 128


def test_numpy_and_png_inputs_are_loaded_as_grayscale(tmp_path):
    source = np.arange(24, dtype=np.uint8).reshape(4, 6)
    npy_path = tmp_path / "capture.npy"
    np.save(npy_path, source)
    assert np.array_equal(load_gray_file(npy_path), source)

    from PIL import Image

    png_path = tmp_path / "capture.png"
    Image.fromarray(source, mode="L").save(png_path)
    assert np.array_equal(load_gray_file(png_path), source)


def test_v4l2_capture_reads_exactly_one_frame_and_releases_device():
    class FakeCapture:
        def __init__(self):
            self.read_calls = 0
            self.released = False

        def isOpened(self):
            return True

        def read(self):
            self.read_calls += 1
            return True, np.full((800, 1280), 73, dtype=np.uint8)

        def release(self):
            self.released = True

    class FakeCV2:
        CAP_V4L2 = 200

        def __init__(self):
            self.capture = FakeCapture()
            self.calls = []

        def VideoCapture(self, device, backend):
            self.calls.append((device, backend))
            return self.capture

    cv2 = FakeCV2()
    captured = capture_v4l2_once("/dev/video0", cv2_module=cv2)

    assert cv2.calls == [("/dev/video0", cv2.CAP_V4L2)]
    assert cv2.capture.read_calls == 1
    assert cv2.capture.released
    assert captured.shape == (800, 1280)
    assert np.all(captured == 73)


def test_prepare_bundle_emits_valid_manifest_packet_golden_and_viewable_pgm(tmp_path):
    template_path, _ = _make_manifest(tmp_path / "template")
    source = np.full((480, 640), 20, dtype=np.uint8)
    source[100:300, 150:500] = 200
    source_path = tmp_path / "camera.npy"
    np.save(source_path, source)

    prepared = prepare_camera_bundle(
        template_manifest_path=template_path,
        input_path=source_path,
        output_root=tmp_path / "camera-runs",
    )

    plan = preflight.validate_manifest(
        prepared["manifest_path"], prepared["manifest_sha256"]
    )
    assert len(plan.frames) == 3
    assert plan.frames[0].frame_id == 4
    assert len(plan.frames[0].packet) == preflight.TX_BYTES
    assert plan.frames[0].binary == np.where(
        np.asarray(load_gray_file(prepared["resized_gray_path"])) >= 128,
        0xFF, 0x00,
    ).astype(np.uint8).tobytes()
    assert Path(prepared["source_gray_path"]).is_file()
    assert Path(prepared["resized_gray_path"]).is_file()
    assert Path(prepared["packet_path"]).stat().st_size == preflight.TX_BYTES
    assert Path(prepared["golden_path"]).stat().st_size == preflight.RX_BYTES
    assert prepared["reserved_unrun_frame_ids"] == [5, 6]
    assert json.loads(Path(prepared["camera_run_path"]).read_text())["manifest_sha256"] == prepared["manifest_sha256"]


def test_prior_step3_evidence_requires_three_zero_mismatch_frames(tmp_path):
    manifest_path, manifest = _make_manifest(tmp_path / "inputs")
    plan = preflight.validate_manifest(manifest_path)
    evidence = {
        "status": "DMA_THREE_PASS",
        "boot_id": plan.boot_id,
        "manifest_sha256": plan.manifest_sha256,
        "frame_count": 3,
        "frames": [
            {"frame_id": frame.frame_id, "byte_mismatches": 0}
            for frame in plan.frames
        ],
    }
    evidence_path = tmp_path / "dma-three-result.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    validated = validate_prior_step3_evidence(
        evidence_path, manifest_path, plan.manifest_sha256
    )
    assert [frame.frame_id for frame in validated.frames] == [1, 2, 3]

    evidence["frames"][1]["byte_mismatches"] = 1
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(preflight.PreflightError, match="zero byte mismatches"):
        validate_prior_step3_evidence(evidence_path, manifest_path, plan.manifest_sha256)


def test_camera_dma_requires_explicit_stage_once_and_exact_manifest_hash():
    digest = "a" * 64
    with pytest.raises(preflight.PreflightError, match="--execute"):
        require_camera_authorization(
            execute=False, authorize_stage="camera-dma-single", approve_once=True,
            manifest_sha256=digest, expected_manifest_sha256=digest,
            boot_id=preflight.AUTHORIZED_BOOT_ID,
        )
    with pytest.raises(preflight.PreflightError, match="--authorize-stage"):
        require_camera_authorization(
            execute=True, authorize_stage="dma-single", approve_once=True,
            manifest_sha256=digest, expected_manifest_sha256=digest,
            boot_id=preflight.AUTHORIZED_BOOT_ID,
        )
    with pytest.raises(preflight.PreflightError, match="manifest SHA-256"):
        require_camera_authorization(
            execute=True, authorize_stage="camera-dma-single", approve_once=True,
            manifest_sha256=digest, expected_manifest_sha256="b" * 64,
            boot_id=preflight.AUTHORIZED_BOOT_ID,
        )
    require_camera_authorization(
        execute=True, authorize_stage="camera-dma-single", approve_once=True,
        manifest_sha256=digest, expected_manifest_sha256=digest,
        boot_id=preflight.AUTHORIZED_BOOT_ID,
    )


def test_history_adapter_rebases_counters_and_mock_dma_checks_next_frame(tmp_path):
    template_path, _ = _make_manifest(tmp_path / "template")
    source = np.full((preflight.HEIGHT, preflight.WIDTH), 0, dtype=np.uint8)
    source[40:180, 100:250] = 255
    source_path = tmp_path / "camera.npy"
    np.save(source_path, source)
    prepared = prepare_camera_bundle(
        template_manifest_path=template_path,
        input_path=source_path,
        output_root=tmp_path / "camera-runs",
    )
    camera_plan = preflight.validate_manifest(prepared["manifest_path"], prepared["manifest_sha256"])
    api = board_once.MockBoardAPI(camera_plan, initial_idle=True)
    template_plan = preflight.validate_manifest(template_path)
    base_input_checksum = (
        2 * template_plan.frames[0].packet_checksum
        + template_plan.frames[1].packet_checksum
        + template_plan.frames[2].packet_checksum
    ) & 0xFFFFFFFF
    api._checker.update({
        board_once.CHECKER_STATUS: board_once.CHECKER_PACKET_DONE | board_once.CHECKER_OUTPUT_COMPLETE,
        board_once.CHECKER_FRAME_ID: template_plan.frames[-1].frame_id,
        board_once.CHECKER_PACKET_DONE_COUNT: 4,
        board_once.CHECKER_INPUT_BYTES: 4 * preflight.TX_BYTES,
        board_once.CHECKER_INPUT_BEATS: 4 * preflight.TX_BEATS,
        board_once.CHECKER_INPUT_TLAST: 4,
        board_once.CHECKER_INPUT_CHECKSUM: base_input_checksum,
        board_once.CHECKER_PIXELS: preflight.PIXEL_COUNT,
        board_once.CHECKER_SOFS: 1,
        board_once.CHECKER_EOLS: preflight.HEIGHT,
        board_once.CHECKER_OUTPUT_CHECKSUM: template_plan.frames[-1].binary_checksum,
        board_once.CHECKER_OUTPUT_TOTAL: 4 * preflight.PIXEL_COUNT,
    })
    api._packer_status = board_once.PACKER_DONE
    api._packer_pixels = preflight.PIXEL_COUNT
    api._packer_beats = preflight.RX_BEATS
    api._dma[board_once.DMA_MM2S_DMASR] = board_once.DMA_IDLE | board_once.DMA_IOC_IRQ
    api._dma[board_once.DMA_S2MM_DMASR] = board_once.DMA_IDLE | board_once.DMA_IOC_IRQ
    history_api = CameraHistoryAdapter(
        api,
        frame=camera_plan.frames[0],
        baseline=board_once._checker_snapshot(api),
        prior_frame_id=template_plan.frames[-1].frame_id,
    )

    result = board_once.run_frames(
        camera_plan, history_api, timeout_s=2.0,
        frame_limit=1, expected_prior_frames=1,
        diagnostic_dir=tmp_path / "camera-diagnostics",
    )

    assert result["frame_count"] == 1
    assert result["frames"][0]["frame_id"] == 4
    assert result["frames"][0]["byte_mismatches"] == 0
    assert result["status"] == "DMA_ONCE_PASS"
