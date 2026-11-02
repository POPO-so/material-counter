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
