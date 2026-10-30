"""Explicit evidence export and fail-hold that survives logging/SD failures."""
from collections import deque
import json
from pathlib import Path
import signal
import time
import hashlib
from dataclasses import asdict


class TraceBuffer:
    """Bound detailed diagnostics while hashing every processed frame.

    If annotations exceed the explicit cap, the digest covers the bounded view
    and total sizes and is labelled truncated; it cannot prove full equality.
    """
    def __init__(self,max_frames=600,max_annotations=128):
        if type(max_frames) is not int or type(max_annotations) is not int or min(max_frames,max_annotations)<1:
            raise ValueError('positive integer diagnostic limits required')
        self.frames=deque(maxlen=max_frames)
        self.max_annotations=max_annotations
        self.frames_seen=0
        self.annotations_truncated=False
        self._hash=hashlib.sha256()

    def add(self,frame_id,result,trajectory_epoch=0):
        limit=self.max_annotations
        groups=(result.detections,result.tracks,result.events,result.suppressed_events)
        truncated=any(len(group)>limit for group in groups)
        self.annotations_truncated |= truncated
        entry=dict(frame=frame_id,count=result.total_count,trajectory_epoch=trajectory_epoch,
            detections=[asdict(d) for d in result.detections[:limit]],tracks=result.tracks[:limit],
            events=[asdict(e) for e in result.events[:limit]],
            suppressed=[asdict(e) for e in result.suppressed_events[:limit]],
            detections_total=len(result.detections),tracks_total=len(result.tracks),
            annotations_truncated=truncated)
        self._hash.update(json.dumps(entry,sort_keys=True,separators=(',',':')).encode()+b'\n')
        self.frames.append(entry);self.frames_seen+=1

    def digest(self):return self._hash.hexdigest()


def _json_default(value):
    if isinstance(value,deque):return list(value)
    raise TypeError('unsupported evidence value '+type(value).__name__)


def write_evidence(path,report):
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,default=_json_default,separators=(',',':'))
        stream.write('\n')


def _hold_forever():
    # A failed log or repeated ordinary stop signal is not proof that DMA no
    # longer owns CMA. Recovery must independently establish that fact.
    for name in ('SIGINT','SIGTERM'):
        signal.signal(getattr(signal,name),signal.SIG_IGN)
    while True:
        time.sleep(1)


def fail_hold(writer,message,*,hold=None):
    try:
        writer()
    except BaseException as error:
        try:print('FAIL_HOLD_EVIDENCE_WRITE_FAILED '+repr(error),flush=True)
        except BaseException:pass
    try:print(message,flush=True)
    except BaseException:pass
    (hold or _hold_forever)()
