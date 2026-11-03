import uuid
import multiprocessing as mp
from pathlib import Path
import time

import numpy as np
import pytest

import scripts.standalone.integrated_run as integrated_run
from scripts.rle.protocol import encode_mask, decode_packet, reconstruct
from scripts.standalone.integrated_run import (
    _live_drop_total,
    _refresh_dashboard_telemetry,
    _sample_camera_telemetry,
    _with_publish_boundary,
    dashboard_values,
    drawing_process,
    rasterize_into,
    result_metadata,
    start_drawing_child,
    source_identity,
    runtime_diagnostic_snapshot,
    QueueDepthWindow,
    queue_diagnostic_snapshot,
)
from scripts.standalone.video_telemetry import FrameTelemetry,TelemetryWindow
from scripts.standalone.preview_inputs import PreviewInputs
from scripts.standalone.shared_frames import SharedFrames
from scripts.standalone.camera import LatestCamera
from scripts.wo0016.stream_counter import MaskCounter


def test_dashboard_values_include_live_core_and_static_manifest_percentages():
    telemetry={
        'performance':{'stale':False,'value':{'display_fps':18.5,'processing_ms':41.2}},
        'resources':{'stale':False,'value':{
            'cpu':{'system':31.0,'cpu0':28.0,'cpu1':34.0},
            'memory':{'MemTotal':'1000 kB','MemAvailable':'538 kB'}}}}
    manifest={'resources':{'lut':12221,'ff':16174,'bram_tiles':4.5,'dsp':9}}

    values=dashboard_values(12,False,telemetry,manifest)

    assert (values['cpu_percent'],values['cpu0_percent'],values['cpu1_percent'])==(31.0,28.0,34.0)
    assert values['memory_percent']==46.2
    assert (values['static_lut_percent'],values['static_ff_percent'],
            values['static_bram_percent'],values['static_dsp_percent'])==(23.0,15.2,3.2,4.1)


