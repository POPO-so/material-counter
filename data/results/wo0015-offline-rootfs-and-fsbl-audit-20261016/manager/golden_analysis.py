"""Offline byte-distribution evidence; never represents reconstructed board RX."""
import hashlib

def analyze(golden):
    if len(golden) < 8 or len(golden) % 4 or set(golden) - {0, 255}:
        raise ValueError('Expected word-aligned Binary8, at least two words')
    def summary(raw):
        diffs = [i for i, (a, b) in enumerate(zip(raw, golden)) if a != b]
        return {'sha256': hashlib.sha256(raw).hexdigest(), 'mismatch_bytes': len(diffs),
                'first_mismatch': diffs[0] if diffs else None}
    hypothesis = bytes(0 if (i // 4) % 2 == 0 else 165 for i in range(len(golden)))
    h = summary(hypothesis)
    start = h['first_mismatch']
    alternate = bytearray(golden)
    for i in range(start, start + h['mismatch_bytes']):
        alternate[i] ^= 1
    return {'evidence_type': 'LOCAL_GOLDEN_AND_SYNTHETIC_COUNTEREXAMPLES_NOT_BOARD_RX',
            'golden_sha256': hashlib.sha256(golden).hexdigest(), 'nbytes': len(golden),
            'first_64_bytes_hex': golden[:64].hex(),
            'first_word_bytes_hex': golden[:4].hex(),
            'first_word_u32_le': int.from_bytes(golden[:4], 'little'),
            'byte_histogram': {'0x00': golden.count(0), '0xff': golden.count(255)},
            'foreground_bytes_by_word_parity': {label: sum(v == 255 for i, v in enumerate(golden) if (i // 4) % 2 == p)
                                               for p, label in enumerate(('even', 'odd'))},
            'word_histogram_by_parity': {label: {f'0x{v:08x}': sum(int.from_bytes(golden[i:i+4], 'little') == v
                                                   for i in range(p * 4, len(golden), 8))
                                                 for v in sorted({int.from_bytes(golden[i:i+4], 'little')
                                                                  for i in range(p * 4, len(golden), 8)})}
                                         for p, label in enumerate(('even', 'odd'))},
            'even_zero_odd_a5': h, 'contiguous_xor_counterexample': summary(alternate),
            'distinct_counterexamples': hypothesis != alternate,
            'actual_historical_rx_pattern': 'UNKNOWN; raw historical RX not persisted'}
