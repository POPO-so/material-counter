"""Focused tests for the independent WO-0015 vector contract."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from prepare_vectors import (
    Beat,
    PIXELS,
    REPO_ROOT,
    binary8_from_gray,
    binary8_from_rgb,
    generate,
    gray_from_rgb,
    make_packet,
    resolve_inputs,
    validate_source_manifest,
)


class VectorContractTests(unittest.TestCase):
    def test_gray_formula_is_exact_for_equal_rgb_lanes(self) -> None:
        for value in (0, 1, 24, 73, 127, 128, 220, 254, 255):
            self.assertEqual(gray_from_rgb(value, value, value), value)

    def test_binary_threshold_includes_equality(self) -> None:
        self.assertEqual(binary8_from_rgb(127, 127, 127), 0x00)
        self.assertEqual(binary8_from_rgb(128, 128, 128), 0xFF)
        self.assertEqual(binary8_from_gray(bytes((127, 128, 255))), bytes((0, 255, 255)))

    def test_40_bit_record_has_tdata_low_and_control_high(self) -> None:
        self.assertEqual(Beat(0x12345678, keep=0x7, last=1).encode_hex(), "1712345678\n")

    def test_small_packet_has_one_final_tlast_and_partial_keep(self) -> None:
        beats = make_packet(bytes((1, 2, 3, 4, 5, 6)), width=1, height=2, frame_id=7)
        self.assertEqual(len(beats), 4 + 2 + 4)
        payload = beats[4:6]
        self.assertEqual([beat.keep for beat in payload], [0xF, 0x3])
        self.assertEqual([index for index, beat in enumerate(beats) if beat.last], [len(beats)-1])
        self.assertEqual(beats[-1].data, 0xC0DEC014)

    def test_prepare_a_validates_source_manifest_and_records_frame_hashes(self) -> None:
        h3_root, source_manifest_path = resolve_inputs(REPO_ROOT, None, None)
        source_manifest, records = validate_source_manifest(h3_root, source_manifest_path)
        self.assertEqual(len(records), 487)
        self.assertEqual(source_manifest["h3"]["frames"], 487)

        with tempfile.TemporaryDirectory(prefix="wo0015-vectors-") as temporary:
            out = Path(temporary) / "vectors"
            summary = generate(out, REPO_ROOT, case_names=("A",))
            case = summary["cases"]["A"]
            self.assertEqual(case["input_beat_count"], 57608)
            self.assertEqual(case["golden_bytes"], PIXELS)
            self.assertEqual(case["expected_eofs"], 1)
            self.assertEqual(case["source_manifest_sha256"],
                             hashlib.sha256(source_manifest_path.read_bytes()).hexdigest())
            golden = (out / "A" / "golden.bin").read_bytes()
            self.assertEqual(len(golden), PIXELS)
            self.assertEqual(hashlib.sha256(golden).hexdigest(), case["golden_sha256"])
            case_file = json.loads((out / "A" / "case.json").read_text(encoding="utf-8"))
            self.assertEqual(case_file["source_frames"][0]["sha256"], records[0]["sha256"])


if __name__ == "__main__":
    unittest.main()
