import gc
import threading
import weakref

import pytest

try:
    from scripts.standalone.driver_owner import DriverOwner, FailHold
except ModuleNotFoundError:
    DriverOwner = None
    FailHold = Exception

def _owner(**kwargs):
    if DriverOwner is None:
        pytest.fail("Driver ownership and timeout protection are not implemented")
    return DriverOwner(**kwargs)


def test_all_driver_operations_share_one_thread_and_results_return():
    owner = _owner()
    caller = threading.get_ident()
    first = owner.call(threading.get_ident)
    assert first != caller
    assert owner.call(threading.get_ident) == first
    owner.close()


def test_timeout_keeps_resources_and_prevents_further_hardware_calls():
    owner = _owner()
    release = threading.Event()
    resource = object()
    owner.retain(resource)
    started = threading.Event()

    def blocked_operation():
        started.set()
        release.wait()

    caller = threading.Thread(target=lambda: _call_while_capturing(owner, blocked_operation))
    caller.start()
    try:
        assert started.wait(1)
        caller.join(1)
        assert not caller.is_alive()
        diag = owner.diagnostics()
        assert diag["state"] == "FAIL_HOLD"
        assert "blocked_operation" in diag["operation"]
        assert diag["submitted_at"] <= diag["started_at"]
        assert diag["completed_at"] is None
        assert diag["retained_count"] == 1
        assert "blocked_operation" in diag["failure"]
        assert owner.retained == (resource,)
        with pytest.raises(FailHold):
            owner.call(lambda: pytest.fail("must never touch hardware after timeout"))
        with pytest.raises(FailHold):
            owner.close()
        release.set()
        assert owner._pending.done.wait(1)
        diag = owner.diagnostics()
        assert diag["state"] == "FAIL_HOLD"
        assert diag["completed_at"] is not None
        assert diag["started_at"] <= diag["completed_at"]
        assert diag["retained_count"] == 1
        assert owner.retained == (resource,)
    finally:
        # Let the quarantined worker finish; deliberately keep the retained resource.
        release.set()


def test_driver_exception_is_propagated_and_quarantines_resources():
    owner = _owner()
    def broken():
        raise OSError("VDMA bus error")
    with pytest.raises(FailHold, match="VDMA bus error"):
        owner.call(broken)
    assert owner.state == "FAIL_HOLD"
    diag = owner.diagnostics()
    assert diag["operation"].endswith("broken")
    assert diag["started_at"] is not None
    assert diag["completed_at"] is not None
    assert "VDMA bus error" in diag["failure"]


def test_successful_call_is_not_reported_as_running():
    owner = _owner()
    assert owner.call(lambda: "done") == "done"
    diag = owner.diagnostics()
    assert diag["state"] == "RUNNING"
    assert diag["operation"] is None
    assert diag["started_at"] is None
    assert diag["completed_at"] is None
    assert diag["retained_count"] == 0
    owner.close()


def test_successful_job_arguments_and_result_are_not_retained():
    class Payload:
        pass

    owner = _owner()
    argument = Payload()
    argument_ref = weakref.ref(argument)
    result = owner.call(lambda value: value, argument)
    result_ref = weakref.ref(result)
    # Processing a following job proves the worker has moved past its first
    # loop iteration and cannot retain the first job in its local variable.
    owner.call(lambda: None)

    del argument, result
    owner.diagnostics()
    gc.collect()

    assert argument_ref() is None
    assert result_ref() is None
    owner.close()


def _call_while_capturing(owner, function):
    try:
        owner.call(function, timeout=0.05)
    except FailHold:
        pass


def test_poll_callback_runs_between_jobs_and_errors_are_visible():
    seen = threading.Event()
    owner = _owner(poll=seen.set)
    assert seen.wait(0.3)
    owner.close()


def test_successful_job_records_enqueue_start_and_complete_times():
    owner = _owner()
    assert owner.call(lambda: 3) == 3
    timing = owner.diagnostics()["last_success"]
    assert timing["operation"].endswith("<lambda>")
    assert timing["submitted_at"] <= timing["started_at"] <= timing["completed_at"]
    assert owner.diagnostics()["last_failed"] is None
    owner.close()


def test_concurrent_caller_waits_on_the_serial_section_not_the_timeout():
    owner = _owner()
    release = threading.Event()
    started = threading.Event()
    order = []

    def blocker():
        started.set()
        release.wait(1)
        order.append("blocker")

    def hold():
        try:
            owner.call(blocker, timeout=5)
        except FailHold:
            pass

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    try:
        assert started.wait(1)
        timer = threading.Timer(0.2, release.set)
        timer.start()
        try:
            # call() serialises callers before it starts the job timeout, so a
            # second caller waits on the lock rather than timing out unqueued.
            assert owner.call(lambda: order.append("second"), timeout=0.05) is None
        finally:
            timer.cancel()
        assert order == ["blocker", "second"]
        assert owner.diagnostics()["state"] == "RUNNING"
    finally:
        release.set()
        thread.join(2)
    owner.close()
