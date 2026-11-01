import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import pytest
from scripts.rle.run_counter import main, synthetic_frames, golden_mask, decode_camera_frame


def _pynq_state_for_bit(bit_path, status='GLOBAL_STATE_PRESENT'):
    contents = bit_path.read_bytes()
    return {
        'pl_state_status': status,
        'loaded_bit_path': str(bit_path),
        'loaded_bit_sha1': hashlib.sha1(contents).hexdigest(),
        'loaded_bit_sha256': hashlib.sha256(contents).hexdigest(),
    }


def test_preload_dma_check_is_skipped_for_verified_other_bitstream(tmp_path):
    from scripts.rle.run_counter import should_check_preload_dma

    loaded_bit = tmp_path / 'base.bit'
    loaded_bit.write_bytes(b'verified other bitstream')
    expected_hash = hashlib.sha256(b'pinned RLE bitstream').hexdigest()
    assert not should_check_preload_dma(_pynq_state_for_bit(loaded_bit), expected_hash)


def test_preload_dma_check_is_kept_for_expected_bitstream_and_cold_boot(tmp_path):
    from scripts.rle.run_counter import should_check_preload_dma

    expected_bit = tmp_path / 'rle.bit'
    expected_bit.write_bytes(b'pinned RLE bitstream')
    expected_hash = hashlib.sha256(expected_bit.read_bytes()).hexdigest()
    assert should_check_preload_dma(_pynq_state_for_bit(expected_bit), expected_hash)
    assert not should_check_preload_dma({
        'pl_state_status': 'COLD_BOOT_NO_GLOBAL_STATE',
        'loaded_bit_path': None,
        'loaded_bit_sha1': None,
        'loaded_bit_sha256': None,
    }, expected_hash)


def test_preload_dma_check_rejects_unverified_loaded_bit_metadata(tmp_path):
    from scripts.rle.run_counter import should_check_preload_dma

    loaded_bit = tmp_path / 'base.bit'
    loaded_bit.write_bytes(b'actual bitstream')
    state = _pynq_state_for_bit(loaded_bit)
    state['loaded_bit_sha1'] = '0' * 40
    with pytest.raises(RuntimeError, match='hashes do not match'):
        should_check_preload_dma(state, hashlib.sha256(b'RLE bitstream').hexdigest())


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


def test_camera_frames_decodes_raw_mjpeg_packets(monkeypatch):
    import cv2
    import scripts.rle.run_counter as run_counter
    sources=[np.full((400,640),value,np.uint8) for value in (35,145)]
    packets=[]
    for source in sources:
        ok,jpeg=cv2.imencode('.jpg',source)
        assert ok
        packets.append(jpeg)
    monkeypatch.setattr(run_counter,'camera_mjpeg_frames',lambda device,count: iter(packets))
    decoded=list(run_counter.camera_frames('/dev/test',2))
    assert len(decoded)==2
    assert all(np.array_equal(actual,decode_camera_frame(raw)) for actual,raw in zip(decoded,packets))


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
