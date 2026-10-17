"""Prepare one formal RGB packet and independent output golden from a recorded H3 PNG."""
import hashlib
import json
import struct
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/results/wo0014-hw-verification-20261014"
OUT.mkdir(parents=True, exist_ok=True)
manifest = json.loads((ROOT / "data/results/wo0014-offline-20261014/vectors/manifest.json").read_text())
source = manifest["h3"]["source_pngs"][0]
png = ROOT / source["path"]
assert hashlib.sha256(png.read_bytes()).hexdigest() == source["sha256"]
with Image.open(png) as image:
    assert image.size == (320, 240)
    rgb = image.convert("RGB").tobytes()
frame_id = 0
header = struct.pack("<4I", 0x574f3134, (240 << 16) | 320, len(rgb), frame_id)
trailer = struct.pack("<4I", 0x454e4434, frame_id, sum(rgb) & 0xffffffff, 0xc0dec014)
packet = header + rgb + trailer
assert len(packet) == 230432
(OUT / "rgb_packet.bin").write_bytes(packet)
beats = bytearray()
for offset in range(0, len(packet), 4):
    beats += packet[offset:offset + 4] + bytes([0x1f if offset == len(packet)-4 else 0x0f])
(OUT / "first-frame-beats.bin").write_bytes(beats)
expected = bytearray()
for pixel in range(76800):
    r, g, b = rgb[3*pixel:3*pixel+3]
    binary = 255 if ((77*r + 150*g + 29*b) >> 8) >= 128 else 0
    word = binary | (0x200 if pixel == 0 else 0) | (0x100 if pixel % 320 == 319 else 0)
    expected += struct.pack("<H", word)
(OUT / "first-frame-golden.bin").write_bytes(expected)
saved = ROOT / "data/results/wo0014-offline-20261014/vectors/h3_expected_th128.bin"
with saved.open("rb") as stream:
    assert stream.read(len(expected)) == expected
report = {
    "source_png": str(png), "source_png_sha256": source["sha256"], "frame_id": frame_id,
    "packet_bytes": len(packet), "input_beats": len(beats)//5, "output_pixels": len(expected)//2,
    "packet_sha256": hashlib.sha256(packet).hexdigest(),
    "golden_sha256": hashlib.sha256(expected).hexdigest(),
    "payload_checksum": sum(rgb) & 0xffffffff, "input_byte_checksum": sum(packet) & 0xffffffff,
    "binary_checksum": sum(expected[0::2]), "tlast_count": 1,
}
(OUT / "packet-manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
