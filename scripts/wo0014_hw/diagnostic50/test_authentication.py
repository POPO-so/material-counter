"""Credential handling is delegated to native terminal programs, never Python."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


def test_native_terminal_commands_fail_closed():
    import authentication as a
    ssh = a.terminal_argv('ssh', 'C:/verified/known_hosts')
    assert 'StrictHostKeyChecking=yes' in ssh
    assert 'NumberOfPasswordPrompts=1' in ssh
    assert 'PubkeyAuthentication=no' in ssh
    assert 'BatchMode=no' in ssh
    assert 'UserKnownHostsFile=C:/verified/known_hosts' in ssh
    assert ssh[-1] == 'xilinx@192.168.2.99'
    assert a.terminal_argv('sftp', 'C:/verified/known_hosts')[-1] == ssh[-1]


@pytest.mark.parametrize('failure', ['authentication-failed', 'host-key-mismatch', 'sudo-denied'])
def test_auth_failure_has_no_fallback_or_dispatch(failure):
    import authentication as a
    calls = []
    with pytest.raises(RuntimeError):
        a.require_authenticated_result(255, failure, lambda: calls.append('hardware'))
    assert calls == []


def test_no_automated_password_transport():
    import authentication as a
    with pytest.raises(ValueError):
        a.terminal_argv('sshpass', 'known_hosts')


def test_live_legacy_transports_disabled(monkeypatch):
    import supervisor
    monkeypatch.setattr(supervisor.subprocess, 'run', lambda *a, **k: pytest.fail('network forbidden'))
    with pytest.raises(RuntimeError, match='disabled'):
        supervisor.SSHHealth('xilinx@192.168.2.99', '192.168.2.99')()
    with pytest.raises(RuntimeError, match='disabled'):
        supervisor.verify_remote_files('xilinx@192.168.2.99', '/home/xilinx/isolated', None, None)
