from pathlib import Path
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


def test_observability_reports_component_rejections_without_changing_detections(library):
    mask=np.zeros((400,640),np.uint8)
    mask[60:80,100:120]=255;mask[60:64,300:304]=255;mask[300:310,400:600]=255
    settings=dict(min_area=48,max_area=3600,max_bbox_width=170,max_bbox_height=170)
    quiet=RleCounter(library=library,**settings).update_rle(frame(mask,1))
    observed=RleCounter(library=library,observability=True,**settings).update_rle(frame(mask,1))
    assert observed.detections==quiet.detections
    assert observed.total_count==quiet.total_count==0
    assert quiet.diagnostics=={}
    reasons={item["reason"] for item in observed.diagnostics["rejected_components"]}
    assert reasons=={"below_min_area","bbox_width"}


def test_observability_reports_roi_clipped_components_and_stage_timings(library):
    mask=np.zeros((400,640),np.uint8);mask[50:60,100:120]=255;mask[290:300,200:220]=255
    counter=RleCounter(library=library,observability=True,roi_top=50,roi_bottom=300)
    result=counter.update_rle(frame(mask,1,roi=(0,50,640,300)))
    edges={tuple(item["roi_clipped_edges"])
           for item in result.diagnostics["boundary_observations"]}
    assert edges=={("top",),("bottom",)}
    assert set(result.stage_timings_ms)=={"t_ccl","t_split","t_track","split_calls"}
    assert set(result.roi_subtimings_ms)=={"rle_regions_ms","track_ms"}
    assert all(result.stage_timings_ms[name]>=0 for name in ("t_ccl","t_split","t_track"))


def test_observability_records_split_attempts_on_the_rle_path(library,monkeypatch):
    import scripts.rle.counter as module
    mask=np.zeros((400,640),np.uint8);mask[100:120,200:240]=255
    monkeypatch.setattr(module,"split_touching_component",lambda cropped,**kwargs:[cropped])
    counter=RleCounter(library=library,observability=True,guarded_merge=True,
                       single_intact_area=100,max_split_area=900,max_area=1000)
    result=counter.update_rle(frame(mask))
    attempt=result.diagnostics["split_attempts"][0]
    assert attempt["split_attempted"] is True and attempt["accepted"] is False
    assert attempt["split_rejected_reason"]=="no_valid_partition"
    assert attempt["candidate_piece_count"]==1
    assert result.stage_timings_ms["split_calls"]==1
    assert len(result.detections)==1


def test_observability_reports_lost_tracks_on_the_rle_path(library):
    counter=RleCounter(library=library,observability=True)
    mask=np.zeros((400,640),np.uint8);mask[100:120,100:120]=255
    counter.update_rle(frame(mask,1))
    empty=np.zeros((400,640),np.uint8)
    lost=[]
    for number in (2,3,4,5):
        result=counter.update_rle(frame(empty,number))
        lost.extend(result.diagnostics["lost_tracks"])
    assert len(lost)==1 and lost[0]["lost_reason"]=="missed_frame_budget_exceeded"
    assert result.diagnostics["associations"]==[]
