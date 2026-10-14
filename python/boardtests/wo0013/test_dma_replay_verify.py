import unittest
import inspect

from dma_replay_verify import (
    DEFAULT_DMA_BASE,
    DEFAULT_MONITOR_BASE,
    FRAME_BEATS,
    FRAME_BYTES,
    PYNQ_Z2_DDR_LIMIT,
    ReplayError,
    bounded_poll,
    _channel_started_result,
    make_tb_payload,
    resolve_hwh_base,
    summarize_payload,
    validate_dma_range,
    validate_dma_source_alignment,
    verify_monitor_expected_bytes,
    verify_monitor_snapshot,
    run_replay,
)


class ReplayFixtureTests(unittest.TestCase):
    def test_exact_tb_fixture_and_checksum(self):
        payload = make_tb_payload()
        expected = summarize_payload(payload)
        self.assertEqual(len(payload), 76816)
        self.assertEqual(FRAME_BYTES, 76816)
        self.assertEqual(FRAME_BEATS, 19204)
        self.assertEqual(expected.first_word, 0xFFFF00FF)
        self.assertEqual(expected.last_word, 0x12340000)
        self.assertEqual(expected.foreground_pixels, 32336)
        self.assertEqual(expected.checksum, 0x70B5017B)

    def test_monitor_acceptance_and_corruption_detection(self):
        expected = summarize_payload(make_tb_payload())
        snapshot = {
            "status": 0x2,
            "byte_count": expected.byte_count,
            "beat_count": expected.beat_count,
            "first_word": expected.first_word,
            "last_word": expected.last_word,
            "tlast_count": expected.tlast_count,
            "checksum": expected.checksum,
            "error_code": 0,
            "expected_bytes": expected.byte_count,
        }
        verify_monitor_snapshot(snapshot, expected)
        snapshot["tlast_count"] = 2
        with self.assertRaisesRegex(ReplayError, "tlast_count"):
            verify_monitor_snapshot(snapshot, expected)

    def test_hwh_addresses_are_resolved_and_checked(self):
        ip_dict = {
            "axi_dma_0": {"phys_addr": DEFAULT_DMA_BASE},
            "rx_monitor": {"phys_addr": DEFAULT_MONITOR_BASE},
        }
        self.assertEqual(resolve_hwh_base(ip_dict, "axi_dma_0", DEFAULT_DMA_BASE),
                         DEFAULT_DMA_BASE)
        self.assertEqual(resolve_hwh_base(ip_dict, "rx_monitor", DEFAULT_MONITOR_BASE),
                         DEFAULT_MONITOR_BASE)
        with self.assertRaisesRegex(ReplayError, "HWH base"):
            resolve_hwh_base(ip_dict, "axi_dma_0", DEFAULT_DMA_BASE + 0x1000)

    def test_hwh_interface_qualified_module_ref_key_is_supported(self):
        ip_dict = {"rx_monitor/s_axi": {"phys_addr": DEFAULT_MONITOR_BASE}}
        self.assertEqual(resolve_hwh_base(ip_dict, "rx_monitor", DEFAULT_MONITOR_BASE),
                         DEFAULT_MONITOR_BASE)

    def test_runtime_default_uses_generated_hwh_monitor_cell_name(self):
        self.assertEqual(inspect.signature(run_replay).parameters["monitor_name"].default,
                         "rx_monitor")

    def test_zero_dmasr_is_a_valid_running_status(self):
        self.assertTrue(_channel_started_result(0))
        self.assertIsNone(_channel_started_result(1))

    def test_source_range_must_fit_pynq_z2_ddr_window(self):
        validate_dma_range(PYNQ_Z2_DDR_LIMIT - FRAME_BYTES, FRAME_BYTES)
        with self.assertRaisesRegex(ReplayError, "exceeds PYNQ-Z2 DDR/HP window"):
            validate_dma_range(PYNQ_Z2_DDR_LIMIT - FRAME_BYTES + 1, FRAME_BYTES)

    def test_source_address_requires_64_bit_path_alignment(self):
        validate_dma_source_alignment(0x00100000)
        with self.assertRaisesRegex(ReplayError, "8-byte aligned"):
            validate_dma_source_alignment(0x00100004)

    def test_monitor_expected_length_is_checked_before_dma(self):
        expected = summarize_payload(make_tb_payload())
        verify_monitor_expected_bytes(FRAME_BYTES, expected)
        with self.assertRaisesRegex(ReplayError, "EXPECTED_BYTES"):
            verify_monitor_expected_bytes(FRAME_BYTES - 4, expected)

    def test_poll_timeout_is_bounded(self):
        with self.assertRaisesRegex(TimeoutError, "unit test timeout"):
            bounded_poll(lambda: None, 0.01, "unit test timeout", 0.001)


if __name__ == "__main__":
    unittest.main()
