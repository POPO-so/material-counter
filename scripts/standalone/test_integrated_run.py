import uuid
import multiprocessing as mp
from pathlib import Path
import time

import numpy as np
import pytest

import scripts.standalone.integrated_run as integrated_run
from scripts.rle.protocol import encode_mask, decode_packet, reconstruct
from scripts.standalone.integrated_run import (
    drawing_process,
    rasterize_into,
    result_metadata,
    start_drawing_child,
    source_identity,
    dashboard_values,
)
from scripts.standalone.preview_inputs import PreviewInputs
from scripts.standalone.shared_frames import SharedFrames
from scripts.wo0016.stream_counter import MaskCounter


def test_dashboard_values_include_live_core_and_static_manifest_percentages():
    telemetry={
        'performance':{'stale':False,'value':{'display_fps':18.5,'processing_ms':41.2}},
        'resources':{'stale':False,'value':{
            'cpu':{'system':31.0,'cpu0':28.0,'cpu1':34.0},
            'memory':{'MemTotal':'1000 kB','MemAvailable':'538 kB'}}}}
    manifest={'resources':{'lut':12221,'ff':16174,'bram_tiles':4.5,'dsp':9}}

    values=dashboard_values(12,False,False,telemetry,manifest)

    assert (values['cpu_percent'],values['cpu0_percent'],values['cpu1_percent'])==(31.0,28.0,34.0)
    assert values['memory_percent']==46.2
    assert (values['static_lut_percent'],values['static_ff_percent'],
            values['static_bram_percent'],values['static_dsp_percent'])==(23.0,15.2,3.2,4.1)


def test_dashboard_values_include_mode_exposure_pl_rates_and_thread_summary():
    values = dashboard_values(2, False, False, {}, {}, mode="diagnostic",
        exposure={"minimum_ms": 5, "maximum_ms": 1000, "current_ms": 5,
                  "step_ms": .1},
        pl_rates={"fresh": True, "rle_activity_pct": 32.0,
                  "input_mb_s": 12.5, "output_mb_s": 0.8,
                  "input_stall_pct": 1.0, "output_stall_pct": 0.0},
        runtime={"threads": {"101": {"name": "MainThread",
                                         "utime_ticks_delta": 2,
                                         "stime_ticks_delta": 1}}})

    assert values["mode"] == "DIAGNOSTIC"
    assert values["exposure_text"] == "\u66dd\u5149 5.0 ms"
    assert values["exposure_range_text"] == "\u8303\u56f4 5\u20131000 ms / \u00b11 ms"
    assert "RLE active (incl wait) 32%" in values["pl_text"] and "12.5" in values["pl_text"]
    assert "MainThr" in values["thread_text"] and "tick/s" in values["thread_text"]


def test_dashboard_exposure_error_keeps_last_known_range_and_bounds_error_text():
    values = dashboard_values(0, False, False, {}, {},
        exposure={"minimum_ms": 5, "maximum_ms": 1000, "current_ms": 6,
                  "step_ms": 1},
        exposure_error="manual control unavailable; driver returned a long error")

    assert values["exposure_text"].startswith("\u66dd\u5149\u5931\u8d25:")
    assert len(values["exposure_text"]) <= 16
    assert values["exposure_range_text"] == "\u8303\u56f4(\u4e0a\u6b21) 5\u20131000 ms / \u00b11 ms"


def test_dashboard_values_keep_stale_and_missing_readings_unavailable():
    values=dashboard_values(0,False,False,{'resources':{'stale':True}},
                            {'resources':{'lut':12221}})

    assert values['cpu_percent'] is None
    assert values['cpu0_percent'] is None and values['cpu1_percent'] is None
    assert values['memory_percent'] is None
    assert values['static_lut_percent']==23.0
    assert values['static_ff_percent'] is None
    assert values['static_bram_percent'] is None
    assert values['static_dsp_percent'] is None


def test_diagnostics_are_not_observed_or_hashed_in_normal_mode():
    policy_type = getattr(integrated_run, "DiagnosticPolicy", None)
    recorder = getattr(integrated_run, "record_diagnostic_frame", None)
    assert policy_type is not None and callable(recorder)
    policy = policy_type()

    class Trace:
        def __init__(self): self.calls = []
        def add(self, *args, **kwargs): self.calls.append((args, kwargs))

    class Captures:
        def __init__(self): self.calls = 0
        def observe(self, *args, **kwargs): self.calls += 1

    trace, captures = Trace(), Captures()
    recorder(policy, trace, captures, 7, object(), object(), object(), object())
    assert trace.calls[0][1]["enabled"] is False
    assert captures.calls == 0
    policy.set_mode("diagnostic", frame_id=8, timestamp_ns=10)
    recorder(policy, trace, captures, 8, object(), object(), object(), object())
    assert trace.calls[-1][1]["enabled"] is True
    assert captures.calls == 1


