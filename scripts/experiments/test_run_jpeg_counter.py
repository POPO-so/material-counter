from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import tempfile


SCRIPT = Path(__file__).with_name("run_jpeg_counter.py")
SPEC = importlib.util.spec_from_file_location("run_jpeg_counter_tests", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bit() -> bytes:
    return runner._BIT_MAGIC + b"design\x00\x65\x00\x01"


def _hwh(*, device="7z020", package="clg400", speed="-1",
         bases=(0x40400000, 0x43C20000, 0x43C50000, 0x43C80000)) -> bytes:
    params = "".join(
        f'<PARAMETER NAME="C_BASEADDR" VALUE="0x{b:08X}"/>' for b in bases)
    return (f'<?xml version="1.0"?><EDKSYSTEM>'
            f'<SYSTEMINFO ARCH="zynq" DEVICE="{device}" PACKAGE="{package}" '
            f'SPEEDGRADE="{speed}"/>{params}</EDKSYSTEM>').encode()


def _hwh_hls() -> bytes:
    """Real HLS control interfaces use C_S_AXI_CONTROL_BASEADDR, not C_BASEADDR."""
    params = (
        '<PARAMETER NAME="C_BASEADDR" VALUE="0x40400000"/>'
        '<PARAMETER NAME="C_BASEADDR" VALUE="0x43C20000"/>'
        '<PARAMETER NAME="C_BASEADDR" VALUE="0x43C50000"/>'
        '<PARAMETER NAME="C_S_AXI_CONTROL_BASEADDR" VALUE="0x43C80000"/>'
    )
    return (f'<?xml version="1.0"?><EDKSYSTEM>'
            f'<SYSTEMINFO ARCH="zynq" DEVICE="7z020" PACKAGE="clg400" '
            f'SPEEDGRADE="-1"/>{params}</EDKSYSTEM>').encode()


def test_artifact_admission_accepts_hls_control_base_parameter():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        bit = root / "joint.bit"
        hwh = root / "joint.hwh"
        bit.write_bytes(_bit())
        hwh.write_bytes(_hwh_hls())

        binding = runner.verify_runtime_artifacts(bit, digest(bit), hwh, digest(hwh),
                                                  status="ROUTED_NOT_BOARD_VERIFIED")
        assert binding["jpeg_base"] == 0x43C80000
        assert binding["status"] == "ROUTED_NOT_BOARD_VERIFIED"


def test_artifact_admission_requires_matching_bit_hwh_hashes_and_routed_state():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        bit = root / "joint.bit"
        hwh = root / "joint.hwh"
        bit.write_bytes(_bit())
        hwh.write_bytes(_hwh())

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
        bit.write_bytes(_bit())
        hwh.write_bytes(_hwh())

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


def test_artifact_admission_rejects_plain_byte_files():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        bit = root / "fake.bit"
        hwh = root / "fake.hwh"
        bit.write_bytes(b"routed bitstream")
        hwh.write_bytes(b"matching hardware handoff")

        try:
            runner.verify_runtime_artifacts(bit, digest(bit), hwh, digest(hwh),
                                            status="ROUTED_NOT_BOARD_VERIFIED")
        except ValueError as exc:
            assert "magic header" in str(exc)
        else:
            raise AssertionError("a non-bitstream byte file was accepted")


def test_artifact_admission_rejects_wrong_part_or_missing_topology():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        # Wrong silicon part
        bit = root / "joint.bit"
        hwh = root / "joint.hwh"
        bit.write_bytes(_bit())
        hwh.write_bytes(_hwh(device="7z010"))

        try:
            runner.verify_runtime_artifacts(bit, digest(bit), hwh, digest(hwh),
                                            status="ROUTED_NOT_BOARD_VERIFIED")
        except ValueError as exc:
            assert "part" in str(exc)
        else:
            raise AssertionError("wrong-part HWH was accepted")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        bit = root / "joint.bit"
        hwh = root / "joint.hwh"
        bit.write_bytes(_bit())
        hwh.write_bytes(_hwh(bases=(0x40400000, 0x43C20000)))  # missing overlay + JPEG
        try:
            runner.verify_runtime_artifacts(bit, digest(bit), hwh, digest(hwh),
                                            status="ROUTED_NOT_BOARD_VERIFIED")
        except ValueError as exc:
            assert "base 0x" in str(exc)
        else:
            raise AssertionError("topology-incomplete HWH was accepted")