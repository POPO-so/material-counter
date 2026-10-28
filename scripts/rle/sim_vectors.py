"""Generate full-resolution independent golden vectors for XSim."""
from __future__ import annotations
import argparse
from pathlib import Path
import struct
import sys
import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.rle.protocol import encode_gray, encode_mask, decode_packet


def write_vectors(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    gray = np.zeros((400, 640), np.uint8)
    gray[2:15, 0:25] = 200
    gray[180:220, 250:300] = 160
    gray[190:210, 260:290] = 0
    gray[398:, 620:] = 255
    gray[70, 71] = 125
    gray[71, 72] = 125
    gray[100:110, 400:410] = 128
    cases = []
    for name, mode, offset, roi in (
        ("baseline", 0, 0, (0, 0, 640, 400)),
        ("roi", 0, 10, (10, 5, 630, 399)),
        ("erode", 1, 0, (0, 0, 640, 400)),
        ("dilate", 2, 0, (10, 5, 630, 399)),
        ("empty", 0, -255, (0, 0, 640, 400)),
        ("overflow", 0, 0, (0, 0, 640, 400)),
        ("bad_checksum", 0, 0, (0, 0, 640, 400)),
        ("bad_header", 0, 0, (0, 0, 640, 400)),
    ):
        source = gray.copy()
        if name == "overflow":
            source[:, ::2] = 255
            source[:, 1::2] = 0
        mapped = np.clip(source.astype(np.int16) + offset, 0, 255)
        mask = np.where(mapped >= 128, 255, 0).astype(np.uint8)
        x0, y0, x1, y1 = roi
        inside = np.zeros_like(mask)
        inside[y0:y1, x0:x1] = mask[y0:y1, x0:x1]
        if mode:
            operation = cv2.erode if mode == 1 else cv2.dilate
            inside = operation(inside, np.ones((3, 3), np.uint8),
                               borderType=cv2.BORDER_CONSTANT, borderValue=0)
        mask.fill(0)
        mask[y0:y1, x0:x1] = inside[y0:y1, x0:x1]
        tx = bytearray(encode_gray(source, frame_id=17))
        packet = bytearray(encode_mask(mask, frame_id=17, params_version=3,
            roi=roi, threshold=128, intensity_offset=offset, morph_mode=mode))
        if name == "bad_checksum":
            tx[-8] ^= 1
            struct.pack_into("<I", packet, len(packet) - 64 + 6 * 4, 2)
            struct.pack_into("<I", packet, len(packet) - 64 + 9 * 4, 5)
        if name == "bad_header":
            tx[0] ^= 1
            packet=bytearray(encode_mask(np.zeros_like(mask),frame_id=17,params_version=3,
                roi=roi,threshold=128,intensity_offset=offset,morph_mode=mode))
            struct.pack_into("<I",packet,len(packet)-64+6*4,2)
            struct.pack_into("<I",packet,len(packet)-64+9*4,1)
            struct.pack_into("<I",packet,len(packet)-64+10*4,0)
        decode_packet(packet, expected_frame_id=17, expected_params_version=3)
        for suffix, data in (("input", tx), ("expected", packet)):
            words = np.frombuffer(data, dtype="<u4")
            (output / f"{name}-{suffix}.hex").write_text(
                "".join(f"{int(word):08x}\n" for word in words), encoding="ascii")
        cases.append(f"{name} {len(packet)//4} {mode} {offset} {x0} {y0} {x1} {y1}")
    (output / "cases.txt").write_text("\n".join(cases)+"\n", encoding="ascii")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    write_vectors(parser.parse_args().output)
