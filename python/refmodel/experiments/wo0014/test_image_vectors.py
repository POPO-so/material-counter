import struct
import tempfile
import unittest
from pathlib import Path

from image_vectors import (
    DIRECTED_THRESHOLDS,
    DIRECTED_HEIGHT,
    DIRECTED_WIDTH,
    H3_HEIGHT,
    H3_WIDTH,
    Beat,
    binary_pixel,
    directed_rgb,
    expected_bytes,
    h3_pngs,
    make_negative_cases,
    make_partial_keep_fixtures,
    packet_beats,
    read_h3_rgb,
)


class PacketVectorTests(unittest.TestCase):
    def test_37_bit_record_lane_order(self):
        self.assertEqual(Beat(0x11223344, 0xB, 1).encode(), bytes.fromhex("44 33 22 11 1b"))

    def test_header_payload_keep_trailer_and_tlast(self):
        payload = bytes(range(9))
        beats = packet_beats(payload, 3, 1, 0x12345678)
        self.assertEqual([beat.data for beat in beats[:4]], [
            0x574F3134, 0x00010003, 9, 0x12345678,
        ])
        self.assertEqual([beat.keep for beat in beats[4:7]], [0xF, 0xF, 0x1])
        self.assertEqual([beat.last for beat in beats], [0] * (len(beats) - 1) + [1])
        self.assertEqual([beat.data for beat in beats[-4:]], [
            0x454E4434, 0x12345678, sum(payload), 0xC0DEC014,
        ])
        self.assertEqual(sum(beat.keep.bit_count() for beat in beats), 32 + len(payload))

    def test_directed_partial_last_word_uses_contiguous_one_byte_keep(self):
        rgb = directed_rgb()
        beats = packet_beats(rgb, DIRECTED_WIDTH, DIRECTED_HEIGHT, 0xA014)
        payload_beats = beats[4:-4]
        self.assertEqual((DIRECTED_WIDTH, DIRECTED_HEIGHT, len(rgb)), (5, 3, 45))
        self.assertEqual(len(payload_beats), 12)
        self.assertEqual(payload_beats[-1].keep, 0x1)
        self.assertEqual(payload_beats[-1].last, 0)
        self.assertEqual(beats[-1].last, 1)

    def test_negative_packet_vectors_have_declared_recovery_and_single_tlast_bits(self):
        with tempfile.TemporaryDirectory() as directory:
            report = make_negative_cases(Path(directory))
            self.assertEqual(set(report["cases"]), {
                "bad_header", "bad_length", "bad_tkeep", "early_tlast",
                "missing_tlast", "bad_checksum", "extra_payload",
            })
            for record in report["cases"].values():
                encoded = (Path(directory) / "negative" / record["file"]).read_bytes()
                self.assertEqual(len(encoded) % 5, 0)
                self.assertEqual(len(encoded) // 5, record["total_records"])
                self.assertTrue(all((encoded[i + 4] & 0xE0) == 0
                                    for i in range(0, len(encoded), 5)))
            bad_keep = (Path(directory) / "negative" / "bad_tkeep.bin").read_bytes()
            self.assertEqual(bad_keep[4 * 5 + 4] & 0xF, 0x5)
            missing = report["cases"]["missing_tlast"]
            self.assertEqual(missing["drain_records"], missing["recovery_records"])
            missing_bytes = (Path(directory) / "negative" / missing["file"]).read_bytes()
            bad_last_offset = (missing["bad_records"] - 1) * 5 + 4
            self.assertEqual((missing_bytes[bad_last_offset] >> 4) & 1, 0)
            self.assertEqual(sum((missing_bytes[i + 4] >> 4) & 1
                                 for i in range(0, len(missing_bytes), 5)), 2)

    def test_valid_partial_keep_fixtures_cover_masks_3_and_7(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            fixtures = make_partial_keep_fixtures(out)
            for name, expected_keep, payload_bytes, pixels in (
                ("keep3", 0x3, 6, 2), ("keep7", 0x7, 3, 1),
            ):
                record = fixtures[name]
                self.assertEqual(record["final_payload_tkeep"], expected_keep)
                self.assertEqual(record["payload_bytes"], payload_bytes)
                packet = (out / record["input_file"]).read_bytes()
                beats = [packet[i:i + 5] for i in range(0, len(packet), 5)]
                self.assertEqual(beats[-5][4] & 0xF, expected_keep)
                self.assertEqual([(beat[4] >> 4) & 1 for beat in beats],
                                 [0] * (len(beats) - 1) + [1])
                self.assertEqual((out / record["expected_file"]).stat().st_size,
                                 pixels * 2)

    def test_rgb_formula_and_threshold_equality(self):
        self.assertEqual(binary_pixel(127, 128), 0x00)
        self.assertEqual(binary_pixel(128, 128), 0xFF)
        rgb = directed_rgb()
        self.assertEqual(len(rgb), DIRECTED_WIDTH * DIRECTED_HEIGHT * 3)
        for threshold in DIRECTED_THRESHOLDS:
            golden = expected_bytes(rgb, DIRECTED_WIDTH, DIRECTED_HEIGHT, threshold)
            self.assertEqual(len(golden), DIRECTED_WIDTH * DIRECTED_HEIGHT * 2)
            words = [struct.unpack_from("<H", golden, i)[0] for i in range(0, len(golden), 2)]
            self.assertEqual(words[0] & 0x300, 0x200)
            self.assertEqual(words[DIRECTED_WIDTH - 1] & 0x100, 0x100)
            self.assertTrue(all((word & 0xFF) in (0x00, 0xFF) for word in words))

    def test_all_h3_sources_are_gray8_320_by_240(self):
        sources = h3_pngs()
        self.assertEqual(len(sources), 487)
        for path in sources:
            rgb, record = read_h3_rgb(path)
            self.assertEqual((record["width"], record["height"], record["mode"]),
                             (H3_WIDTH, H3_HEIGHT, "L"))
            self.assertEqual(len(rgb), H3_WIDTH * H3_HEIGHT * 3)


if __name__ == "__main__":
    unittest.main()
