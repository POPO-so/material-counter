"""Independent integer golden vectors from existing H3 PNGs (no GT identity)."""
import argparse
import hashlib
import json
from pathlib import Path
import random
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
FRAMES = [
    'backbone_slow_small_01/frames/frame_0000.png',
    'backbone_nominal_medium_02/frames/frame_0000.png',
    'projection_overlap_noncontact/frames/frame_0000.png',
]

def gray(rgb):
    """Unsigned RGB888 -> Gray8, truncate; weights sum to exactly 256."""
    red, green, blue = rgb
    return (77 * red + 150 * green + 29 * blue) // 256

def generate(out):
    out.mkdir(parents=True, exist_ok=False)
    rng = random.Random(20261012)
    colors = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0),
              (0, 0, 255), (1, 1, 1), (254, 254, 254), (1, 2, 3)]
    colors += [(v, v, v) for v in range(256)]
    colors += [tuple(rng.randrange(256) for _ in range(3)) for _ in range(512-len(colors))]
    vectors = [(rgb, int(i == 0), int(i % 32 == 31)) for i, rgb in enumerate(colors)]
    sources = []
    for rel in FRAMES:
        p = ROOT / 'data/synthetic/h3_full' / rel
        with Image.open(p) as im:
            if im.mode != 'L' or im.size != (320, 240):
                raise ValueError('Unexpected H3 format: ' + str(p))
            pixels = list(im.tobytes())
        vectors.extend(((v, v, v), int(i == 0), int(i % 320 == 319)) for i, v in enumerate(pixels))
        sources.append({'path': str(p.relative_to(ROOT)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
    with (out / 'vectors_rgb.hex').open('w', encoding='ascii') as source, (out / 'expected_gray.hex').open('w', encoding='ascii') as expected:
        for rgb, sof, eol in vectors:
            source.write(f'{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}\n')
            expected.write(f'{(sof << 9) | (eol << 8) | gray(rgb):03x}\n')
    manifest = {'beats': len(vectors), 'directed_color_beats': 512, 'h3_frames': sources,
                'formula': '(77*R+150*G+29*B)//256', 'channels': 'R[23:16], G[15:8], B[7:0]', 'seed': 20261012}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    generate(parser.parse_args().out)
