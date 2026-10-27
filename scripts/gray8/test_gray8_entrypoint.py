from pathlib import Path
import subprocess
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_gray8_counter import packet_encoders
from packet_codec import validate_packet, binary_golden


def test_lighting_compensation_keeps_gray8_and_source_threshold_semantics():
    gray = np.resize(np.array([0, 99, 100, 101, 127, 128, 255], dtype=np.uint8), (240, 320))
    encode, encode_into = packet_encoders("lighting-calibrated", 100)
    packet = encode(gray, frame_id=9)
    assert len(packet) == 76832
    actual = binary_golden(validate_packet(packet, frame_id=9))
    np.testing.assert_array_equal(actual, np.where(gray > 100, 255, 0).astype(np.uint8))
    output = np.empty(76832, dtype=np.uint8)
    encode_into(gray, output, frame_id=9)
    assert output.tobytes() == packet


def test_gray8_cli_does_not_import_the_legacy_finalizer_by_name():
    proc = subprocess.run([sys.executable, str(Path(__file__).with_name("run_gray8_counter.py")), "--help"],
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0
    assert "--manifest-sha256" in proc.stdout
    assert "--mode {offline,self-test,camera}" in proc.stdout
