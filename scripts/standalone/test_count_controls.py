import sys

import numpy as np
import pytest

from scripts.rle.components import build_native
from scripts.rle.counter import RleCounter
from scripts.rle.protocol import encode_mask, decode_packet
from scripts.standalone.controls import ControlState
from scripts.standalone.count_controls import advance_count


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
        commands=('clear','page','background') if i==5 else ()
        result=advance_count(counter,controls,packet(i,y),commands,
                             capture_monotonic_ns=(i+1)*100_000_000)
    assert result.total_count==0
    assert any(t['counted'] for t in result.tracks)
    assert controls.page==1 and not controls.binary_background
