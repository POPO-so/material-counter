from pathlib import Path
import cv2
import sys
import numpy as np
import pytest

from scripts.rle.components import build_native
from scripts.rle.protocol import encode_mask, decode_packet
from scripts.rle.counter import RleCounter


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    return build_native(tmp_path_factory.mktemp("rle-counter") / ("rle.dll" if sys.platform=="win32" else "rle.so"))


def frame(mask, number=0, **kwargs):
    return decode_packet(encode_mask(mask, frame_id=number, **kwargs))


def test_regions_match_pixel_counter_without_full_mask(library, monkeypatch):
    from scripts.wo0016.stream_counter import MaskCounter
    mask=np.zeros((400,640),np.uint8)
    mask[20:40,30:50]=255;mask[90:110,80:100]=255;mask[95:105,85:95]=0
    settings=dict(width=640,height=400,line_y=200,line_x=320,roi_mode="full",min_area=12,max_area=3600,
                  max_bbox_width=170,max_bbox_height=170,component_cluster_dx=0)
    expected=MaskCounter(**settings).update(0,mask)
    def forbidden(*args,**kwargs):raise AssertionError("ordinary path reconstructed a mask")
    monkeypatch.setattr("scripts.rle.counter.local_mask",forbidden)
    result=RleCounter(library=library,**settings).update_rle(frame(mask))
    assert result.detections==expected.detections
    assert result.row_projection==expected.row_projection
    assert result.column_projection==expected.column_projection


def test_tracking_crosses_line_once(library):
    counter=RleCounter(library=library,max_step=50,max_area=3600)
    for i,y in enumerate((160,175,190,205,220,235)):
        mask=np.zeros((400,640),np.uint8);mask[y:y+10,100:110]=255
        result=counter.update_rle(frame(mask,i))
    assert result.total_count==1


def test_overflow_does_not_update_tracker_and_duplicate_rejected(library):
    mask=np.zeros((400,640),np.uint8);mask[:,::2]=255
    counter=RleCounter(library=library)
    bad=frame(mask,3,max_runs=2)
    assert counter.update_rle(bad) is None
    assert counter.total_count==0 and counter.last_frame_id is None
    with pytest.raises(ValueError,match="increas"):
        counter.update_rle(bad)


def test_mismatched_roi_rejected(library):
    counter=RleCounter(library=library,roi_top=50,roi_bottom=300)
    with pytest.raises(ValueError,match="ROI"):
        counter.update_rle(frame(np.zeros((400,640),np.uint8)))


def test_complex_candidate_uses_only_local_mask(library,monkeypatch):
    import scripts.rle.counter as module
    mask=np.zeros((400,640),np.uint8);mask[100:110,200:220]=255
    shapes=[]
    original=module.local_mask
    def local(*args,**kwargs):
        out=original(*args,**kwargs);shapes.append(out.shape);return out
    monkeypatch.setattr(module,"local_mask",local)
    monkeypatch.setattr(module,"split_touching_component",lambda cropped,**kwargs: [cropped])
    counter=RleCounter(library=library,guarded_merge=True,single_intact_area=100,max_split_area=500,
                       max_area=1000,max_merge_area=500)
    result=counter.update_rle(frame(mask))
    assert shapes==[(10,20)] and len(result.detections)==1


def _ring_mask(centers, outer=24, inner=8):
    mask = np.zeros((400, 640), np.uint8)
    for cx, cy in centers:
        cv2.circle(mask, (cx, cy), outer // 2, 255, -1)
        cv2.circle(mask, (cx, cy), inner // 2, 0, -1)
    return mask


def _counting_calls(monkeypatch):
    import scripts.rle.counter as module
    calls = []
    original = module.split_touching_component
    def wrapped(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "split_touching_component", wrapped)
    return calls


def test_intact_single_ring_above_split_area_skips_full_split(library, monkeypatch):
    from scripts.standalone.count_controls import make_runtime_counter
    calls = _counting_calls(monkeypatch)
    counter = make_runtime_counter(library, profile="field-v1")
    result = counter.update_rle(frame(_ring_mask([(100, 200)])), capture_monotonic_ns=1)
    assert np.count_nonzero(_ring_mask([(100, 200)])) > 380
    assert len(result.detections) == 1
    assert calls == []


def test_touching_pair_still_runs_full_split(library, monkeypatch):
    from scripts.standalone.count_controls import make_runtime_counter
    calls = _counting_calls(monkeypatch)
    counter = make_runtime_counter(library, profile="field-v1")
    result = counter.update_rle(frame(_ring_mask([(100, 200), (121, 201)])), capture_monotonic_ns=1)
    assert len(result.detections) == 2
    assert calls == [1]
