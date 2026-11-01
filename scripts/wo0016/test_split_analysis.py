"""Equivalence tests for the pre-computed split analysis (no behaviour change)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from scripts.wo0016.split_touching import (analyze_split, count_enclosed_holes,
                                           has_projection_neck,
                                           split_touching_component)

FIXTURES = Path(__file__).resolve().parents[1] / "rle" / "fixtures"
CROSSING_JSON = FIXTURES / "crossing-masks-20261031.json"
CROSSING_NPZ = FIXTURES / "crossing-masks-20261031.npz"
M6_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lighting_b2_m6_touch_masks.npz"

PARAMS = dict(min_child_area=80, min_hole_area=30, min_hole_distance=10,
              hole_area_cap=16, recursive_item_area=None)


def _crossing_masks():
    data = json.loads(CROSSING_JSON.read_text(encoding="utf-8"))
    arrays = np.load(CROSSING_NPZ, allow_pickle=False)
    masks = []
    for name, sequence in data["sequences"].items():
        for item in sequence:
            masks.append((f"{name}-{item['frame_id']}", np.asarray(arrays[item["array"]]) != 0))
    for item in data["single_masks"]:
        masks.append((f"single-{item['frame_id']}-{item['array']}",
                      np.asarray(arrays[item["array"]]) != 0))
    return masks


def _m6_masks():
    arrays = np.load(M6_FIXTURE, allow_pickle=False)
    return [(f"m6-{frame_id}", np.asarray(mask) != 0)
            for frame_id, mask in zip(arrays["frame_ids"], arrays["masks"])]


MASKS = _crossing_masks() + _m6_masks()
ELIGIBLE = [entry for entry in MASKS if int(entry[1].sum()) >= 2 * PARAMS["min_child_area"]]


def _analysis(mask):
    return analyze_split(mask, min_child_area=PARAMS["min_child_area"],
                         min_hole_area=PARAMS["min_hole_area"],
                         hole_area_cap=PARAMS["hole_area_cap"])


@pytest.mark.parametrize("name,mask", ELIGIBLE)
def test_reused_analysis_matches_plain_split(name, mask):
    plain = split_touching_component(mask, assume_connected=True, **PARAMS)
    reused = split_touching_component(mask, assume_connected=True,
                                      analysis=_analysis(mask), **PARAMS)
    assert len(plain) == len(reused)
    for left, right in zip(plain, reused):
        assert np.array_equal(np.asarray(left) != 0, np.asarray(right) != 0)


@pytest.mark.parametrize("name,mask", ELIGIBLE)
def test_analysis_evidence_matches_old_gate(name, mask):
    expected = (count_enclosed_holes(mask, min_hole_area=PARAMS["min_hole_area"],
                                     hole_area_cap=PARAMS["hole_area_cap"]) in (2, 3)
                or has_projection_neck(mask, min_child_area=PARAMS["min_child_area"]))
    assert _analysis(mask).has_evidence is expected


def test_analysis_from_another_mask_is_ignored():
    first, second = ELIGIBLE[0][1], ELIGIBLE[-1][1]
    plain = split_touching_component(second, assume_connected=True, **PARAMS)
    reused = split_touching_component(second, assume_connected=True,
                                      analysis=_analysis(first), **PARAMS)
    assert len(plain) == len(reused)
    for left, right in zip(plain, reused):
        assert np.array_equal(np.asarray(left) != 0, np.asarray(right) != 0)


def test_invalid_analysis_type_is_rejected():
    with pytest.raises(TypeError, match="SplitAnalysis"):
        split_touching_component(ELIGIBLE[0][1], assume_connected=True,
                                 analysis=object(), **PARAMS)