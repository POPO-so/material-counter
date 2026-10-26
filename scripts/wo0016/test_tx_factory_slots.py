"""Slot and scratch reuse checks for one serialized TX encoder owner."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "wo0015"))

import lighting_profile as profile
import packet_encoder


def test_factory_reuses_scratch_with_two_independent_tx_slots(monkeypatch):
    encode, encode_into = profile.make_packet_encoders(threshold=100)
    gray_a = np.random.default_rng(801).integers(0, 256, (240, 320), dtype=np.uint8)
    gray_b = np.random.default_rng(802).integers(0, 256, (240, 320), dtype=np.uint8)
    original_a, original_b = gray_a.copy(), gray_b.copy()
    slots = [np.empty(packet_encoder.TX_BYTES, dtype=np.uint8) for _ in range(2)]
    pointers = []
    real_lut = profile.cv2.LUT
    real_empty = np.empty

    def capture_lut(source, lut, *, dst=None):
        pointers.append(dst.__array_interface__["data"][0])
        return real_lut(source, lut, dst=dst)

    def reject_full_frame_empty(shape, *args, **kwargs):
        try:
            shape_tuple = tuple(shape)
        except TypeError:
            shape_tuple = (int(shape),)
        if shape_tuple == (240, 320):
            raise AssertionError("encode_into allocated a per-frame Gray8 image")
        return real_empty(shape, *args, **kwargs)

    monkeypatch.setattr(profile.cv2, "LUT", capture_lut)
    monkeypatch.setattr(np, "empty", reject_full_frame_empty)

    encode_into(gray_a, slots[0], frame_id=801)
    first_slot_after_call = slots[0].copy()
    encode_into(gray_b, slots[1], frame_id=802)

    assert len(pointers) == 2
    assert pointers[0] == pointers[1]
    assert slots[0].tobytes() == first_slot_after_call.tobytes()
    assert slots[0].tobytes() == encode(gray_a, frame_id=801).tobytes()
    assert slots[1].tobytes() == encode(gray_b, frame_id=802).tobytes()
    assert np.array_equal(gray_a, original_a)
    assert np.array_equal(gray_b, original_b)
