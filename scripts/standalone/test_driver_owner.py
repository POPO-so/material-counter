import threading
import time

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
    with pytest.raises(FailHold):
        owner.call(release.wait, timeout=0.03)
    assert owner.state == "FAIL_HOLD"
    assert owner.retained == (resource,)
    with pytest.raises(FailHold):
        owner.call(lambda: pytest.fail("must never touch hardware after timeout"))
    with pytest.raises(FailHold):
        owner.close()
    release.set()


def test_driver_exception_is_propagated_and_quarantines_resources():
    owner = _owner()
    def broken():
        raise OSError("VDMA bus error")
    with pytest.raises(FailHold, match="VDMA bus error"):
        owner.call(broken)
    assert owner.state == "FAIL_HOLD"


def test_poll_callback_runs_between_jobs_and_errors_are_visible():
    seen = threading.Event()
    owner = _owner(poll=seen.set)
    assert seen.wait(0.3)
    owner.close()
