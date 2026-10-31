"""Real captured components exercise segmentation and counting together."""
import json
from pathlib import Path
import sys

import cv2
import numpy as np
import pytest

from scripts.rle.components import build_native
from scripts.rle.protocol import decode_packet, encode_mask
from scripts.standalone.count_controls import make_runtime_counter, advance_count
from scripts.standalone.controls import ControlState

ROOT=Path(__file__).parent/'fixtures'


@pytest.fixture(scope='module')
def library(tmp_path_factory):
    return build_native(tmp_path_factory.mktemp('field-native')/('rle.dll' if sys.platform=='win32' else 'rle.so'))


@pytest.mark.parametrize('sequence',['touching_side_front','touching_two_front'])
def test_field_profile_splits_and_counts_two_real_touching_nuts(library,sequence):
    manifest=json.loads((ROOT/'crossing-masks-20261031.json').read_text())
    counter=make_runtime_counter(library,profile='field-v1')
    controls=ControlState()
    events=[]
    with np.load(ROOT/'crossing-masks-20261031.npz') as masks:
        for f in manifest['sequences'][sequence]:
            packet=decode_packet(encode_mask(masks[f['array']],frame_id=f['frame_id']))
            result=advance_count(counter,controls,packet,capture_monotonic_ns=f['capture_monotonic_ns'])
            assert len(result.detections)==2
            assert sum(d.area for d in result.detections)==packet.total_fg
            events.extend(result.events)
    assert len(events)==counter.total_count==2
    assert len({e.track_id for e in events})==2


def test_field_profile_does_not_split_captured_single_nuts(library):
    manifest=json.loads((ROOT/'crossing-masks-20261031.json').read_text())
    with np.load(ROOT/'crossing-masks-20261031.npz') as masks:
        for record in manifest['single_masks']:
            mask=np.zeros((400,640),np.uint8)
            crop=masks[record['array']]
            mask[100:100+crop.shape[0],100:100+crop.shape[1]]=crop
            counter=make_runtime_counter(library,profile='field-v1')
            packet=decode_packet(encode_mask(mask,frame_id=0))
            result=counter.update_rle(packet,capture_monotonic_ns=1)
            assert len(result.detections)==1,record


def test_field_profile_does_not_split_large_round_single_ring(library):
    mask=np.zeros((400,640),np.uint8)
    cv2.circle(mask,(120,120),16,255,-1)
    cv2.circle(mask,(120,120),9,0,-1)
    assert np.count_nonzero(mask)>380  # exercise the actual splitting path
    counter=make_runtime_counter(library,profile='field-v1')
    result=counter.update_rle(decode_packet(encode_mask(mask,frame_id=0)),capture_monotonic_ns=1)
    assert len(result.detections)==1


def test_field_profile_preserves_pause_and_clear_deduplication(library):
    counter=make_runtime_counter(library,profile='field-v1')
    controls=ControlState()
    for i,x in enumerate((260,285,310,335,360,385)):
        mask=np.zeros((400,640),np.uint8);mask[100:120,x:x+20]=255
        commands=('pause',) if i in (2,4) else ('clear',) if i==5 else ()
        packet=decode_packet(encode_mask(mask,frame_id=i))
        result=advance_count(counter,controls,packet,commands,capture_monotonic_ns=(i+1)*100_000_000)
    assert result.total_count==0 and not result.events


def test_unknown_runtime_profile_is_rejected():
    with pytest.raises(ValueError,match='profile'):
        make_runtime_counter(None,profile='unknown')
