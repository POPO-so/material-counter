import uuid
import multiprocessing as mp
from pathlib import Path
import time

import numpy as np
import pytest

from scripts.rle.protocol import encode_mask, decode_packet, reconstruct
from scripts.standalone.integrated_run import (
    _refresh_dashboard_telemetry,
    _with_publish_boundary,
    dashboard_values,
    drawing_process,
    rasterize_into,
    result_metadata,
    start_drawing_child,
    source_identity,
)
from scripts.standalone.video_telemetry import FrameTelemetry
from scripts.standalone.preview_inputs import PreviewInputs
from scripts.standalone.shared_frames import SharedFrames
from scripts.wo0016.stream_counter import MaskCounter


def test_dashboard_unknown_metrics_stay_unknown():
    values=dashboard_values(9,False,0,{})

    assert values['count']==9
    assert values['camera_fps'] is None
    assert values['e2e_ms'] is None
    assert values['drop_total'] is None
    assert values['resolution_chain']=="640x400 -> 800x640 -> 1280x720"


def test_dashboard_prefers_fresh_video_telemetry_and_marks_stale_unknown():
    value=dict(camera_fps=29.5,effective_input_fps=24.1,
        e2e_ms={'mean':12.3},drop_total=7,
        resolution_chain="640x400 -> 800x640 -> 1280x720",
        processing_fps=23.7,regions_ms={'mean':2.1},track_ms={'mean':1.2},
        pl={'clock_hz':50_000_000},link_summary='link ok',page_lines=('PL 50 MHz',))
    telemetry={'video_telemetry':{'value':value,'stale':False}}

    values=dashboard_values(9,True,2,telemetry)
    assert values['detail_page']==2 and values['state']=='PAUSED'
    for key,item in value.items():
        assert values[key]==item
    stale_values=dashboard_values(9,False,0,{
        'video_telemetry':{'value':value,'stale':True}
    })
    for key in ('camera_fps','effective_input_fps','e2e_ms','drop_total',
                'processing_fps','regions_ms','track_ms','pl'):
        assert stale_values[key] is None
    assert stale_values['page_lines'] in (None,())
    assert stale_values['resolution_chain']=="640x400 -> 800x640 -> 1280x720"


def test_publish_failure_does_not_set_publish_boundary():
    frame=FrameTelemetry(frame_id=1,capture_ns=10,consume_ns=20,
        transfer_done_ns=30,regions_done_ns=40,track_done_ns=50)

    result=_with_publish_boundary(frame,False,60)

    assert result is frame
    assert result.publish_ns is None


def test_two_second_snapshot_is_reused_without_per_frame_cache_snapshot():
    class FakeCache:
        def __init__(self):
            self.calls=[]

        def snapshot(self,sampled_at):
            self.calls.append(sampled_at)
            return {'video_telemetry':{'value':{},'stale':False}}

    cache=FakeCache()
    latest=_refresh_dashboard_telemetry(cache,2.0)
    for frame in range(5):
        dashboard_values(frame,False,0,latest)

    assert cache.calls==[2.0]


def test_telemetry_does_not_change_result_metadata_or_count():
    mask=np.zeros((400,640),np.uint8);mask[100:110,200:220]=255
    result=MaskCounter(width=640,height=400,roi_mode='full',min_area=10).update(0,mask)
    common=dict(paused=False,binary_background=False,page=0)
    empty=result_metadata(result,**common,telemetry={})
    filled=result_metadata(result,**common,telemetry={
        'camera_fps':29.5,'e2e_ms':{'mean':12.3},'drop_total':7,
    })

    assert (empty['count'],empty['boxes'],empty['tracks'])==(filled['count'],filled['boxes'],filled['tracks'])


def test_source_identity_covers_loaded_counting_and_motion_code():
    identity=source_identity(Path(__file__).resolve().parents[2])
    assert 'scripts/wo0016/stream_counter.py' in identity['sha256_by_path']
    assert 'scripts/wo0016/motion_association.py' in identity['sha256_by_path']


class _StartFailureProcess:
    def __init__(self, *, pid_before_error=None, error=None):
        self.pid = None
        self.pid_before_error = pid_before_error
        self.error = error
        self.closed = False

    def start(self):
        if self.error is not None:
            self.pid = self.pid_before_error
            raise self.error
        self.pid = 1234

    def close(self):
        self.closed = True


class _StartFailureContext:
    def __init__(self, *, process=None, factory_error=None):
        self.stop = object()
        self.process = process
        self.factory_error = factory_error
        self.process_calls = 0

    def Event(self):
        return self.stop

    def Process(self, **_kwargs):
        self.process_calls += 1
        if self.factory_error is not None:
            raise self.factory_error
        return self.process


def test_drawing_process_creation_failure_returns_retryable_error():
    error = OSError("process factory failed")
    child, stop, caught = start_drawing_child(
        _StartFailureContext(factory_error=error), None, None, None, None, "font", "latin"
    )

    assert child is None and stop is None and caught is error


def test_drawing_process_start_failure_without_pid_closes_child():
    error = OSError("process start failed")
    candidate = _StartFailureProcess(error=error)
    child, stop, caught = start_drawing_child(
        _StartFailureContext(process=candidate), None, None, None, None, "font", "latin"
    )

    assert child is None and stop is None and caught is error
    assert candidate.closed


