import json
from pathlib import Path
import sys
import numpy as np
import pytest
from scripts.rle.run_counter import main, synthetic_frames, golden_mask, decode_camera_frame


def test_offline_end_to_end_counter_and_overflow(tmp_path):
    output=tmp_path/'offline.json'
    assert main(['--mode','offline','--frames','12','--output',str(output)])==0
    result=json.loads(output.read_text())
    assert result['status']=='OFFLINE_VERIFIED'
    assert result['total_count']==1
    assert result['frames']==12 and result['pixel_verified_frames']==12
    assert result['resolution']==[640,400]
    with pytest.raises(FileExistsError):
        main(['--mode','offline','--output',str(output)])


def test_input_source_has_original_geometry_and_gray_threshold():
    frames=list(synthetic_frames(3))
    assert all(f.shape==(400,640) and f.dtype==np.uint8 for f in frames)
    gray=np.zeros((400,640),np.uint8);gray[20:40,10:30]=120
    mask=golden_mask(gray,roi=(5,10,600,350),threshold=128,intensity_offset=10,morph_mode=0)
    assert np.count_nonzero(mask)==400


def test_noise_stimulus_exercises_bounded_overflow(tmp_path):
    output=tmp_path/'noise.json'
    assert main(['--mode','offline','--frames','1','--pattern','noise','--output',str(output)])==0
    result=json.loads(output.read_text())
    assert result['invalid_frames']==1 and result['total_count']==0
    assert result['frames_evidence'][0]['status']==1
    assert result['frames_evidence'][0]['total_runs']==128000


def test_camera_mjpeg_decode_preserves_all_source_pixels():
    import cv2
    source=np.arange(400*640,dtype=np.uint8).reshape(400,640)
    ok,jpeg=cv2.imencode('.jpg',source)
    assert ok
    expected=cv2.imdecode(jpeg,cv2.IMREAD_GRAYSCALE)
    assert np.array_equal(decode_camera_frame(jpeg),expected)
    with pytest.raises(ValueError):decode_camera_frame(np.zeros((2,3),np.uint8))


def test_counter_configuration_preserves_x_axis_and_complex_rules(tmp_path):
    config=tmp_path/'counter.json'
    config.write_text(json.dumps(dict(count_axis='x',line_x=320,roi_top=20,roi_bottom=380,
        guarded_merge=True,single_intact_area=256,max_split_area=2160,
        max_merge_area=2160,max_area=2160,component_cluster_gap=0)))
    output=tmp_path/'configured.json'
    assert main(['--mode','offline','--frames','2','--counter-config',str(config),'--output',str(output)])==0
    result=json.loads(output.read_text())
    assert result['counter_configuration']['count_axis']=='x'
    assert result['counter_configuration']['guarded_merge'] is True
    assert result['configuration']['roi']==[0,20,640,380]
