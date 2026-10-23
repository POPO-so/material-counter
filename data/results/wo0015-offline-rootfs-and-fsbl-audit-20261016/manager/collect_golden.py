"""Read only historical Golden and source snippets. No target module imports."""
import hashlib
import json
import csv
from pathlib import Path
from golden_analysis import analyze

OUT = Path(__file__).resolve().parents[1]
ROOT = OUT.parents[2]
BUNDLE = ROOT / 'data/results/wo0015-retry3-two-tier-bundle-20261015'
manifest = json.loads((BUNDLE / 'run-manifest.json').read_bytes())
frame = manifest['frames'][0]
path = BUNDLE / frame['binary_path']
data = path.read_bytes()
assert hashlib.sha256(data).hexdigest() == frame['binary_sha256']
result = analyze(data)
assert result['nbytes'] == 76800
assert result['golden_sha256'] == '174164ae33086f8606b83113f43ebd5a52470f8640bde1c489e51e76f411a60e'
assert result['first_word_u32_le'] == 0
assert result['even_zero_odd_a5']['first_mismatch'] == 4
assert result['even_zero_odd_a5']['mismatch_bytes'] == 39520
assert result['contiguous_xor_counterexample']['mismatch_bytes'] == 39520
assert result['contiguous_xor_counterexample']['first_mismatch'] == 4
result['golden_path'] = str(path)
result['manifest_path'] = str(BUNDLE / 'run-manifest.json')
result['reasoning'] = {
    'even_zero_odd_a5': 'Even-word forced-zero mismatches 1120 foreground bytes; every odd-word A5 byte differs from Binary8 (38400); total 39520.',
    'first_difference': 'Golden bytes 0..3 are 00. Hypothetical zero even word matches; byte4 Golden00 versus hypothetical A5 is first mismatch.',
    'poison_only': 'An all-A5 untouched RX differs from Binary8 at all 76800 bytes, starting byte0. Offset4 implies bytes0..3 matched Golden00 after synchronization, not that a specific hardware write occurred.',
    'counterexample': 'Golden copied then XOR1 at contiguous offsets4..39523 has same count and first offset, distinct bytes and no odd/even poison pattern.',
    'limits': 'Historical log gives mismatch count/first offset only; actual RX bytes, last writer and cache/coherence state UNKNOWN.'}
(OUT / 'golden-and-cache-audit.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
with (OUT / 'manager/golden-all-19200-words.csv').open('w', newline='', encoding='utf-8') as stream:
    writer = csv.writer(stream)
    writer.writerow(['word_index', 'byte_offset', 'parity', 'bytes_hex', 'u32_le_hex'])
    for offset in range(0, len(data), 4):
        writer.writerow([offset // 4, offset, 'odd' if (offset // 4) % 2 else 'even',
                         data[offset:offset+4].hex(), f'0x{int.from_bytes(data[offset:offset+4], "little"):08x}'])
historical = ROOT / 'data/results/wo0015-runonly-resume-retry2-20261015/remote-evidence/board_once_watermark.py'
lines = historical.read_text(encoding='utf-8').splitlines()
snippets = {'path': str(historical), 'sha256': hashlib.sha256(historical.read_bytes()).hexdigest(),
            'excerpts': [{'line': i, 'text': lines[i-1]} for a,b in [(430,451),(507,522),(919,927),(944,971)] for i in range(a,b+1)]}
(OUT / 'manager/historical-cache-call-sites.json').write_text(json.dumps(snippets, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'golden_first_word': result['first_word_bytes_hex'], 'counts': result['byte_histogram'],
                  'parity_fg': result['foreground_bytes_by_word_parity'], 'first_difference': 4, 'mismatches': 39520}))