def test_preview_binary_is_only_rasterized_when_binary_background_is_visible():
    prepare = getattr(integrated_run, "prepare_preview_binary", None)
    assert callable(prepare)
    mask = np.zeros((400, 640), np.uint8)
    mask[20:30, 40:50] = 255
    packet = decode_packet(encode_mask(mask, frame_id=9))
    output = np.full_like(mask, 37)
    prepare(packet, output, binary_background=False)
    assert np.all(output == 37)
    prepare(packet, output, binary_background=True)
    assert np.array_equal(output, mask)


def test_button_input_cli_defaults_to_irq_and_supports_explicit_compatibility_modes():
    factory = getattr(integrated_run, "build_argument_parser", None)
    assert callable(factory)
    required = ["--manifest", "m", "--manifest-sha256", "a" * 64,
                "--boot-id", str(uuid.uuid4()), "--native-lib", "lib",
                "--output", "out"]
    parser = factory()
    assert parser.parse_args(required).button_input == "irq"
    for mode in ("polling", "off"):
        assert parser.parse_args(required + ["--button-input", mode]).button_input == mode


def test_preview_and_input_backend_cli_defaults_and_candidate_values():
    parser = integrated_run.build_argument_parser()
    required = ["--manifest", "m", "--manifest-sha256", "a" * 64,
                "--boot-id", str(uuid.uuid4()), "--native-lib", "lib",
                "--output", "out"]
    defaults = parser.parse_args(required)
    assert (defaults.preview_backend, defaults.input_mode) == ("software", "packet")
    candidate = parser.parse_args(required + ["--preview-backend", "pl", "--input-mode", "raw"])
    assert (candidate.preview_backend, candidate.input_mode) == ("pl", "raw")


def test_preview_frame_selection_keeps_binary_for_software_and_black_placeholders_for_pl():
    select = integrated_run.prepare_preview_frame
    gray = np.full((400, 640), 73, np.uint8)
    binary = np.full((400, 640), 255, np.uint8)
    selected_gray, selected_binary = select(gray, binary, backend="software")
    assert selected_gray is gray
    assert selected_binary is binary
    selected_gray, selected_binary = select(gray, binary, backend="pl")
    assert not np.any(selected_gray)
    assert not np.any(selected_binary)


def test_preview_panel_gate_publishes_on_change_or_at_two_hz_limit():
    gate = integrated_run.PreviewPublishGate()
    assert gate.should_publish(0, (3, "RUNNING"))
    assert not gate.should_publish(.1, (3, "RUNNING"))
    assert not gate.should_publish(.1, (4, "RUNNING"))
    assert gate.should_publish(.5, (4, "RUNNING"))


def test_irq_capability_requires_hwh_parameter_and_channel_wait_method():
    check = getattr(integrated_run, "button_gpio_irq_configured", None)
    assert callable(check)

    class Channel:
        async def wait_for_interrupt_async(self): pass

    class Overlay:
        def __init__(self, value):
            self.ip_dict = {"btns_gpio": {"parameters": {"C_INTERRUPT_PRESENT": value}}}

    assert check(Overlay("1"), Channel())
    assert not check(Overlay("0"), Channel())
    assert not check(Overlay("1"), object())


def test_irq_worker_refuses_unavailable_channel_without_polling_fallback():
    worker_type = getattr(integrated_run, "ButtonInputWorker", None)
    assert worker_type is not None
    from scripts.standalone.driver_owner import DriverOwner
    owner = DriverOwner()
    worker = worker_type(owner, "irq", object(), lambda: 0, lambda _: None, lambda _: None)
    with pytest.raises(RuntimeError, match="interrupt unavailable"):
        worker.start()
    assert worker._task is None
    owner.close()


def test_explicit_polling_worker_emits_events_and_stops_cleanly():
    worker_type = getattr(integrated_run, "ButtonInputWorker", None)
    from scripts.standalone.driver_owner import DriverOwner
    commands=[]
    holder={}
    owner=DriverOwner(poll=lambda:holder.get("worker") and holder["worker"].poll())
    worker=worker_type(owner,"polling",None,lambda:1,commands.append,lambda _:None)
    holder["worker"]=worker
    worker.start()
    deadline=time.monotonic()+1
    while not commands and time.monotonic()<deadline:time.sleep(.002)
    worker.stop()
    owner.close()
    assert commands==["clear"]


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
        # The source count coordinate is preserved under 4/5 binary preview scaling.
        if axis=='x':
            assert lease.array[156,640]==248
            assert lease.array[166,640]==0
        else:
            assert lease.array[306,394]==248
            assert lease.array[306,402]==0
        outputs.release_read(lease.slot);lease=None
    finally:
        stop.set();child.join(timeout=10)
        if lease is not None:outputs.release_read(lease.slot)
        inputs.close();outputs.close()
    assert child.exitcode==0 and counts[0]==1
