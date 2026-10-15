"""Compare accepted RTL bytes to golden and reconstruct H3 output PNGs."""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]

def check(vectors, run):
    manifest = json.loads((vectors / 'manifest.json').read_text(encoding='utf-8'))
    expected = bytes(int(line, 16) & 255 for line in (vectors / 'expected_gray.hex').read_text().splitlines())
    results = []
    for mode in (0, 1):
        actual = bytes(int(line, 16) for line in (run / f'output-mode-{mode}.hex').read_text().splitlines())
        if len(actual) != manifest['beats'] or actual != expected:
            raise AssertionError(f'mode={mode}: output length/value mismatch')
        for i, source in enumerate(manifest['h3_frames']):
            p = ROOT / source['path']
            if hashlib.sha256(p.read_bytes()).hexdigest() != source['sha256']:
                raise AssertionError('Source changed since vector generation: ' + str(p))
            frame = actual[512+i*76800:512+(i+1)*76800]
            with Image.open(p) as original:
                if frame != original.tobytes():
                    raise AssertionError('RTL differs from H3 frame: ' + str(p))
            Image.frombytes('L', (320, 240), frame).save(run / f'rtl-mode-{mode}-frame-{i}.png')
        results.append({'mode': mode, 'beats': len(actual), 'mismatches': 0,
                        'h3_frames_equal': 3, 'output_sha256': hashlib.sha256(actual).hexdigest()})
    (run / 'pixel-comparison.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print('PIXEL_COMPARISON_PASS ' + json.dumps(results))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--vectors', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    check(args.vectors.resolve(), args.run.resolve())
