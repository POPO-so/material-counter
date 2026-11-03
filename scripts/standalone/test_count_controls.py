import sys

import numpy as np
import pytest

from scripts.rle.components import build_native
from scripts.rle.counter import RleCounter
from scripts.rle.protocol import encode_mask, decode_packet
from scripts.standalone.controls import ControlState
from scripts.standalone.count_controls import advance_count, make_runtime_counter


@pytest.fixture(scope='module')
def library(tmp_path_factory):
    return build_native(tmp_path_factory.mktemp('controls-native') / ('rle.dll' if sys.platform=='win32' else 'rle.so'))


def packet(index, y):
    mask=np.zeros((400,640),np.uint8);mask[y:y+10,100:110]=255
    return decode_packet(encode_mask(mask,frame_id=index))


def test_pause_tracks_crossing_but_resume_does_not_replay_event(library):
    counter=RleCounter(library=library,max_step=50)
    controls=ControlState()
    seen=[]
    for i,y in enumerate((160,175,190,205,220,235,245)):
        commands=('pause',) if i in (2,5) else ()
        result=advance_count(counter,controls,packet(i,y),commands,capture_monotonic_ns=(i+1)*100_000_000)
        seen.extend(result.events)
    assert counter.total_count==0 and not seen
    assert result.tracks and any(t['counted'] for t in result.tracks)


def test_clear_before_crossing_adds_only_new_boundary_event(library):
    counter=RleCounter(library=library,max_step=50)
    controls=ControlState()
    counter.total_count=8
    for i,y in enumerate((160,175,190,205,220,235)):
        result=advance_count(counter,controls,packet(i,y),('clear',) if i==3 else (),
                             capture_monotonic_ns=(i+1)*100_000_000)
    assert result.total_count==counter.total_count==1
    assert controls.clear_requests==1


def test_clear_keeps_track_dedup_and_display_commands_do_not_change_count(library):
    counter=RleCounter(library=library,max_step=50)
    controls=ControlState()
    for i,y in enumerate((160,175,190,205,220,235,245)):
        commands=('clear','background') if i==5 else ()
        result=advance_count(counter,controls,packet(i,y),commands,
                             capture_monotonic_ns=(i+1)*100_000_000)
    assert result.total_count==0
    assert any(t['counted'] for t in result.tracks)
    assert not controls.binary_background


def test_streaming_runtime_discards_finished_history_without_changing_events():
    from scripts.wo0016.stream_counter import Detection
    reference=RleCounter(library=None,count_axis='x',line_x=320,direction=1)
    live=make_runtime_counter(None)
    assert (live.count_axis,live.line_x,live.direction)==('x',320,1)
    most_tracks=0
    for i in range(1200):
        x=260+(i%12)*12
        detections=(Detection(x,x+19,180,195,320,x+9.5,187.5),)
        stamp=(i+1)*33_333_333
        expected=reference.update_detections(i,detections,capture_monotonic_ns=stamp)
        actual=live.update_detections(i,detections,capture_monotonic_ns=stamp)
        assert actual.events==expected.events and actual.total_count==expected.total_count
        most_tracks=max(most_tracks,len(actual.tracks))
    assert live.total_count==100 and most_tracks<=2


@pytest.mark.parametrize('positions,expected', [
    ((260,275,290,305,320,335,350),1),
    ((350,335,320,305,290,275,260),0),
])
def test_runtime_rle_packets_count_only_left_to_right_crossing(library,positions,expected):
    counter=make_runtime_counter(library)
    controls=ControlState()
    events=[]
    for index,x in enumerate(positions):
        mask=np.zeros((400,640),np.uint8)
        mask[180:196,x:x+20]=255
        frame=decode_packet(encode_mask(mask,frame_id=index))
        result=advance_count(counter,controls,frame,
            capture_monotonic_ns=(index+1)*33_333_333)
        events.extend(result.events)
    assert counter.invalid_frames==0
    assert counter.total_count==len(events)==expected


def test_advance_count_defaults_to_native_without_feature_reads(library):
    class Reader:
        def __init__(self): self.calls=0
        def read(self, frame): self.calls+=1; return ()
    counter=make_runtime_counter(library)
    controls=ControlState()
    reader=Reader()
    advance_count(counter,controls,packet(0,160),capture_monotonic_ns=1)
    assert reader.calls==0 and counter.last_backend=='native'


def test_advance_count_passes_validated_features_or_native_fallback(library):
    from scripts.rle.components import Component

    class Reader:
        def __init__(self, result): self.result=result; self.calls=[]
        def read(self, frame): self.calls.append(frame.frame_id); return self.result

    frame=packet(0,160)
    component=Component(100,160,109,169,100,104.5,164.5)
    reader=Reader((component,))
    counter=make_runtime_counter(library)
    advance_count(counter,ControlState(),frame,capture_monotonic_ns=1,
                  region_features=reader.read(frame))
    assert reader.calls==[0] and counter.last_backend=='pl_features'

    reader=Reader(None)
    counter=make_runtime_counter(library)
    advance_count(counter,ControlState(),frame,capture_monotonic_ns=1,
                  region_features=reader.read(frame))
    assert reader.calls==[0] and counter.last_backend=='native'
