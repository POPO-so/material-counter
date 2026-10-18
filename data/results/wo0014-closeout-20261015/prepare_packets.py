"""Reproduce four consecutive H3 packets and cross-check saved pixel Golden."""
import hashlib
import json
import struct
from pathlib import Path
from PIL import Image

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
manifest = json.loads((ROOT / 'data/results/wo0014-offline-20261014/vectors/manifest.json').read_text())
sources = manifest['h3']['source_pngs'][:4]
rows = []
with (ROOT / 'data/results/wo0014-offline-20261014/vectors/h3_expected_th128.bin').open('rb') as saved:
    for frame_id, source in enumerate(sources):
        png = ROOT / source['path']
        assert hashlib.sha256(png.read_bytes()).hexdigest() == source['sha256']
        assert source['frame_id'] == frame_id
        with Image.open(png) as image:
            assert image.size == (320, 240)
            rgb = image.convert('RGB').tobytes()
        packet = struct.pack('<4I', 0x574f3134, (240 << 16) | 320, len(rgb), frame_id)
        packet += rgb + struct.pack('<4I', 0x454e4434, frame_id, sum(rgb) & 0xffffffff, 0xc0dec014)
        expected = bytearray()
        for pixel in range(76800):
            r, g, b = rgb[pixel*3:pixel*3+3]
            binary = 255 if (77*r + 150*g + 29*b)//256 >= 128 else 0
            expected += struct.pack('<H', binary | (0x200 if pixel == 0 else 0) | (0x100 if pixel % 320 == 319 else 0))
        assert saved.read(len(expected)) == expected
        assert sum(expected[0::2]) == source['binary_checksum']
        assert len(packet) == 230432
        name = f'frame-{frame_id}.bin'
        (OUT / name).write_bytes(packet)
        rows.append({'file': name, 'frame_id': frame_id, 'source_png': source['path'],
                     'source_sha256': source['sha256'], 'bytes': len(packet),
                     'sha256': hashlib.sha256(packet).hexdigest(),
                     'input_byte_checksum': sum(packet) & 0xffffffff,
                     'payload_checksum': sum(rgb) & 0xffffffff,
                     'binary_checksum': sum(expected[0::2]),
                     'foreground_pixels': sum(expected[0::2])//255,
                     'golden_sha256': hashlib.sha256(expected).hexdigest(),
                     'pixels': 76800, 'sofs': 1, 'eols': 240})
assert rows[0]['sha256'] == 'cacf35237e6b4e79de2940a55ad414565fb9eadd581dfe8251ef09f4909f9dae'
(OUT / 'packets.json').write_text(json.dumps(rows, indent=2) + '\n')
print(json.dumps(rows, indent=2))
