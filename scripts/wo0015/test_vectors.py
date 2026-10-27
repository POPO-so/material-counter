"""Focused tests for the independent WO-0015 vector contract."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from prepare_vectors import (
    Beat,
    PIXELS,
    REPO_ROOT,
    binary8_from_gray,
    generate,
    make_packet,
    resolve_inputs,
    validate_source_manifest,
)


class VectorContractTests(unittest.TestCase):
    @staticmethod
    def _synthetic_h3(root: Path) -> tuple[Path, Path]:
        import hashlib
        import json

        from prepare_vectors import HEIGHT, WIDTH

        h3_root = root / "h3"
        frame_dir = h3_root / "fixture" / "frames"
        frame_dir.mkdir(parents=True)
        records = []
        for index in range(487):
            path = frame_dir / f"frame_{index:03d}.png"
            image = Image.new("L", (WIDTH, HEIGHT), index % 256)
            image.save(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            records.append({
                "path": f"data/synthetic/h3_full/fixture/frames/{path.name}",
                "sha256": digest, "width": WIDTH, "height": HEIGHT, "mode": "L",
            })
        manifest_path = root / "source-manifest.json"
        manifest_path.write_text(json.dumps({"h3": {
            "width": WIDTH, "height": HEIGHT, "frames": 487,
            "png_count_in_repository": 487, "input_sha256": "0" * 64,
            "expected_sha256": "1" * 64, "source_pngs": records,
        }}), encoding="utf-8")
        return h3_root, manifest_path

    def test_default_offline_manifest_is_a_traceable_canonical_fixture(self) -> None:
        manifest_path = REPO_ROOT / "scripts/wo0015/fixtures/wo0014-h3-source-manifest.json"
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
        h3_root = REPO_ROOT / "data/synthetic/h3_full"
        if len(list(h3_root.glob("*/frames/frame_*.png"))) != 487:
            self.skipTest("canonical H3 PNG corpus is not included in this checkout")
        _, records = validate_source_manifest(h3_root, manifest_path)
        self.assertEqual(len(records), 487)

    def test_binary_threshold_includes_equality(self) -> None:
        self.assertEqual(binary8_from_gray(bytes((127, 128, 255))), bytes((0, 255, 255)))

    def test_40_bit_record_has_tdata_low_and_control_high(self) -> None:
        self.assertEqual(Beat(0x12345678, keep=0x7, last=1).encode_hex(), "1712345678\n")

    def test_small_packet_has_one_final_tlast_and_partial_keep(self) -> None:
        beats = make_packet(bytes((1, 2, 3, 4, 5)), width=1, height=5, frame_id=7)
        self.assertEqual(len(beats), 4 + 2 + 4)
        payload = beats[4:6]
        self.assertEqual([beat.keep for beat in payload], [0xF, 0x1])
        self.assertEqual([index for index, beat in enumerate(beats) if beat.last], [len(beats)-1])
        self.assertEqual(beats[2].data, 5)
        self.assertEqual(beats[-2].data, 15)
        self.assertEqual(beats[-1].data, 0xC0DEC018)

    def test_prepare_a_validates_source_manifest_and_records_frame_hashes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wo0015-source-") as source_temporary:
            h3_root, source_manifest_path = self._synthetic_h3(Path(source_temporary))
            source_manifest, records = validate_source_manifest(h3_root, source_manifest_path)
            self.assertEqual(len(records), 487)
            self.assertEqual(source_manifest["h3"]["frames"], 487)

            with tempfile.TemporaryDirectory(prefix="wo0015-vectors-") as temporary:
                out = Path(temporary) / "vectors"
                summary = generate(out, REPO_ROOT, h3_root, source_manifest_path,
                                   case_names=("A",))
                case = summary["cases"]["A"]
                self.assertEqual(case["input_beat_count"], 19208)
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
