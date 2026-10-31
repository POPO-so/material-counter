from collections import deque
import json

import pytest

from scripts.standalone.evidence import write_evidence, fail_hold, TraceBuffer


def test_bounded_diagnostic_buffers_export_as_json_without_overwrite(tmp_path):
    report={'samples':deque([1,2],maxlen=2)}
    path=tmp_path/'run.json'
    write_evidence(path,report)
    assert json.loads(path.read_text())=={'samples':[1,2]}
    with pytest.raises(FileExistsError):write_evidence(path,{'samples':[]})
    assert json.loads(path.read_text())=={'samples':[1,2]}


def test_failed_evidence_write_cannot_bypass_resource_hold():
    calls=[]
    def writer():
        calls.append('write');raise OSError('SD full')
    class Held(Exception):pass
    def hold():
        calls.append('hold');raise Held()
    with pytest.raises(Held):fail_hold(writer,'resources retained',hold=hold)
    assert calls==['write','hold']


def test_broken_log_pipe_cannot_bypass_resource_hold(monkeypatch):
    import builtins
    calls=[]
    def broken_print(*args,**kwargs):raise BrokenPipeError('SSH closed')
    monkeypatch.setattr(builtins,'print',broken_print)
    class Held(Exception):pass
    def hold():calls.append('hold');raise Held()
    with pytest.raises(Held):fail_hold(lambda:None,'retaining',hold=hold)
    assert calls==['hold']


def test_trace_hash_covers_all_frames_even_when_old_frame_details_are_retired():
    from scripts.wo0016.stream_counter import MaskCounter,Detection
    counter=MaskCounter(width=640,height=400,roi_mode='full',min_area=1)
    small=TraceBuffer(max_frames=3);large=TraceBuffer(max_frames=10)
    for i in range(10):
        result=counter.update_detections(i,[Detection(10,19,20+i,29+i,100,14.5,24.5+i)])
        small.add(i,result);large.add(i,result)
    assert len(small.frames)==3 and small.frames[0]['frame']==7
    assert small.frames_seen==10 and small.digest()==large.digest()
    assert not small.annotations_truncated


def test_trace_annotation_limits_never_change_counter_results():
    from scripts.wo0016.stream_counter import MaskCounter,Detection
    counter=MaskCounter(width=640,height=400,roi_mode='full',min_area=1)
    result=counter.update_detections(0,[Detection(10,19,20,29,100,14.5,24.5),Detection(40,49,20,29,100,44.5,24.5)])
    trace=TraceBuffer(max_frames=2,max_annotations=1)
    trace.add(0,result)
    assert len(trace.frames[0]['detections'])==1 and len(result.detections)==2
    assert trace.frames[0]['detections_total']==2 and trace.annotations_truncated


def test_disabled_trace_does_not_hash_or_retain_frame_details():
    from scripts.wo0016.stream_counter import MaskCounter, Detection
    result = MaskCounter(width=640,height=400,roi_mode='full',min_area=1).update_detections(
        0,[Detection(10,19,20,29,100,14.5,24.5)])
    trace = TraceBuffer()
    trace.add(0, result, enabled=False)
    assert trace.frames_seen == 0
    assert not trace.frames
    assert trace.digest() is None


def test_trace_mode_switch_records_boundary_and_only_diagnostic_adds_frames():
    from scripts.wo0016.stream_counter import MaskCounter, Detection
    from scripts.standalone.diagnostic_mode import DiagnosticPolicy
    result = MaskCounter(width=640,height=400,roi_mode='full',min_area=1).update_detections(
        0,[Detection(10,19,20,29,100,14.5,24.5)])
    policy = DiagnosticPolicy()
    trace = TraceBuffer()
    trace.add(0, result, enabled=policy.trace_enabled)
    policy.set_mode('diagnostic')
    trace.add(1, result, enabled=policy.trace_enabled)
    assert trace.frames_seen == 1 and trace.frames[-1]['frame'] == 1
    assert policy.transitions[0]['frame_id'] is None
