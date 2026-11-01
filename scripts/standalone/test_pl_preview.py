from types import SimpleNamespace
import pytest
from scripts.standalone.pl_preview import (PlPreview, PBO1_SIGNATURE, ButtonEventStatus,
                                             BTN1_SIGNATURE)

class MMIO:
    def __init__(self):
        self.reg={0:PBO1_SIGNATURE,4:(400<<16)|640,8:9,0x18:7,
                  0x80:BTN1_SIGNATURE,0x84:5,0x88:10,0x8c:2}
        self.writes=[]
    def read(self,a):return self.reg.get(a,0)
    def write(self,a,v):self.writes.append((a,v));self.reg[a]=v

def packet(fid=7,valid=True):return SimpleNamespace(frame_id=fid,valid=valid)

def test_capability_rejection_before_writes():
    m=MMIO();m.reg[0]=0
    with pytest.raises(ValueError,match="capability"):PlPreview(m)
    assert not m.writes

def test_matching_completed_frame_updates_boxes_and_accepts_last():
    m=MMIO();p=PlPreview(m)
    result=SimpleNamespace(detections=[SimpleNamespace(x_min=10,x_max=20,y_min=30,y_max=40)])
    assert p.submit(packet(),result,line_x=320)
    assert (0x100,10|(20<<16)) in m.writes
    assert (0x104,30|(40<<16)) in m.writes
    assert m.writes[-3:]==[(0x40,1),(0x20,7),(0x24,1)]

def test_busy_missing_or_invalid_capture_is_not_accepted():
    for status,fid,valid in [(0,7,True),(5,7,True),(9,8,True),(9,7,False)]:
        m=MMIO();m.reg[8]=status;p=PlPreview(m)
        assert not p.submit(packet(fid,valid),None,line_x=320)
        assert not m.writes

def test_bounds_checked_before_metadata_mutation():
    m=MMIO();p=PlPreview(m)
    bad=SimpleNamespace(detections=[SimpleNamespace(x_min=-1,x_max=20,y_min=30,y_max=40)])
    with pytest.raises(ValueError):p.submit(packet(),bad,line_x=320)
    assert not m.writes

def test_bounded_annotations_and_queue_stats():
    m=MMIO();p=PlPreview(m)
    d=SimpleNamespace(x_min=1,x_max=2,y_min=3,y_max=4)
    assert p.submit(packet(),SimpleNamespace(detections=[d]*17),line_x=320)
    assert (0x38,16) in m.writes
    assert p.stats()['annotations_truncated']==1


def test_button_status_snapshot_and_clear_only_observed_sticky_bits():
    m=MMIO(); status=ButtonEventStatus(m)
    snapshot=status.snapshot(clear=True)
    assert (snapshot.stable,snapshot.events,snapshot.fault)==(5,10,2)
    assert m.writes==[(0x88,10),(0x8c,2)]


def test_button_status_signature_required_and_snapshot_can_be_read_only():
    m=MMIO(); m.reg[0x80]=0
    with pytest.raises(ValueError,match="signature"):
        ButtonEventStatus(m)
    m=MMIO(); status=ButtonEventStatus(m)
    snapshot=status.snapshot()
    assert snapshot.stable==5 and snapshot.events==10 and snapshot.fault==2
    assert not m.writes