def test_dashboard_values_include_mode_exposure_pl_rates_and_thread_summary():
    values = dashboard_values(2, False, {}, {}, mode="diagnostic",
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
    values = dashboard_values(0, False, {}, {},
        exposure={"minimum_ms": 5, "maximum_ms": 1000, "current_ms": 6,
                  "step_ms": 1},
        exposure_error="manual control unavailable; driver returned a long error")

    assert values["exposure_text"].startswith("\u66dd\u5149\u5931\u8d25:")
    assert len(values["exposure_text"]) <= 16
    assert values["exposure_range_text"] == "\u8303\u56f4(\u4e0a\u6b21) 5\u20131000 ms / \u00b11 ms"


def test_dashboard_values_keep_stale_and_missing_readings_unavailable():
    values=dashboard_values(0,False,{'resources':{'stale':True}},
                            {'resources':{'lut':12221}})

    assert values['cpu_percent'] is None
    assert values['cpu0_percent'] is None and values['cpu1_percent'] is None
    assert values['memory_percent'] is None
    assert values['static_lut_percent']==23.0
    assert values['static_ff_percent'] is None
    assert values['static_bram_percent'] is None
    assert values['static_dsp_percent'] is None
def test_dashboard_unknown_metrics_stay_unknown():
    values=dashboard_values(9,False,{})

    assert values['count']==9
    assert values['camera_fps'] is None
    assert values['e2e_ms'] is None
    assert values['drop_total'] is None
    assert values['resolution_chain']=="640x400 -> 640x400 -> 1280x720"


def test_dashboard_prefers_fresh_video_telemetry_and_marks_stale_unknown():
    value=dict(camera_fps=29.5,effective_input_fps=24.1,
        e2e_ms={'mean':12.3},drop_total=7,
        resolution_chain="640x400 -> 640x400 -> 1280x720",
        processing_fps=23.7,regions_ms={'mean':2.1},track_ms={'mean':1.2},
        pl={'clock_hz':50_000_000},link_summary='link ok')
    telemetry={'video_telemetry':{'value':value,'stale':False}}

    values=dashboard_values(9,True,telemetry)
    for key,item in value.items():
        assert values[key]==item
    stale_values=dashboard_values(9,False,{
        'video_telemetry':{'value':value,'stale':True}
    })
    for key in ('camera_fps','effective_input_fps','e2e_ms','drop_total',
                'processing_fps','regions_ms','track_ms','pl'):
        assert stale_values[key] is None
    assert stale_values['resolution_chain']=="640x400 -> 640x400 -> 1280x720"


def test_publish_failure_does_not_set_publish_boundary():
    frame=FrameTelemetry(frame_id=1,capture_ns=10,consume_ns=20,
        transfer_done_ns=30,regions_done_ns=40,track_done_ns=50)

    result=_with_publish_boundary(frame,False,60)

    assert result is frame
    assert result.publish_ns is None


def test_camera_absent_keeps_fps_unknown_not_zero():
    window=TelemetryWindow()
    for sample_time_ns in (1_000_000_000,2_000_000_000):
        camera_stats,camera_sample=_sample_camera_telemetry(
            None,window,sample_time_ns)
        assert camera_stats is None
        video_sample=window.sample(sample_time_ns)
        video_sample.update(camera=camera_sample)
        video_sample['camera_fps']=camera_sample['camera_fps']
        values=dashboard_values(0,False,{
            'video_telemetry':{'value':video_sample,'stale':False}
        })
        assert values['camera_fps'] is None


def test_live_drop_total_is_unknown_until_all_sources_measured():
    assert _live_drop_total(None, {'pump_skips': 2, 'pump_submit_failures': 1}) is None
    assert _live_drop_total({'camera_overwrite_drops': 3}, None) is None
    assert _live_drop_total({'camera_overwrite_drops': 3},
                            {'pump_skips': 2, 'pump_submit_failures': 1},
                            {'preview_publish_drops': 1, 'drawing_output_drops': 0}) == 7
    assert _live_drop_total({'camera_overwrite_drops': 0},
                            {'pump_skips': 0, 'pump_submit_failures': 0},
                            {'preview_publish_drops': 0, 'drawing_output_drops': 0}) == 0

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
        dashboard_values(frame,False,latest)

    assert cache.calls==[2.0]


def test_telemetry_does_not_change_result_metadata_or_count():
    mask=np.zeros((400,640),np.uint8);mask[100:110,200:220]=255
    result=MaskCounter(width=640,height=400,roi_mode='full',min_area=10).update(0,mask)
    common=dict(paused=False,binary_background=False)
    empty=result_metadata(result,**common,telemetry={})
    filled=result_metadata(result,**common,telemetry={
        'camera_fps':29.5,'e2e_ms':{'mean':12.3},'drop_total':7,
    })

    assert (empty['count'],empty['boxes'],empty['tracks'])==(filled['count'],filled['boxes'],filled['tracks'])


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


def test_candidate_bundle_capabilities_are_required_for_selected_irq_pl_or_raw(monkeypatch):
    verify = integrated_run.verify_selected_offload_bundle
    calls = []
    monkeypatch.setattr(integrated_run, "verify_offload_capabilities",
                        lambda manifest, hwh: calls.append((manifest, hwh)) or {"ok": True})
    def args(button="polling", preview="software", input_mode="packet"):
        return type("Args", (), {"button_input": button, "preview_backend": preview,
                                  "input_mode": input_mode})()

    for selected in (args(button="irq"), args(preview="pl"), args(input_mode="raw")):
        assert verify(selected, {"offload": True}, "candidate.hwh") == {"ok": True}
    assert len(calls) == 3
    assert verify(args(), {}, "legacy.hwh") is None
    assert len(calls) == 3


def test_mode_transition_resets_existing_capture_context_only():
    reset = getattr(integrated_run, "reset_capture_context_on_transition", None)
    assert callable(reset)
    class Captures:
        def __init__(self): self.reasons = []
        def reset_context(self, reason): self.reasons.append(reason)
    captures = Captures()
    reset(captures, None)
    assert captures.reasons == []
    reset(captures, {"from": "diagnostic", "to": "normal"})
    reset(None, {"from": "normal", "to": "diagnostic"})
    assert captures.reasons == ["diagnostic_mode_changed"]


def test_video_owner_has_periodic_poll_only_for_explicit_polling_mode():
    make_poll = integrated_run.button_input_poll_callback
    worker = type("Worker", (), {"poll": lambda self: None})()

    getter = lambda: worker
    assert make_poll("irq", getter) is None
    assert make_poll("off", getter) is None
    callback = make_poll("polling", getter)
    assert callable(callback)
    callback()

    from scripts.standalone.driver_owner import DriverOwner
    owner = DriverOwner(poll=make_poll("irq", getter))
    try:
        assert owner._poll is None
        assert owner._background == set()
    finally:
        owner.close()


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


def test_integrated_camera_consumes_late_decode_timeout_without_recovery():
    from scripts.standalone.camera import LateDecodeTimeout
    from scripts.standalone.integrated_run import read_camera_frame

    frame = (7, np.full((400,640),123,np.uint8), time.monotonic())
    class Camera:
        def __init__(self):
            self.close_calls = 0
        def read(self, *, after, timeout):
            assert after == 6 and timeout == .25
            raise LateDecodeTimeout(frame)
        def close(self):
            self.close_calls += 1

    camera=Camera()
    result=read_camera_frame(camera,after=6,timeout=.25)
    assert result[0]==frame[0] and result[2]==frame[2]
    assert np.array_equal(result[1],frame[1])
    assert camera.close_calls==0


def test_integrated_camera_read_preserves_genuine_no_frame_timeout():
    from scripts.standalone.integrated_run import read_camera_frame

    class Camera:
        def read(self, *, after, timeout):
            raise TimeoutError('no fresh real-camera Gray8 frame')

    with pytest.raises(TimeoutError,match='no fresh real-camera Gray8 frame'):
        read_camera_frame(Camera(),after=6,timeout=.25)


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
    metadata=result_metadata(result,paused=True,binary_background=False)
    assert metadata['boxes']==[[200,100,219,109]]
    assert metadata['paused'] and not metadata['binary_background']
    assert 'row_projection' not in metadata and 'gray' not in metadata
    assert metadata['tracks'][0][0]=='0:1'
    resumed=result_metadata(result,paused=False,binary_background=True,trajectory_epoch=2)
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
        # The source count coordinate is preserved in the active dashboard viewport.
        view_x, view_y, view_width, view_height = integrated_run.DashboardComposer.VIEW
        if axis=='x':
            display_x = view_x + round(position * view_width / 640)
            assert lease.array[view_y + 2, display_x]==248
            assert lease.array[view_y + 16, display_x]==0
        else:
            display_y = view_y + round(position * view_height / 400)
            assert lease.array[display_y, view_x + 10]==248
            assert lease.array[display_y, view_x + 18]==0
        outputs.release_read(lease.slot);lease=None
    finally:
        stop.set();child.join(timeout=10)
        if lease is not None:outputs.release_read(lease.slot)
        inputs.close();outputs.close()
    assert child.exitcode==0 and counts[0]==1

def test_runtime_diagnostic_snapshot_limits_threads_and_records_pool_occupancy():
    runtime = {"interval_s": 3.0, "threads": {
        "1": {"name": "cpu", "utime_ticks_delta": 2, "stime_ticks_delta": 1,
              "schedstat_run_ns_delta": 1_200_000_000,
              "schedstat_wait_ns_delta": 300_000_000},
        "2": {"name": "io", "utime_ticks_delta": 0, "stime_ticks_delta": 1,
              "schedstat_run_ns_delta": 500_000_000,
              "schedstat_wait_ns_delta": 900_000_000},
        "3": {"name": "other", "utime_ticks_delta": 20, "stime_ticks_delta": 0,
              "schedstat_run_ns_delta": 2_000_000_000,
              "schedstat_wait_ns_delta": 0},
    }}
    snapshot = runtime_diagnostic_snapshot(runtime,
        {"ready": 2, "capacity": 3}, {"ready": 1, "capacity": 3}, 100)

    assert [thread["tid"] for thread in snapshot["threads"]] == ["3", "1"]
    assert snapshot["threads"][0]["cpu_time_ms"] == 200.0
    assert snapshot["threads"][0]["run_time_ms"] == 2000.0
    assert snapshot["threads"][0]["wait_time_ms"] == 0.0
    assert snapshot["input_pool"] == {"ready": 2, "capacity": 3}
    assert snapshot["output_pool"] == {"ready": 1, "capacity": 3}

def test_dashboard_values_show_thread_run_wait_and_preview_pool_occupancy():
    values = dashboard_values(2, False, {}, {}, runtime_diagnostics={
        "threads": [{"name": "capture", "cpu_time_ms": 12.5,
                     "run_time_ms": 20.0, "wait_time_ms": 4.0}],
        "input_pool": {"ready": 2, "capacity": 3},
        "output_pool": {"ready": 1, "capacity": 3},
    })

    assert "capt" in values["thread_text"]
    assert "CPU12ms" in values["thread_text"]
    assert "run20ms" in values["thread_text"]
    assert "wait4ms" in values["thread_text"]
    assert values["queue_text"] == "IN 2/3  OUT 1/3"

def test_queue_depth_window_tracks_bounded_maximum_and_unknown_empty_state():
    window = QueueDepthWindow()
    assert window.snapshot(3) == {"window_max": None, "current": None, "capacity": 3}
    window.observe(0)
    window.observe(2)
    assert window.snapshot(3) == {"window_max": 2, "current": 2, "capacity": 3}
    assert window.snapshot(3) == {"window_max": None, "current": 2, "capacity": 3}
    window.observe(None)
    assert window.snapshot(3)["current"] is None

def test_queue_diagnostic_snapshot_preserves_unknown_and_reports_four_paths():
    snapshot = queue_diagnostic_snapshot(None, None)
    assert snapshot["camera_to_processing"] == {
        "depth": None, "dropped_old_frames": None, "recent_frame_overwritten": None}
    assert snapshot["main_to_preview"] is None
    assert snapshot["preview_to_drawing"]["ready"] is None
    assert snapshot["preview_to_drawing"]["dropped_frames"] is None
    assert snapshot["drawing_to_hdmi"]["ready"] is None
    assert snapshot["drawing_to_hdmi"]["submit_failures"] is None

def test_queue_diagnostic_snapshot_reads_pool_statistics_without_mutating_them():
    inputs = {"ready": 2, "capacity": 3, "dropped_ready_frames": 4}
    outputs = {"ready": 1, "capacity": 2, "dropped_ready_frames": 5}
    before = dict(inputs), dict(outputs)

    snapshot = queue_diagnostic_snapshot(inputs, outputs)

    assert snapshot["main_to_preview"] == {"ready": 2, "capacity": 3}
    assert (inputs, outputs) == before

def test_camera_read_after_is_a_repeatable_peek_without_consumer_state():
    import threading
    camera = LatestCamera.__new__(LatestCamera)
    camera._condition = threading.Condition()
    camera._error = None
    frame = object()
    camera._latest = (7, frame, 1.0)
    original_latest = camera._latest

    first = camera.read(after=6, timeout=0)
    second = camera.read(after=6, timeout=0)

    assert first is original_latest and second is original_latest
    assert camera._latest is original_latest

def test_dashboard_values_expose_cpu_and_queue_summaries_in_json_ready_fields():
    values = dashboard_values(0, False, {}, {}, runtime_diagnostics={
        "cpu_sources": {"camera": 2.0, "drawing": 1.0,
                        "external_ssh": 4.0, "external_other": 3.0},
        "queues": {"camera_to_processing": {"dropped_old_frames": 3,
            "recent_frame_overwritten": True},
            "preview_to_drawing": {"ready": 1, "dropped_frames": 4},
            "drawing_to_hdmi": {"ready": 2, "submit_failures": 0}},
    })
    assert "camera 2.0%" in values["cpu_source_text"]
    assert "SSH 4.0%" in values["cpu_source_text"]
    assert "ext 3.0%" in values["cpu_source_text"]
    assert "相机丢3" in values["queue_summary_text"]
    assert values["cpu_sources"]["camera"] == 2.0
    assert values["queues"]["drawing_to_hdmi"]["ready"] == 2

def test_cpu_scope_distinguishes_whole_machine_sources_from_legacy_single_core_processes():
    scope = getattr(integrated_run, "CPU_SCOPE_DESCRIPTION", "")
    assert "cpu_sources" in scope and "all logical CPUs" in scope
    assert "legacy process" in scope and "100% equals one core" in scope
    assert "network counters are not CPU percentages" in scope

def test_owner_region_feature_reader_configures_thresholds_only_when_changed_and_supported():
    class MMIO:
        def __init__(self, signature):
            self.length = 0x10000
            self.values = {0x200: signature, 0x204: 0, 0x218: 512,
                           0x220: 0, 0x224: 0, 0x228: 0, 0x22C: 0}
            self.writes = []
        def read(self, offset): return self.values[offset]
        def write(self, offset, value):
            self.writes.append((offset, value)); self.values[offset] = value
    class Owner:
        def __init__(self): self.calls = 0
        def call(self, fn, *args):
            self.calls += 1
            return fn(*args)
    class Counter:
        guarded_merge = True
        single_intact_area = 95

    owner = Owner()
    mmio = MMIO(0x46524732)
    reader = integrated_run.create_region_feature_reader("pl", mmio, owner=owner,
                                                          mmio_length=mmio.length)
    status = reader.configure_thresholds(Counter())
    assert status == {"supported": True, "configured": True,
                      "max_area": 95, "max_w": 0, "max_h": 0, "aspect_x16": 0}
    assert mmio.writes == [(0x220, 95), (0x224, 0), (0x228, 0), (0x22C, 0)]
    reader.configure_thresholds(Counter())
    assert len(mmio.writes) == 4
    Counter.guarded_merge = False
    status = reader.configure_thresholds(Counter())
    assert status["max_area"] == 0 and len(mmio.writes) == 8

    legacy = MMIO(0x46524731)
    legacy_reader = integrated_run.create_region_feature_reader(
        "pl", legacy, owner=owner, mmio_length=legacy.length)
    status = legacy_reader.configure_thresholds(Counter())
    assert status["supported"] is False and status["configured"] is False
    assert legacy.writes == []

def test_pl_button_status_is_low_level_diagnostic_snapshot_with_owner_serialization():
    sample = getattr(integrated_run, "sample_button_pl_status", None)
    assert callable(sample)
    class Owner:
        def __init__(self): self.calls = 0
        def call(self, fn): self.calls += 1; return fn()
    class Status:
        def __init__(self): self.clear = None
        def snapshot(self, *, clear=False):
            self.clear = clear
            return type("Snapshot", (), {"stable": 5, "events": 3, "fault": 1})()
    owner, status = Owner(), Status()

    result = sample(owner, status)

    assert result == {"available": True, "stable": 5, "events": 3, "fault": 1,
                      "external_fault_source": "no_external_fault_source"}
    assert status.clear is True and owner.calls == 1
    assert sample(owner, None) == {"available": None, "stable": "unknown",
                                   "events": "unknown", "fault": "unknown",
                                   "external_fault_source": "no_external_fault_source"}

def test_pl_button_status_read_failure_propagates():
    sample = integrated_run.sample_button_pl_status
    class Owner:
        def call(self, fn): return fn()
    class Status:
        def snapshot(self, *, clear=False): raise OSError("MMIO read failed")
    with pytest.raises(OSError, match="MMIO read failed"):
        sample(Owner(), Status())

def test_pl_candidate_count_is_unknown_for_missing_or_legacy_capability():
    count = getattr(integrated_run, "region_candidate_count", None)
    assert callable(count)
    features = (type("Feature", (), {"candidate": 1})(),
                type("Feature", (), {"candidate": 0})(),
                type("Feature", (), {"candidate": None})())
    reader = type("Reader", (), {"reader": type("Inner", (),
        {"supports_candidates": True})()})()
    legacy = type("Reader", (), {"reader": type("Inner", (),
        {"supports_candidates": False})()})()
    assert count(reader, features) == 1
    assert count(reader, ()) == 0
    assert count(legacy, features) is None
    assert count(None, features) is None
    assert count(reader, None) is None

def test_pl_region_backend_requires_pl_preview_and_verified_capabilities(monkeypatch):
    parser=integrated_run.build_argument_parser()
    required=["--manifest","m","--manifest-sha256","a"*64,"--boot-id",str(uuid.uuid4()),
              "--native-lib","lib","--output","out"]
    args=parser.parse_args(required+["--region-backend","pl","--preview-backend","software"])
    with pytest.raises(ValueError,match="requires --preview-backend pl"):
        integrated_run.validate_region_backend(args,{},"candidate.hwh")
    args.preview_backend="pl"
    monkeypatch.setattr(integrated_run,"verify_offload_capabilities",
                        lambda *_: (_ for _ in ()).throw(ValueError("PBO1 FRG1 capability mismatch")))
    with pytest.raises(ValueError,match="PBO1 FRG1 capability mismatch"):
        integrated_run.validate_region_backend(args,{},"candidate.hwh")

def test_region_reader_reuses_owner_mmio_window_and_checks_frge_capability():
    factory=getattr(integrated_run,"create_region_feature_reader",None)
    assert callable(factory)
    class MMIO:
        def __init__(self,length): self.length=length; self.values={0x200:0x46524731,0x218:512}; self.reads=[]
        def read(self,offset): self.reads.append(offset); return self.values[offset]
    mmio=MMIO(0x10000)
    owner=type("Owner",(),{"call":lambda self,fn,*args:fn(*args)})()
    reader=factory("pl",mmio,owner=owner,mmio_length=mmio.length)
    assert reader is not None and mmio.length>=0x5000
    assert mmio.reads==[0x200,0x218]
    with pytest.raises(ValueError,match="mapping"):
        factory("pl",mmio,owner=owner,mmio_length=0x1000)
    assert factory("native",mmio,owner=owner,mmio_length=0x1000) is None

def test_region_backend_admission_rejects_frg1_when_frg2_is_required():
    class MMIO:
        def __init__(self): self.values={0x200:0x46524731,0x218:512}
        def read(self,offset): return self.values[offset]
    class Owner:
        def __init__(self): self.calls=0
        def call(self,fn,*args): self.calls+=1; return fn(*args)
    owner=Owner()

    with pytest.raises(ValueError,match="FRG2.*required"):
        integrated_run.create_region_feature_reader(
            "pl",MMIO(),owner=owner,mmio_length=0x10000,require_frg2=True)

    assert owner.calls==1

def test_button_status_admission_rejects_btn1_signature_mismatch():
    factory=getattr(integrated_run,"create_button_event_status",None)
    assert callable(factory)
    class MMIO:
        def read(self,offset): return 0
    class Owner:
        def __init__(self): self.calls=0
        def call(self,fn,*args): self.calls+=1; return fn(*args)
    owner=Owner()

    with pytest.raises(ValueError,match="signature mismatch"):
        factory(owner,MMIO())

    assert owner.calls==1

def test_region_feature_reader_validates_frame_and_not_ready_falls_back():
    from scripts.rle.features import PlRegionFeatures
    from scripts.standalone.count_controls import advance_count,make_runtime_counter
    from scripts.standalone.controls import ControlState
    class MMIO:
        def __init__(self):
            self.values={0x200:0x46524731,0x218:512,0x204:1,0x208:99,
                         0x20c:0,0x210:0,0x214:0}
        def read(self,offset): return self.values[offset]
    class Owner:
        def call(self,fn,*args): return fn(*args)
    mmio=MMIO()
    reader=integrated_run.create_region_feature_reader('pl',mmio,owner=Owner(),mmio_length=0x10000)
    frame=decode_packet(encode_mask(np.zeros((400,640),np.uint8),frame_id=1))
    counter=make_runtime_counter(None)
    features=reader.read(frame)
    result=advance_count(counter,ControlState(),frame,capture_monotonic_ns=1,region_features=features)
    assert reader.last_reason=='frame_mismatch' and counter.last_backend=='native'
    assert reader.last_read_ms>=0 and result is not None
    mmio.values[0x208]=1;mmio.values[0x204]=0
    frame=decode_packet(encode_mask(np.zeros((400,640),np.uint8),frame_id=2))
    features=reader.read(frame)
    advance_count(counter,ControlState(),frame,capture_monotonic_ns=2,region_features=features)
    assert reader.last_reason=='not_done' and counter.last_backend=='native'

def test_region_frame_summary_records_reader_and_actual_counter_backend():
    summarize=getattr(integrated_run,'region_feature_frame_summary',None)
    assert callable(summarize)
    counter=type('Counter',(),{'last_backend':'native'})()
    summary=summarize('accepted',object(),counter)
    assert summary=={'feature_status':'accepted','feature_backend_used':'native'}
    summary=summarize('accepted',None,type('Counter',(),{'last_backend':'pl_features'})())
    assert summary=={'feature_status':'accepted','feature_backend_used':'not_counted'}

def test_frge_constructor_rejects_signature_or_capacity_mismatch():
    from scripts.rle.features import PlRegionFeatures
    class MMIO:
        def __init__(self,signature,capacity):self.values={0x200:signature,0x218:capacity}
        def read(self,offset):return self.values[offset]
    with pytest.raises(ValueError,match='signature'):
        PlRegionFeatures(MMIO(0,512))
    with pytest.raises(ValueError,match='capacity'):
        PlRegionFeatures(MMIO(0x46524731,511))
