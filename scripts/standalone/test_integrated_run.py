import uuid
import multiprocessing as mp
from pathlib import Path
import time

import numpy as np
import pytest

from scripts.rle.protocol import encode_mask, decode_packet, reconstruct
from scripts.standalone.integrated_run import (
    drawing_process,
    rasterize_into,
    result_metadata,
    start_drawing_child,
)
from scripts.standalone.preview_inputs import PreviewInputs
from scripts.standalone.shared_frames import SharedFrames
from scripts.wo0016.stream_counter import MaskCounter


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

    def Event(self):
        return self.stop

    def Process(self, **_kwargs):
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


def test_actual_drawing_child_preserves_frame_identity_and_renders_horizontal_line():
    font=Path('C:/Windows/Fonts/msyh.ttc')
    if not font.is_file():pytest.skip('local font unavailable')
    ctx=mp.get_context('spawn');inputs=PreviewInputs.create(ctx);outputs=SharedFrames.create(ctx)
    stop=ctx.Event();errors=ctx.Queue(maxsize=1);counts=ctx.RawArray('Q',2)
    child=ctx.Process(target=drawing_process,args=(inputs,outputs,stop,errors,counts,str(font),str(font)))
    child.start();lease=None
    try:
        session=uuid.uuid4();gray=np.full((400,640),80,np.uint8);binary=np.zeros_like(gray)
        metadata=dict(boxes=[],tracks=[],binary_background=True,status_lines=['测试'])
        assert inputs.publish(gray,binary,session,77,metadata)
        deadline=time.monotonic()+10
        while lease is None and time.monotonic()<deadline:
            lease=outputs.acquire_latest();time.sleep(.005)
        assert lease is not None and child.is_alive()
        assert lease.session_uuid==session and lease.frame_id==77
        marker=np.packbits((lease.array[0,640:864]==255).astype(np.uint8)).tobytes()
        assert uuid.UUID(bytes=marker[2:18])==session
        assert int.from_bytes(marker[18:26],'big')==77
        assert np.all(lease.array[540,32:608]==255)
        outputs.release_read(lease.slot);lease=None
    finally:
        stop.set();child.join(timeout=10)
        if lease is not None:outputs.release_read(lease.slot)
        inputs.close();outputs.close()
    assert child.exitcode==0 and counts[0]==1
