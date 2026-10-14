"""Real local subprocess fault injection; the mock child has no hardware backend."""
import json
import sys
from pathlib import Path
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def test_supervisor_required():
    assert (HERE / 'supervisor.py').is_file(), 'external supervision is not implemented'


@pytest.mark.parametrize('fault', ['block', 'crash', 'mmio_error', 'overlay_error', 'success'])
def test_real_subprocess_stage_timeout_and_crash(tmp_path, fault):
    import supervisor as s
    link = s.ProcessLink([sys.executable, '-u', str(HERE / 'mock_child.py'), fault, str(tmp_path)],
                         tmp_path, timeout_s=0.6 if fault == 'block' else 3)
    checks = []
    def health():
        checks.append('health')
        return {'ping': True, 'ssh': True, 'linux': True, 'boot_id': 'mock-boot'}
    result = s.Supervisor(link, health).run()
    assert result['status'] == ('PASS' if fault == 'success' else 'FAIL')
    stages = link.sent
    if fault in ('block', 'crash', 'mmio_error'):
        assert stages == ['ENV', 'LOAD', 'HEALTH', 'MIN_READ']
    if fault == 'overlay_error':
        assert stages == ['ENV', 'LOAD']
    if fault == 'block':
        assert result['reason'] == 'SUPERVISOR_TIMEOUT'
        assert link.process.poll() is None
        assert result['child_killed'] is False
        assert result['axi_bus_recovered'] is False
        assert link.heartbeats > 0
    if fault == 'crash':
        assert result['exit_code'] == 23
    before = list(link.sent)
    with pytest.raises(RuntimeError):
        link.request('SEND')
    assert link.sent == before
    # Only the fake subprocess may be terminated by the test harness.
    if link.process.poll() is None:
        link.process.terminate(); link.process.wait(timeout=3)
    rows = [json.loads(line) for line in (tmp_path / 'supervisor-events.jsonl').read_text().splitlines()]
    assert rows and (tmp_path / 'status.json').is_file()


@pytest.mark.parametrize('phase', ['pre', 'post_load', 'post'])
@pytest.mark.parametrize('failed', ['ping', 'ssh', 'linux'])
def test_external_health_failure_does_not_advance(tmp_path, phase, failed):
    import supervisor as s
    link = s.ProcessLink([sys.executable, '-u', str(HERE / 'mock_child.py'), 'success', str(tmp_path)],
                         tmp_path, timeout_s=3)
    count = 0
    def health():
        nonlocal count
        count += 1
        value = {'ping': True, 'ssh': True, 'linux': True, 'boot_id': 'mock-boot'}
        if count == {'pre': 1, 'post_load': 2, 'post': 3}[phase]:
            value[failed] = False
        return value
    result = s.Supervisor(link, health).run()
    assert result['status'] == 'FAIL'
    if phase == 'pre':
        assert not link.sent
    if phase == 'post_load':
        assert link.sent == ['ENV', 'LOAD']
    if phase == 'post':
        assert 'POST' not in link.sent
        assert 'RELEASE' in link.sent
    if link.process.poll() is None:
        link.process.terminate(); link.process.wait(timeout=3)


def test_supervisor_session_restart_refused(tmp_path):
    import supervisor as s
    link = s.ProcessLink([sys.executable, '-u', str(HERE / 'mock_child.py'), 'success', str(tmp_path)], tmp_path)
    with pytest.raises(FileExistsError):
        s.ProcessLink([sys.executable, '-u', str(HERE / 'mock_child.py'), 'success', str(tmp_path)], tmp_path)
    link.process.terminate(); link.process.wait(timeout=3)
