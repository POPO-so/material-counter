"""Integration faults at the actual ProcessLink I/O boundaries."""
import sys
import threading
import time
from pathlib import Path
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


@pytest.mark.parametrize('fault', ['log', 'write', 'flush'])
def test_dispatch_io_timeout_is_terminal(tmp_path, fault):
    import supervisor as s
    link = s.ProcessLink([sys.executable, '-u', str(HERE/'mock_child.py'), 'success', str(tmp_path)],
                         tmp_path, timeout_s=0.7)
    release = threading.Event()
    old_record = link.record
    class Pipe:
        def write(self, text):
            if fault == 'write': release.wait(2)
        def flush(self):
            if fault == 'flush': release.wait(2)
    if fault == 'log':
        link.record = lambda *a, **k: release.wait(2)
    else:
        link.process.stdin = Pipe()
    started = time.monotonic()
    try:
        result = link.request('ENV')
        assert time.monotonic() - started < 1.7
        assert result['status'] == 'FAIL'
        assert link.stopped
        with pytest.raises(RuntimeError): link.request('SEND')
    finally:
        release.set()
        link.record = old_record
        link.process.terminate(); link.process.wait(3)


def test_child_terminated_prevents_next_command(tmp_path):
    import supervisor as s
    link = s.ProcessLink([sys.executable, '-u', str(HERE/'mock_child.py'), 'success', str(tmp_path)],
                         tmp_path, timeout_s=2)
    link.process.terminate(); link.process.wait(3)
    result = link.request('ENV')
    assert result['status'] == 'FAIL'
    assert link.stopped
    with pytest.raises(RuntimeError): link.request('SEND')


def test_dispatch_oserror_is_terminal(tmp_path):
    import supervisor as s
    link = s.ProcessLink([sys.executable, '-u', str(HERE/'mock_child.py'), 'success', str(tmp_path)],
                         tmp_path, timeout_s=2)
    class Pipe:
        def write(self, text): raise OSError('injected dispatch failure')
    link.process.stdin = Pipe()
    try:
        assert link.request('ENV')['status'] == 'FAIL'
        with pytest.raises(RuntimeError): link.request('SEND')
    finally:
        link.process.terminate(); link.process.wait(3)


def test_health_io_block_is_bounded(tmp_path):
    import supervisor as s
    link = s.ProcessLink([sys.executable, '-u', str(HERE/'mock_child.py'), 'success', str(tmp_path)],
                         tmp_path, timeout_s=0.7)
    release = threading.Event()
    try:
        result = s.Supervisor(link, lambda: release.wait(3)).run()
        assert result['status'] == 'FAIL' and not link.sent
    finally:
        release.set()
        link.process.terminate(); link.process.wait(3)


def test_concurrent_dispatch_revokes_delayed_writer(tmp_path):
    import supervisor as s
    link = s.ProcessLink([sys.executable, '-u', str(HERE/'mock_child.py'), 'success', str(tmp_path)],
                         tmp_path, timeout_s=2)
    entered, release = threading.Event(), threading.Event()
    writes = []
    old_record = link.record
    def record(event, **data):
        if event == 'DISPATCH': entered.set(); release.wait(3)
        else: old_record(event, **data)
    class Pipe:
        def write(self, text): writes.append(text)
        def flush(self): pass
    link.record = record
    link.process.stdin = Pipe()
    first = threading.Thread(target=lambda: link.request('ENV'))
    try:
        first.start(); assert entered.wait(2)
        assert link.request('SEND')['reason'] == 'CONCURRENT_DISPATCH'
        release.set(); first.join(3)
        assert not writes
        assert link.stopped
    finally:
        release.set(); first.join(3)
        link.process.terminate(); link.process.wait(3)
