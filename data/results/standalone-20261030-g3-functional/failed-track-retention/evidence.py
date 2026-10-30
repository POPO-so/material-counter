"""Explicit evidence export and fail-hold that survives logging/SD failures."""
from collections import deque
import json
from pathlib import Path
import signal
import time


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
