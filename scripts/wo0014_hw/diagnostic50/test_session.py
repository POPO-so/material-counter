"""Session runner tests replace health and processes; no network or board."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.mark.parametrize('mode', ['overlay-only', 'mmio-read-only', 'dma-once', 'full'])
def test_hardware_modes_require_matching_authorization(mode):
    import session
    with pytest.raises(RuntimeError, match='authorization'):
        session.authorize(mode, True, None)
    with pytest.raises(RuntimeError, match='authorization'):
        session.authorize(mode, True, 'health-check-only')
    assert session.authorize(mode, True, mode) is None


@pytest.mark.parametrize('mode', ['health-check-only', 'deploy-verify-only'])
def test_read_only_modes_never_start_child(monkeypatch, tmp_path, mode):
    import session
    monkeypatch.setattr(session, 'require_secure_session', lambda: None)
    monkeypatch.setattr(session, 'verify_local', lambda: 'verified')
    monkeypatch.setattr(session, 'LocalHealth', lambda: lambda: {'ping': True, 'ssh': True, 'linux': True})
    monkeypatch.setattr(session, 'ProcessLink', lambda *a, **k: pytest.fail('spawn forbidden'))
    assert session.main(['--mode', mode, '--execute', '--evidence-dir', str(tmp_path)]) == 0


def test_non_root_denied_before_spawn(monkeypatch):
    import session
    monkeypatch.setattr(session.os, 'geteuid', lambda: 1000, raising=False)
    with pytest.raises(RuntimeError, match='sudo'):
        session.require_secure_session()


def test_full_does_not_substitute_loopback_for_external_health(monkeypatch, tmp_path):
    import session
    monkeypatch.setattr(session, 'require_secure_session', lambda: pytest.fail('full must fail earlier'))
    with pytest.raises(RuntimeError, match='independent workstation'):
        session.main(['--mode','full','--execute','--authorize-stage','full','--evidence-dir',str(tmp_path)])
