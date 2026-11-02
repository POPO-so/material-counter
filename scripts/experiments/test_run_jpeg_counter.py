from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import uuid


SCRIPT = Path(__file__).with_name("run_jpeg_counter.py")
SPEC = importlib.util.spec_from_file_location("run_jpeg_counter_tests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _segment(marker: int, body: bytes) -> bytes:
    return bytes((0xFF, marker)) + (len(body) + 2).to_bytes(2, "big") + body


def _supported_jpeg_header() -> bytes:
    quantization = _segment(0xDB, bytes((0,)) + bytes((1,)) * 64 +
                            bytes((1,)) + bytes((1,)) * 64)
    huffman_tables = b""
    for table_class in (0, 1):
        for table_id in (0, 1):
            huffman_tables += _segment(
                0xC4, bytes(((table_class << 4) | table_id, 1)) + bytes(15) + b"\x00")
    frame = _segment(0xC0, bytes((8, 1, 144, 2, 128, 3,
                                  1, 0x21, 0, 2, 0x11, 1, 3, 0x11, 1)) )
    restart = _segment(0xDD, bytes((0, 40)))
    scan = _segment(0xDA, bytes((3, 1, 0, 2, 0x11, 3, 0x11, 0, 63, 0)))
    return b"\xff\xd8" + quantization + huffman_tables + frame + restart + scan + b"\x00\xff\xd9"


def test_artifact_admission_requires_matching_bit_hwh_hashes_and_routed_state():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        bit = root / "joint.bit"
        hwh = root / "joint.hwh"
        bit.write_bytes(b"routed bitstream")
        hwh.write_bytes(b"matching hardware handoff")

        binding = runner.verify_runtime_artifacts(bit, digest(bit), hwh, digest(hwh),
                                                  status="ROUTED_NOT_BOARD_VERIFIED")

        assert binding["bit_sha256"] == digest(bit)
        assert binding["hwh_sha256"] == digest(hwh)
        assert binding["status"] == "ROUTED_NOT_BOARD_VERIFIED"


def test_artifact_admission_rejects_stale_hash_or_non_routed_evidence():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        bit = root / "joint.bit"
        hwh = root / "joint.hwh"
        bit.write_bytes(b"routed bitstream")
        hwh.write_bytes(b"matching hardware handoff")

        try:
            runner.verify_runtime_artifacts(bit, "0" * 64, hwh, digest(hwh),
                                            status="ROUTED_NOT_BOARD_VERIFIED")
        except ValueError as exc:
            assert "bitstream SHA-256" in str(exc)
        else:
            raise AssertionError("stale bitstream digest was accepted")

        try:
            runner.verify_runtime_artifacts(bit, digest(bit), hwh, digest(hwh), status="SYNTH_ONLY")
        except ValueError as exc:
            assert "routed" in str(exc)
        else:
            raise AssertionError("synthesis-only artifacts were accepted for the runtime entry")


def test_hdmi_start_configures_gray8_video_out_after_setting_clocks(monkeypatch):
    calls = []
    clocks = types.SimpleNamespace(fclk0_mhz=0, fclk1_mhz=0, fclk2_mhz=0)
    pynq = types.ModuleType("pynq")
    pynq.Clocks = clocks
    lib = types.ModuleType("pynq.lib")
    video = types.ModuleType("pynq.lib.video")
    pixel_gray = object()

    class VideoMode:
        def __init__(self, width, height, bits_per_pixel, fps):
            self.value = (width, height, bits_per_pixel, fps)

    video.PIXEL_GRAY = pixel_gray
    video.VideoMode = VideoMode
    monkeypatch.setitem(sys.modules, "pynq", pynq)
    monkeypatch.setitem(sys.modules, "pynq.lib", lib)
    monkeypatch.setitem(sys.modules, "pynq.lib.video", video)

    class HDMI:
        def configure(self, mode, pixel_format):
            calls.append(("configure", mode.value, pixel_format))

        def start(self):
            calls.append(("start",))

    hdmi = HDMI()
    vdma = object()
    actual_clocks = runner.start_hdmi(hdmi, vdma)

    assert actual_clocks == [50.0, 100.0, 142.857]
    assert hdmi._vdma is vdma
    assert calls == [("configure", (1280, 720, 8, 60), pixel_gray), ("start",)]


def test_hdmi_frame_marker_has_expected_identity_and_crc():
    session = uuid.UUID("01234567-89ab-cdef-0123-456789abcdef")
    marker = runner._encode_output_marker(session, 23)

    assert len(marker) == 28
    assert marker[:2] == b"MC"
    assert marker[2:18] == session.bytes
    assert int.from_bytes(marker[18:26], "big") == 23
    assert int.from_bytes(marker[26:], "big") == runner.binascii.crc_hqx(marker[:26], 0xFFFF)


def test_jpeg_fixture_accepts_one_complete_bounded_jpeg():
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "fixture.jpg"
        payload = _supported_jpeg_header()
        path.write_bytes(payload)

        assert runner.load_jpeg_fixture(path, frames=1) == payload


def test_jpeg_fixture_rejects_frame_replay_and_invalid_or_oversized_payloads():
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "fixture.jpg"
        path.write_bytes(_supported_jpeg_header())
        try:
            runner.load_jpeg_fixture(path, frames=2)
        except ValueError as exc:
            assert "single frame" in str(exc)
        else:
            raise AssertionError("single-frame JPEG fixture accepted multiple frames")

        path.write_bytes(b"not jpeg")
        try:
            runner.load_jpeg_fixture(path, frames=1)
        except ValueError as exc:
            assert "SOI and EOI" in str(exc)
        else:
            raise AssertionError("JPEG fixture without markers was accepted")

        path.write_bytes(b"\xff\xd8" + b"x" * runner.JPEG_MAX_BYTES + b"\xff\xd9")
        try:
            runner.load_jpeg_fixture(path, frames=1)
        except ValueError as exc:
            assert "exceeds" in str(exc)
        else:
            raise AssertionError("oversized JPEG fixture was accepted")


def test_jpeg_fixture_rejects_grayscale_sof_before_hardware_access():
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "grayscale.jpg"
        sof_body = bytes((8, 1, 144, 2, 128, 1, 1, 0x11, 0))
        path.write_bytes(b"\xff\xd8\xff\xc0\x00\x0b" + sof_body + b"\xff\xd9")

        try:
            runner.load_jpeg_fixture(path, frames=1)
        except ValueError as exc:
            assert "3-component" in str(exc)
        else:
            raise AssertionError("grayscale JPEG unsupported by the PL decoder was accepted")
def test_jpeg_fixture_sequence_rejects_count_mismatch_and_bad_frames():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        frame1 = root / "frame_000001.jpg"
        frame2 = root / "frame_000002.jpg"
        frame1.write_bytes(_supported_jpeg_header())
        frame2.write_bytes(_supported_jpeg_header())

        assert runner.load_jpeg_fixture_sequence([frame1, frame2], frames=2) == [
            _supported_jpeg_header(), _supported_jpeg_header()
        ]
        try:
            runner.load_jpeg_fixture_sequence([frame1], frames=2)
        except ValueError as exc:
            assert "one JPEG per frame" in str(exc)
        else:
            raise AssertionError("fixture sequence accepted a frame-count mismatch")

        frame2.write_bytes(b"\xff\xd8\xff\xc0\x00\x0b" +
                           bytes((8, 1, 144, 2, 128, 1, 1, 0x11, 0)) + b"\xff\xd9")
        try:
            runner.load_jpeg_fixture_sequence([frame1, frame2], frames=2)
        except ValueError as exc:
            assert "3-component" in str(exc)
        else:
            raise AssertionError("fixture sequence accepted an unsupported frame")


def test_main_rejects_unsupported_fixture_before_artifact_or_hardware_access():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        fixture = root / "grayscale.jpg"
        fixture.write_bytes(b"\xff\xd8\xff\xc0\x00\x0b" +
                            bytes((8, 1, 144, 2, 128, 1, 1, 0x11, 0)) + b"\xff\xd9")
        original = runner.verify_runtime_artifacts

        def forbidden(*args, **kwargs):
            raise AssertionError("runtime artifact admission happened before JPEG preflight")

        runner.verify_runtime_artifacts = forbidden
        try:
            try:
                runner.main([
                    "--execute", "--bit", str(root / "missing.bit"), "--bit-sha256", "0",
                    "--hwh", str(root / "missing.hwh"), "--hwh-sha256", "0",
                    "--build-status", runner.ROUTED_STATUS, "--boot-id", "test",
                    "--frames", "1", "--input-jpeg", str(fixture),
                    "--output", str(root / "report.json"),
                ])
            except SystemExit as exc:
                assert exc.code == 2
            else:
                raise AssertionError("main accepted an unsupported grayscale JPEG")
        finally:
            runner.verify_runtime_artifacts = original
