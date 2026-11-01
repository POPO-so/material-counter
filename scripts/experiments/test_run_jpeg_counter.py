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
