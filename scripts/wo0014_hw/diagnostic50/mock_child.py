"""Test-only local protocol process. No PYNQ, SSH or FPGA backend."""
import json
import importlib.util
import os
import sys
import threading
import time
from pathlib import Path

from core import Worker, Journal, base

lock = threading.Lock()
state = 'NEW'


def emit(row):
    with lock:
        print(json.dumps(row), flush=True)


def heartbeat():
    while True:
        time.sleep(0.05)
        emit({'kind': 'heartbeat', 'state': state})


fault = sys.argv[1]
root = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location('fixture', Path(__file__).resolve().parent.parent / 'test_pynq_image_once.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
paths = fixture.make_artifacts(root)
plan = base.validate_inputs(*paths)
api = fixture.FakeApi(plan.packet)
if fault == 'overlay_error':
    def fail_overlay(*a, **kw):
        raise RuntimeError('Injected Overlay failure')
    api.Overlay = fail_overlay
original = api.MMIO
def mmio(*args, **kwargs):
    device = original(*args, **kwargs)
    if device.base == plan.dma_base:
        write = device.write
        def completed(offset, value):
            write(offset, value)
            if offset == 0x28:
                device.regs[4] |= 0x1000
        device.write = completed
    if fault == 'block':
        def blocked(offset):
            threading.Event().wait()  # Actual synchronous read never returns.
        device.read = blocked
    elif fault == 'mmio_error':
        def failed(offset):
            raise OSError('Injected MMIO read exception')
        device.read = failed
    return device
api.MMIO = mmio
worker = Worker(plan, api, Journal(root / 'mock-guard'),
                lambda: {'boot_id': 'mock-boot', 'exclusive': True}, fixed_identity=False,
                timeout_s=2, emit=lambda row: emit({'kind': 'event', **row}))
threading.Thread(target=heartbeat, daemon=True).start()
for line in sys.stdin:
    command = json.loads(line)
    state = command['stage']
    if state == 'MIN_READ' and fault == 'crash':
        os._exit(23)
    result = worker.command(state, command['report'])
    emit({'kind': 'result', 'boot_id': 'mock-boot', **result})
    if worker.state == 'PASS':
        raise SystemExit(0)
