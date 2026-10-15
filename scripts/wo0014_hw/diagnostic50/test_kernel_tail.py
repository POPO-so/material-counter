"""Ring buffer continuity preserves a verified tail and checks every new line."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
def kernel_increment(before, after):
    from session import kernel_increment as increment
    return increment(before, after)


def test_head_rollover_keeps_tail_anchor():
    old = [f'[{i:5}.000000] normal {i}' for i in range(20)]
    appended = ['[   20.000000] fpga_manager writing bitstream']
    assert kernel_increment(old, old[9:] + appended) == appended


def test_no_new_lines_and_append():
    old = ['[1.0] first', '[2.0] last']
    assert kernel_increment(old, old) == []
    assert kernel_increment(old, old + ['[3.0] new']) == ['[3.0] new']


@pytest.mark.parametrize('after', [[], ['[3.0] unrelated'], ['[2.0] last']])
def test_lost_tail_fails_closed(after):
    with pytest.raises(RuntimeError, match='continuity'):
        kernel_increment(['[1.0] first', '[2.0] last'], after)


def test_duplicate_anchor_fails_closed():
    old = ['[1.0] first', '[2.0] last']
    with pytest.raises(RuntimeError, match='continuity'):
        kernel_increment(old, old + old)


def test_real_saved_rollover():
    root = Path(__file__).resolve().parents[3]
    logs = root / 'data/results/wo0014-coldstate-fix-20261015/board-logs'
    old = (logs / 'dmesg-before.txt').read_text().splitlines()
    after = (logs / 'dmesg-after.txt').read_text().splitlines()
    assert len(kernel_increment(old, after)) == 20


def test_local_health_checks_error_after_rolled_anchor(monkeypatch):
    import session
    from types import SimpleNamespace
    old = [f'[{i}.0] normal' for i in range(20)]
    snapshots = iter([old, old[5:] + ['[20.0] AXI fault']])
    monkeypatch.setattr(session, 'require_secure_session', lambda: None)
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout='\n'.join(next(snapshots)) if argv[0] == 'dmesg' else '')
    monkeypatch.setattr(session.subprocess, 'run', run)
    monkeypatch.setattr(session.Path, 'read_text', lambda p: 'operating' if p.name == 'state' else 'boot')
    health = session.LocalHealth()
    assert health()['new_kernel_warnings'] == []
    assert health()['new_kernel_warnings'] == ['[20.0] AXI fault']


def test_local_health_accepts_benign_rollover(monkeypatch):
    import session
    from types import SimpleNamespace
    old = [f'[{i}.0] normal' for i in range(20)]
    snapshots = iter([old, old[5:] + ['[20.0] fpga_manager writing image']])
    monkeypatch.setattr(session, 'require_secure_session', lambda: None)
    monkeypatch.setattr(session.subprocess, 'run', lambda argv, **kw: SimpleNamespace(
        returncode=0, stdout='\n'.join(next(snapshots)) if argv[0] == 'dmesg' else ''))
    monkeypatch.setattr(session.Path, 'read_text', lambda p: 'operating' if p.name == 'state' else 'boot')
    health = session.LocalHealth()
    health()
    report = health()
    assert report['new_kernel_warnings'] == []
    assert report['new_kernel_lines'] == ['[20.0] fpga_manager writing image']