def test_drawing_process_start_failure_after_pid_retains_join_handle():
    error = OSError("process start reported late failure")
    candidate = _StartFailureProcess(pid_before_error=1234, error=error)
    context = _StartFailureContext(process=candidate)
    child, stop, caught = start_drawing_child(
        context, None, None, None, None, "font", "latin"
    )

    assert child is candidate and stop is context.stop and caught is error
    assert child.pid == 1234 and not candidate.closed


def test_drawing_process_success_returns_child_and_stop_event():
    candidate = _StartFailureProcess()
    context = _StartFailureContext(process=candidate)
    child, stop, error = start_drawing_child(
        context, None, None, None, None, "font", "latin"
    )

    assert child is candidate and stop is context.stop and error is None
    assert child.pid == 1234 and not candidate.closed


def test_injected_drawing_process_creation_failure_uses_no_process_factory():
    context = _StartFailureContext()
    child, stop, error = start_drawing_child(
        context, None, None, None, None, "font", "latin", failure_mode="create"
    )

    assert child is None and stop is None
    assert isinstance(error, OSError) and "creation" in str(error)
    assert context.process_calls == 0


def test_injected_drawing_start_failure_closes_unstarted_process():
    candidate = _StartFailureProcess()
    context = _StartFailureContext(process=candidate)
    child, stop, error = start_drawing_child(
        context, None, None, None, None, "font", "latin", failure_mode="start"
    )

    assert child is None and stop is None
    assert isinstance(error, OSError) and "Process.start" in str(error)
    assert candidate.pid is None and candidate.closed


def test_injected_error_after_drawing_start_retains_live_process_handle():
    candidate = _StartFailureProcess()
    context = _StartFailureContext(process=candidate)
    child, stop, error = start_drawing_child(
        context, None, None, None, None, "font", "latin", failure_mode="after-start"
    )

    assert child is candidate and stop is context.stop
    assert isinstance(error, OSError) and "after drawing Process.start" in str(error)
    assert child.pid == 1234 and not candidate.closed


def test_raster_reuses_storage_and_matches_protocol_reference():
    mask=np.zeros((400,640),np.uint8);mask[30:90,10:50]=255;mask[200:203,300:490]=255
    frame=decode_packet(encode_mask(mask,frame_id=3))
    out=np.full_like(mask,93)
    assert rasterize_into(frame,out) is out
    assert np.array_equal(out,reconstruct(frame))
    empty=decode_packet(encode_mask(np.zeros_like(mask),frame_id=4))
    rasterize_into(empty,out)
    assert not out.any()


def test_invalid_frame_never_publishes_partial_mask():
    mask=np.zeros((400,640),np.uint8);mask[:,::2]=255
    frame=decode_packet(encode_mask(mask,frame_id=1,max_runs=2))
    out=np.full_like(mask,93)
    with pytest.raises(ValueError,match='valid'):
        rasterize_into(frame,out)
    assert np.all(out==93)


def test_preview_metadata_contains_only_small_annotations_and_controls():
    mask=np.zeros((400,640),np.uint8);mask[100:110,200:220]=255
    result=MaskCounter(width=640,height=400,roi_mode='full',min_area=10).update(0,mask)
    metadata=result_metadata(result,paused=True,binary_background=False,page=1)
    assert metadata['boxes']==[[200,100,219,109]]
    assert metadata['paused'] and not metadata['binary_background'] and metadata['page']==1
    assert 'row_projection' not in metadata and 'gray' not in metadata
    assert metadata['tracks'][0][0]=='0:1'
    resumed=result_metadata(result,paused=False,binary_background=True,page=0,trajectory_epoch=2)
    assert resumed['tracks'][0][0]=='2:1'


@pytest.mark.parametrize('axis,position', [('x',320),('y',200)])
def test_actual_drawing_child_preserves_frame_identity_and_renders_count_line(axis,position):
    font=Path('C:/Windows/Fonts/msyh.ttc')
    if not font.is_file():pytest.skip('local font unavailable')
    ctx=mp.get_context('spawn');inputs=PreviewInputs.create(ctx);outputs=SharedFrames.create(ctx)
    stop=ctx.Event();errors=ctx.Queue(maxsize=1);counts=ctx.RawArray('Q',2)
    child=ctx.Process(target=drawing_process,args=(inputs,outputs,stop,errors,counts,str(font),str(font)))
    child.start();lease=None
    try:
        session=uuid.uuid4();gray=np.full((400,640),80,np.uint8);binary=np.zeros_like(gray)
        metadata=dict(boxes=[],tracks=[],binary_background=True,status_lines=['测试'])
        metadata['count_line']=dict(axis=axis,position=position,direction=1)
        assert inputs.publish(gray,binary,session,77,metadata)
        deadline=time.monotonic()+10
        while lease is None and time.monotonic()<deadline:
            lease=outputs.acquire_latest();time.sleep(.005)
        assert lease is not None and child.is_alive()
        assert lease.session_uuid==session and lease.frame_id==77
        marker=np.packbits((lease.array[0,640:864]==255).astype(np.uint8)).tobytes()
        assert uuid.UUID(bytes=marker[2:18])==session
        assert int.from_bytes(marker[18:26],'big')==77
        # Dashboard viewport is (256,144,768,480); both axes retain source coordinates.
        if axis=='x':
            assert lease.array[154,640]==248
            assert lease.array[164,640]==0
        else:
            assert lease.array[384,266]==248
            assert lease.array[384,276]==0
        outputs.release_read(lease.slot);lease=None
    finally:
        stop.set();child.join(timeout=10)
        if lease is not None:outputs.release_read(lease.slot)
        inputs.close();outputs.close()
    assert child.exitcode==0 and counts[0]==1
