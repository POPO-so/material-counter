import unittest
from golden_analysis import analyze

class GoldenTests(unittest.TestCase):
    def test_zero_first_word_explains_offset_four(self):
        r = analyze(bytes(16))
        self.assertEqual(r['first_word_u32_le'], 0)
        self.assertEqual(r['even_zero_odd_a5']['first_mismatch'], 4)
        self.assertEqual(r['even_zero_odd_a5']['mismatch_bytes'], 8)

    def test_foreground_in_first_word_changes_first_difference(self):
        r = analyze(bytes([255, 0, 0, 0]) + bytes(12))
        self.assertEqual(r['even_zero_odd_a5']['first_mismatch'], 0)

    def test_parity_counts_use_four_byte_words(self):
        r = analyze(bytes([255]*4 + [0]*4 + [0]*4 + [255]*4))
        self.assertEqual(r['foreground_bytes_by_word_parity'], {'even': 4, 'odd': 4})
        self.assertEqual(r['even_zero_odd_a5']['mismatch_bytes'], 12)

    def test_non_parity_counterexample_same_summary(self):
        r = analyze(bytes(32))
        self.assertEqual(r['even_zero_odd_a5']['mismatch_bytes'], r['contiguous_xor_counterexample']['mismatch_bytes'])
        self.assertEqual(r['even_zero_odd_a5']['first_mismatch'], r['contiguous_xor_counterexample']['first_mismatch'])
        self.assertTrue(r['distinct_counterexamples'])

    def test_reject_unaligned(self):
        with self.assertRaises(ValueError): analyze(bytes(17))

    def test_reject_non_binary(self):
        with self.assertRaises(ValueError): analyze(bytes([3]*16))

    def test_reject_short(self):
        with self.assertRaises(ValueError): analyze(bytes(4))

if __name__ == '__main__': unittest.main()
