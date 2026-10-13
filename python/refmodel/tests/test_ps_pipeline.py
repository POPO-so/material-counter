"""Image-level supplements absent from H3's global-drop-only generator."""
from pathlib import Path
import math
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from refmodel.ps_reference.regions import extract_regions
from refmodel.ps_reference.tracking import Tracker, TrackerConfig


def picture(points):
    gray = np.full((120, 200), 24, dtype=np.uint8)
    for x, y in points:
        left, top = int(round(x - 3)), int(round(y - 3))
        gray[top:top + 7, left:left + 7] = 220
    return gray


@pytest.mark.parametrize('speed', [0.5, 1.5, 3.0, 5.0])
def test_image_speed_gap_and_repeated_downstream_observations(speed):
    t = Tracker(TrackerConfig(line_x=100))
    for fid in range(math.ceil(60 / speed) + 1):
        points = [] if fid in (3, 4) else [(70 + fid * speed, 50)]
        regions = extract_regions(picture(points))
        assert all(not hasattr(r, 'object_id') for r in regions)
        t.update(fid, regions)
    assert t.count == 1
    assert len(t.tracks) == 1


@pytest.mark.parametrize('spacing', [10, 16, 24])
def test_image_target_spacing_preserves_two_independent_counts(spacing):
    t = Tracker(TrackerConfig(line_x=100))
    for fid in range(35):
        points = [(60 + 2 * fid, 50), (60 + spacing + 2 * fid, 50)]
        t.update(fid, extract_regions(picture(points)))
    assert t.count == 2


def test_single_object_disappears_while_another_remains():
    t = Tracker(TrackerConfig(line_x=100))
    for fid in range(25):
        points = [(70 + 2 * fid, 80)]
        if fid not in (14, 15):
            points.append((70 + 2 * fid, 30))
        t.update(fid, extract_regions(picture(points)))
    assert t.count == 2
    assert len(t.tracks) == 2


def test_image_long_occlusion_across_line_is_explicit_known_miss():
    t = Tracker(TrackerConfig(line_x=100))
    for fid in range(28):
        points = [] if 10 <= fid <= 20 else [(70 + 2 * fid, 50)]
        t.update(fid, extract_regions(picture(points)))
    assert t.count == 0  # Physical target crossed once during missing observations.
    assert t.tracks[1].state == 'finished'
    assert t.tracks[2].born_counted
