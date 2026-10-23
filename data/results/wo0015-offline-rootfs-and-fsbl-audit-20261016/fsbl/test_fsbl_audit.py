from pathlib import Path
import importlib.util
import json
import struct
import unittest

MODULE_PATH = Path(__file__).with_name('audit_fsbl.py')
spec = importlib.util.spec_from_file_location('audit_fsbl', MODULE_PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def encode_words(*words):
    return struct.pack('<' + 'I' * len(words), *words)


class FsblTableDecodeTests(unittest.TestCase):
    def test_existing_decoded_catalog_counts_without_reparsing_fsbl(self):
        # POSIX closeout reuses the sealed prior audit; no 21-table replay.
        catalog = json.loads(MODULE_PATH.with_name('decoded-tables.json').read_bytes())
        tables = [table for group in catalog['groups'] for table in group['tables']]
        self.assertEqual(len(tables), 21)
        self.assertEqual(catalog['decoded_operation_count'], 675)
        self.assertTrue(all(table['byte_length_including_exit'] > 4 for table in tables))
        self.assertEqual(catalog['target_address_audit']['0xF8009000']['hit_count'], 0)
        self.assertEqual(catalog['target_address_audit']['0xF8009014']['hit_count'], 0)
        self.assertEqual(catalog['target_address_audit']['0xF8000240']['hit_count'], 3)

    def test_existing_post_config_candidates_have_equal_operation_signatures(self):
        catalog = json.loads(MODULE_PATH.with_name('decoded-tables.json').read_bytes())
        post_group = catalog['groups'][0]['tables']
        self.assertEqual(len(post_group), 3)
        signature = lambda table: [(op['opcode'], tuple(op['args'])) for op in table['operations']]
        self.assertEqual(signature(post_group[0]), signature(post_group[1]))
        self.assertEqual(signature(post_group[1]), signature(post_group[2]))

    def test_decodes_complete_write_and_maskwrite_table(self):
        raw = encode_words(
            0x22, 0xF8000008, 0x0000DF0D,
            0x33, 0xF8000900, 0x0000000F, 0x0000000F,
            0x33, 0xF8000240, 0xFFFFFFFF, 0x00000000,
            0x22, 0xF8000004, 0x0000767B,
            0x00,
        )
        self.assertEqual(audit.decode_table(raw, 0), [
            {'kind': 'WRITE', 'opcode': 2, 'address': 0xF8000008, 'value': 0x0000DF0D},
            {'kind': 'MASKWRITE', 'opcode': 3, 'address': 0xF8000900, 'mask': 0x0000000F, 'value': 0x0000000F},
            {'kind': 'MASKWRITE', 'opcode': 3, 'address': 0xF8000240, 'mask': 0xFFFFFFFF, 'value': 0x00000000},
            {'kind': 'WRITE', 'opcode': 2, 'address': 0xF8000004, 'value': 0x0000767B},
        ])

    def test_rejects_unknown_opcode_instead_of_guessing(self):
        raw = encode_words(0x99, 0xF8000000, 0xFFFFFFFF, 0x0)
        with self.assertRaisesRegex(ValueError, 'opcode'):
            audit.decode_table(raw, 0)

    def test_rejects_truncated_operation_arguments(self):
        raw = encode_words(0x33, 0xF8000900, 0x0000000F)
        with self.assertRaisesRegex(ValueError, 'truncated'):
            audit.decode_table(raw, 0)

    def test_requires_terminating_exit(self):
        raw = encode_words(0x22, 0xF8000008, 0x0000DF0D)
        with self.assertRaisesRegex(ValueError, 'EXIT'):
            audit.decode_table(raw, 0)

    def test_decodes_write_zero_maskpoll_and_delay(self):
        raw = encode_words(
            0x11, 0xF8000004,
            0x42, 0xF8F00200, 0x00000001,
            0x52, 0xF8F00200, 0x00000010,
            0x00,
        )
        self.assertEqual(audit.decode_table(raw), [
            {'kind': 'WRITE_ZERO', 'opcode': 1, 'address': 0xF8000004, 'value': 0},
            {'kind': 'MASKPOLL', 'opcode': 4, 'address': 0xF8F00200, 'mask': 1},
            {'kind': 'DELAY', 'opcode': 5, 'timer_address': 0xF8F00200, 'delay_count': 0x10},
        ])

    def test_rejects_opcode_argument_count_mismatch(self):
        raw = encode_words(0x21, 0xF8000000, 0x0)
        with self.assertRaisesRegex(ValueError, 'expected 2'):
            audit.decode_table(raw)

    def test_rejects_unaligned_offset(self):
        with self.assertRaisesRegex(ValueError, 'word boundary'):
            audit.decode_table(encode_words(0), 1)

    def test_fdt_cell_zero_is_not_misread_as_empty_strings(self):
        self.assertEqual(audit._format_property('#size-cells', b'\x00\x00\x00\x01'),
                         '#size-cells = <0x00000001>;')

    def test_fdt_string_list_remains_a_string_list(self):
        self.assertEqual(audit._format_property('compatible', b'xlnx,zynq\x00vendor,soc\x00'),
                         'compatible = "xlnx,zynq", "vendor,soc";')

    def test_parses_complete_partition_dtb_tree(self):
        root_path = MODULE_PATH.resolve().parents[4]
        raw = (root_path / 'data/results/wo0015-boot-provenance-20261016/extracted/partition-02.raw.bin').read_bytes()
        tree, reservations, metadata = audit.parse_fdt(raw)
        nodes, properties = audit._node_counts(tree)
        self.assertEqual(metadata['total_size'], len(raw) - 2)
        self.assertEqual((nodes, properties), (98, 478))
        self.assertEqual(reservations, [])
        compatible = next(p['raw'] for p in tree['properties'] if p['name'] == 'compatible')
        self.assertEqual(compatible, b'xlnx,zynq-7000\x00')
        relevant = audit.relevant_dtb_nodes(tree)
        by_path = {n['path']: n for n in relevant['nodes']}
        self.assertIn('/fpga-region', by_path)
        self.assertEqual(by_path['/fpga-region']['fpga_mgr_resolves_to'], '/axi/devcfg@f8007000')
        self.assertIn('/axi/slcr@f8000000', by_path)
        self.assertEqual(relevant['afi_name_property_hits'], [])


if __name__ == '__main__':
    unittest.main()
