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
    PAYLOAD_BYTES,
    REPO_ROOT,
    binary8_from_gray,
    generate,
    make_packet,
    resolve_inputs,
    validate_source_manifest,
)


class VectorContractTests(unittest.TestCase):
    def test_default_offline_manifest_is_a_traceable_canonical_fixture(self) -> None:
        h3_root, manifest_path = resolve_inputs(REPO_ROOT, None, None)
        fixture_path = REPO_ROOT / "scripts/wo0015/fixtures/wo0014-h3-source-manifest.json"
        self.assertEqual(manifest_path, fixture_path.resolve())
        raw_manifest = manifest_path.read_bytes()
        self.assertEqual(len(raw_manifest), 192452)
        self.assertEqual(
            hashlib.sha256(raw_manifest).hexdigest(),
            "8d123469853ad2425e03d91590aa7473de4ca72a04fffaa6bc2d8964b377317d",
        )
        fixture = json.loads(raw_manifest)
        self.assertEqual(fixture["threshold"], 128)
        self.assertEqual(fixture["h3"]["frames"], 487)
        self.assertEqual(
            fixture["h3"]["input_sha256"],
            "ad5a5e5ca5bd95c41df900cc72ef0e1096dffa255d5e698ff904018e5fab1391",
        )
        self.assertEqual(
            fixture["h3"]["expected_sha256"],
            "3e2f35b2dbbb21b55408dcc62da11aa29217e304a9f976f7feac207aa384cced",
        )
        _, records = validate_source_manifest(h3_root, manifest_path)
        self.assertEqual(len(records), 487)

    def test_binary_threshold_includes_equality(self) -> None:
        self.assertEqual(binary8_from_gray(bytes((127, 128, 255))), bytes((0, 255, 255)))

    def test_40_bit_record_has_tdata_low_and_control_high(self) -> None:
        self.assertEqual(Beat(0x12345678, keep=0x7, last=1).encode_hex(), "1712345678\n")

    def test_small_gray_packet_has_one_final_tlast_and_partial_keep(self) -> None:
        gray = bytes((1, 2, 3, 4, 5, 6))
        beats = make_packet(gray, width=1, height=6, frame_id=7)
        self.assertEqual(len(beats), 4 + 2 + 4)
        payload = beats[4:6]
        self.assertEqual([beat.keep for beat in payload], [0xF, 0x3])
        self.assertEqual(b"".join(beat.data.to_bytes(4, "little")[:beat.keep.bit_count()]
                                 for beat in payload), gray)
        self.assertEqual([index for index, beat in enumerate(beats) if beat.last], [len(beats)-1])
        self.assertEqual(beats[2].data, len(gray))
        self.assertEqual(beats[-1].data, 0xC0DEC014)
        self.assertEqual(beats[-2].data, sum(gray))

    def test_prepare_a_validates_source_manifest_and_records_frame_hashes(self) -> None:
        h3_root, source_manifest_path = resolve_inputs(REPO_ROOT, None, None)
        source_manifest, records = validate_source_manifest(h3_root, source_manifest_path)
        self.assertEqual(len(records), 487)
        self.assertEqual(source_manifest["h3"]["frames"], 487)

        with tempfile.TemporaryDirectory(prefix="wo0015-vectors-") as temporary:
            out = Path(temporary) / "vectors"
            summary = generate(out, REPO_ROOT, case_names=("A",))
            case = summary["cases"]["A"]
            self.assertEqual(case["input_beat_count"], 4 + PAYLOAD_BYTES // 4 + 4)
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
