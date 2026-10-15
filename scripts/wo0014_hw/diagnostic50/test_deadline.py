"""Offline fault injection for the independent stage deadline helper."""
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def deadline_module():
    assert (HERE / 'deadline.py').is_file(), 'deadline supervision helper is missing'
    import deadline
    return deadline


def disable_multiprocessing_context(monkeypatch):
    """Fail if deadline supervision tries to create spawn synchronization."""
    import multiprocessing

    def forbidden(*args, **kwargs):
        raise AssertionError('multiprocessing context must not be required')

    monkeypatch.setattr(multiprocessing, 'get_context', forbidden)


def test_success_does_not_require_multiprocessing_context(monkeypatch):
    d = deadline_module()
    disable_multiprocessing_context(monkeypatch)

    outcome = d.run_bounded(lambda ctx: (ctx.check_active(), 42)[1], 1.0,
                            phase='complete-without-multiprocessing')

    assert outcome.status == 'PASS'
    assert outcome.value == 42
    assert not outcome.revoked.is_set()


def test_operation_exception_does_not_require_multiprocessing_context(monkeypatch):
    d = deadline_module()
    disable_multiprocessing_context(monkeypatch)

    def operation(ctx):
        ctx.check_active()
        raise OSError('mock dispatch failure')

    outcome = d.run_bounded(operation, 1.0,
                            phase='exception-without-multiprocessing')

    assert outcome.status == 'ERROR'
    assert outcome.exception_type == 'OSError'
    assert 'mock dispatch failure' in outcome.error


def test_timeout_does_not_require_multiprocessing_context(monkeypatch):
    d = deadline_module()
    disable_multiprocessing_context(monkeypatch)
    release = threading.Event()

    outcome = d.run_bounded(lambda ctx: release.wait(), 0.1,
                            phase='timeout-without-multiprocessing')
    try:
        assert outcome.status == 'TIMEOUT'
        assert outcome.revoked.is_set()
        assert outcome.operation_thread.is_alive()
        assert outcome.child_killed is False
    finally:
        release.set()


def test_blocked_log_io_returns_timeout_and_revokes_operation():
    d = deadline_module()
    entered = threading.Event()
    release = threading.Event()

    def operation(ctx):
        entered.set()
        release.wait()  # Mock a filesystem write/flush that never returns.
        ctx.check_active()
        return 'unexpected'

    outcome = d.run_bounded(operation, 0.8, phase='journal-write')
    try:
        assert entered.is_set()
        assert outcome.status == 'TIMEOUT'
        assert outcome.phase == 'journal-write'
        assert outcome.revoked.is_set()
        assert outcome.value is None
    finally:
        release.set()


def test_blocked_command_writer_cannot_start_a_later_write_after_revoke():
    d = deadline_module()
    entered = threading.Event()
    release = threading.Event()
    writes = []

    def operation(ctx):
        entered.set()
        release.wait()  # Mock a writer delayed before reaching its write call.
        ctx.check_active()
        writes.append('late command')

    outcome = d.run_bounded(operation, 0.8, phase='command-write')
    try:
        assert entered.is_set()
        assert outcome.status == 'TIMEOUT'
        assert outcome.revoked.is_set()
        release.set()
        assert outcome.operation_thread.join(1) is None
        assert writes == []
    finally:
        release.set()


def test_inflight_blocked_write_can_complete_after_timeout():
    d = deadline_module()
    entered = threading.Event()
    release = threading.Event()
    writes = []

    def operation(ctx):
        ctx.check_active()  # Final check immediately before entering write.
        entered.set()
        release.wait()  # Mock an OS pipe write already blocked after dispatch.
        writes.append('write completed late')

    outcome = d.run_bounded(operation, 0.8, phase='command-write-in-flight')
    try:
        assert entered.is_set()
        assert outcome.status == 'TIMEOUT'
        assert outcome.revoked.is_set()
        release.set()
        outcome.operation_thread.join(1)
        assert writes == ['write completed late']
    finally:
        release.set()


def test_blocked_child_wait_returns_timeout_without_terminating_process():
    d = deadline_module()
    entered = threading.Event()
    release = threading.Event()
    child_state = {'alive': True}

    def operation(ctx):
        entered.set()
        release.wait()  # Mock wait/result collection on a still-live child.
        return {'child_killed': False, 'alive': child_state['alive']}

    outcome = d.run_bounded(operation, 0.8, phase='child-wait')
    try:
        assert entered.is_set()
        assert outcome.status == 'TIMEOUT'
        assert outcome.revoked.is_set()
        assert child_state['alive'] is True
        assert outcome.child_killed is False
    finally:
        release.set()


def test_operation_exception_is_retained_as_failure_evidence():
    d = deadline_module()
    def operation(ctx):
        ctx.check_active()
        raise OSError('mock dispatch failure')

    outcome = d.run_bounded(operation, 3.0, phase='dispatch')
    assert outcome.status == 'ERROR'
    assert outcome.exception_type == 'OSError'
    assert 'mock dispatch failure' in outcome.error
    assert outcome.child_killed is False


def test_success_returns_value_and_stops_watchdog():
    d = deadline_module()
    outcome = d.run_bounded(lambda ctx: (ctx.check_active(), 42)[1], 3.0,
                            phase='complete')
    assert outcome.status == 'PASS'
    assert outcome.value == 42
    assert not outcome.revoked.is_set()
    assert outcome.child_killed is False
    assert outcome.deadline_monotonic_s - outcome.started_monotonic_s == pytest.approx(3.0)


def test_dispatch_check_rejects_expired_deadline_before_write():
    d = deadline_module()
    writes = []

    def operation(ctx):
        ctx.revoked.set()
        ctx.check_active()
        writes.append('command')

    outcome = d.run_bounded(operation, 3.0, phase='dispatch')
    assert outcome.status == 'ERROR'
    assert outcome.exception_type == 'DeadlineRevoked'
    assert writes == []
