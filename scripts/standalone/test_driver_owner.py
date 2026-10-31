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


def test_background_task_and_sync_jobs_share_owner_loop_without_nested_loop():
    owner = _owner()
    progressed = threading.Event()
    loop_ids = []

    async def background():
        import asyncio
        loop_ids.append(id(asyncio.get_running_loop()))
        progressed.set()
        await asyncio.Event().wait()

    task = owner.call(owner.start_background, lambda: background())
    assert progressed.wait(1)

    async def driver_style_sync_call():
        import asyncio
        loop_ids.append(id(asyncio.get_running_loop()))

    def synchronous_driver_call():
        import asyncio
        loop_ids.append(id(asyncio.get_event_loop()))
        asyncio.get_event_loop().run_until_complete(driver_style_sync_call())
        return "sync-call-completed"

    assert owner.call(synchronous_driver_call) == "sync-call-completed"
    assert owner.call(lambda: id(__import__("asyncio").get_event_loop())) == loop_ids[0]
    assert loop_ids[1] == loop_ids[0]
    owner.call(owner.cancel_background, task)
    owner.close()


def test_idle_background_progress_and_job_wakeup_do_not_use_poll_interval():
    owner = _owner()
    progressed = threading.Event()

    async def background():
        import asyncio
        while True:
            progressed.set()
            await asyncio.sleep(0.002)

    task = owner.call(owner.start_background, lambda: background())
    assert progressed.wait(1)
    start = threading.Event()
    real_get = owner._jobs.get

    def forbid_blocking_get(*, block=True, timeout=None):
        assert block is False
        return real_get(block=False)

    owner._jobs.get = forbid_blocking_get
    assert owner.call(start.set) is None
    assert start.is_set()
    owner.call(owner.cancel_background, task)
    owner.close()


def test_background_exception_is_reported_without_stopping_sync_driver_jobs():
    owner = _owner()

    async def broken():
        raise RuntimeError("button IRQ callback failed")

    task = owner.call(owner.start_background, lambda: broken())
    owner.call(lambda: None)
    failure = owner.failure_errors.get(timeout=1)
    assert "button IRQ callback failed" in str(failure)
    assert task.done()
    assert owner.call(lambda: "counter still advances") == "counter still advances"
    owner.close()


def test_background_callback_exception_is_reported():
    owner = _owner()
    callback_errors = []

    async def broken():
        raise RuntimeError("IRQ task failed")

    def bad_callback(error):
        callback_errors.append(error)
        raise ValueError("report callback failed")

    task = owner.call(lambda: owner.start_background(lambda: broken(), on_error=bad_callback))
    owner.call(lambda: None)
    assert task.done()
    assert isinstance(owner.failure_errors.get(timeout=1), RuntimeError)
    assert isinstance(owner.failure_errors.get(timeout=1), ValueError)
    assert callback_errors
    assert task not in owner._background
    owner.close()


def test_cancel_background_timeout_enters_fail_hold_and_retains_resource():
    owner = _owner()
    gate = threading.Event()
    resource = object()
    owner.retain(resource)

    async def cancellation_blocked():
        import asyncio
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            while not gate.is_set():
                await asyncio.sleep(.001)

    task = owner.call(owner.start_background, lambda: cancellation_blocked())
    owner.call(lambda: None)
    with pytest.raises(FailHold):
        owner.call(lambda: owner.cancel_background(task, timeout=.02), timeout=.2)
    assert owner.state == "FAIL_HOLD"
    assert owner.retained == (resource,)
    gate.set()
