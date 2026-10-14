"""Independent, spawn-safe deadlines for blocking diagnostic operations.

Run all work that must be covered by a stage deadline inside ``run_bounded``'s
callback, including journal/log writes, Popen, command write/flush, wait and
result parsing. The callback executes on a daemon thread; a separate daemon
thread owns the monotonic deadline and sets ``ctx.revoked`` at expiry. Neither
the helper nor its watchdog terminates a child process or releases resources.

The caller's timed result wait and the watchdog thread both use the same
in-process events. This avoids passing multiprocessing synchronization objects
through spawn serialization. Like any thread-based timeout, deadline handling
depends on Python and the OS scheduler running the caller/watchdog threads; it
cannot provide a hard real-time bound if the process is not scheduled or a
native call holds the interpreter lock indefinitely.

Before dispatch, call ``ctx.check_active()`` immediately before the first pipe
write. This prevents a writer that has not started yet from dispatching after
revocation. An OS write already blocked in the kernel may still complete later;
the board child must also reject commands whose ``expires_unix_ns`` is past.
The integrated session.py instead runs supervisor and child on the same board
and sends ctx.deadline_monotonic_s; child.py checks it before every access.
The workstation network path is disabled, so no cross-host clock comparison
is used. For any future remote transport, receiver-side wall-clock expiry is
only a strict bound when host/board wall-clock skew
has a verified limit. The current module supplies the absolute expiry value but
does not claim that remote clocks are synchronized or that software expiry can
release a hung AXI transaction.

Failure outcomes remain in memory and are sent to stderr on a daemon thread as
best effort. If filesystem or stderr I/O blocks, the bounded caller still gets
its result; complete disk logs cannot be guaranteed while storage is stuck.
"""

from __future__ import annotations

import json
import math
import queue
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable


_WATCHDOG_JOIN_S = 0.25
_TERMINAL_HISTORY_LIMIT = 128
_terminal_history: deque[dict[str, Any]] = deque(maxlen=_TERMINAL_HISTORY_LIMIT)
_terminal_lock = threading.Lock()
_fallback_threads: list[threading.Thread] = []


class DeadlineRevoked(RuntimeError):
    """The operation was revoked or its monotonic deadline has expired."""


@dataclass(frozen=True)
class BoundedResult:
    """A bounded operation result retained by the caller in ordinary memory."""

    status: str
    phase: str
    started_monotonic_s: float
    deadline_monotonic_s: float
    started_unix_ns: int
    deadline_unix_ns: int
    finished_monotonic_s: float
    elapsed_s: float
    value: Any = None
    late_value: Any = None
    exception_type: str | None = None
    error: str | None = None
    traceback_text: str | None = None
    child_killed: bool = False
    axi_bus_recovered: bool = False
    revoked: Any = field(default=None, repr=False, compare=False)
    expired: Any = field(default=None, repr=False, compare=False)
    operation_thread: threading.Thread | None = field(default=None, repr=False,
                                                       compare=False)
    # Kept under its historical public name; this now contains a watchdog
    # thread handle because cross-process SemLock serialization is avoided.
    watchdog_process: Any = field(default=None, repr=False, compare=False)

    @property
    def timed_out(self) -> bool:
        return self.status == 'TIMEOUT'

    def as_record(self) -> dict[str, Any]:
        """Return JSON-safe terminal evidence without serializing live objects."""
        return {
            'kind': 'bounded-operation-terminal',
            'phase': self.phase,
            'status': self.status,
            'started_monotonic_s': self.started_monotonic_s,
            'deadline_monotonic_s': self.deadline_monotonic_s,
            'started_unix_ns': self.started_unix_ns,
            'deadline_unix_ns': self.deadline_unix_ns,
            'finished_monotonic_s': self.finished_monotonic_s,
            'elapsed_s': self.elapsed_s,
            'exception_type': self.exception_type,
            'error': self.error,
            'traceback': self.traceback_text,
            'operation_thread_alive': bool(self.operation_thread and
                                           self.operation_thread.is_alive()),
            'watchdog_pid': getattr(self.watchdog_process, 'pid', None),
            'child_killed': False,
            'axi_bus_recovered': False,
        }


class DeadlineContext:
    """Cooperative stop signal and absolute deadline passed to one operation."""

    def __init__(self, deadline_monotonic_s: float, deadline_unix_ns: int,
                 revoked: Any, expired: Any):
        self.deadline_monotonic_s = deadline_monotonic_s
        self.deadline_unix_ns = deadline_unix_ns
        self.revoked = revoked
        self.expired = expired

    def remaining_s(self) -> float:
        return max(0.0, self.deadline_monotonic_s - time.monotonic())

    def check_active(self) -> None:
        """Fail closed before side effects, especially immediately before write."""
        if time.monotonic() >= self.deadline_monotonic_s:
            self.expired.set()
            self.revoked.set()
        if self.revoked.is_set() or self.expired.is_set():
            self.revoked.set()
            raise DeadlineRevoked('operation deadline expired or was revoked')


def _watch_deadline(deadline_monotonic_s: float, revoked: threading.Event,
                    expired: threading.Event, completed: threading.Event) -> None:
    """Watch the operation from a thread independent of its callback thread."""
    remaining = max(0.0, deadline_monotonic_s - time.monotonic())
    if not completed.wait(remaining):
        expired.set()
        revoked.set()


def _write_stderr_best_effort(line: str) -> None:
    try:
        sys.stderr.write(line + '\n')
        sys.stderr.flush()
    except BaseException:
        # Terminal evidence remains available through terminal_history() and
        # the returned BoundedResult even if stderr is also unavailable.
        return


def terminal_history() -> tuple[dict[str, Any], ...]:
    """Return an in-memory snapshot of recent terminal outcomes."""
    with _terminal_lock:
        return tuple(dict(record) for record in _terminal_history)


def _retain_terminal(result: BoundedResult) -> None:
    record = result.as_record()
    with _terminal_lock:
        _terminal_history.append(record)
    try:
        line = json.dumps(record, sort_keys=True, ensure_ascii=False)
    except BaseException:
        line = ('bounded operation terminal: phase=' + result.phase +
                ' status=' + result.status)
    thread = threading.Thread(target=_write_stderr_best_effort, args=(line,),
                              name='diagnostic50-terminal-stderr', daemon=True)
    with _terminal_lock:
        _fallback_threads.append(thread)
    try:
        thread.start()
    except BaseException:
        # Starting the fallback writer must never delay or replace the result.
        pass


def run_bounded(operation: Callable[[DeadlineContext], Any], timeout_s: float, *,
                phase: str = 'operation') -> BoundedResult:
    """Run ``operation(ctx)`` under a monotonic deadline without killing it.

    The deadline is captured before creating the watchdog or invoking the
    callback. The callback runs in its own daemon thread so a blocked filesystem
    call, pipe write/flush, process wait, or result read cannot block the caller.
    A second daemon thread sets the cooperative revoke event. The caller also
    has its own timed wait, so a watchdog scheduling failure cannot leave this
    function waiting forever. These are scheduler-based software bounds, not a
    hard real-time guarantee. On timeout, the operation thread and any child
    objects remain alive and reachable from the result.

    Callers must treat every status other than ``PASS`` as terminal and must
    not dispatch another hardware stage. Use ``ctx.check_active()`` directly
    before command writes. The check narrows the race but cannot cancel an OS
    write that has already blocked; use ``ctx.deadline_unix_ns`` in the wire
    message and enforce it in the child when the clock-skew bound is known.
    """
    started = time.monotonic()
    started_unix_ns = time.time_ns()
    if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or
            not math.isfinite(timeout_s) or timeout_s <= 0):
        raise ValueError('timeout_s must be a finite positive number')
    duration = float(timeout_s)
    deadline_monotonic_s = started + duration
    deadline_unix_ns = started_unix_ns + int(duration * 1_000_000_000)

    revoked = threading.Event()
    expired = threading.Event()
    completed = threading.Event()
    results: queue.Queue[tuple[str, float, Any, BaseException | None, str | None]] = queue.Queue()
    watchdog = threading.Thread(
        target=_watch_deadline,
        args=(deadline_monotonic_s, revoked, expired, completed),
        name='diagnostic50-deadline-watchdog')
    watchdog.daemon = True
    try:
        watchdog.start()
    except BaseException as exc:
        finished = time.monotonic()
        revoked.set()
        result = BoundedResult(
            status='ERROR', phase=phase, started_monotonic_s=started,
            deadline_monotonic_s=deadline_monotonic_s,
            started_unix_ns=started_unix_ns, deadline_unix_ns=deadline_unix_ns,
            finished_monotonic_s=finished, elapsed_s=max(0.0, finished - started),
            exception_type=type(exc).__name__, error='watchdog start failed: ' + str(exc),
            traceback_text=traceback.format_exc(), revoked=revoked, expired=expired,
            watchdog_process=watchdog)
        _retain_terminal(result)
        return result

    context = DeadlineContext(deadline_monotonic_s, deadline_unix_ns, revoked, expired)

    def invoke() -> None:
        value: Any = None
        error: BaseException | None = None
        traceback_value: str | None = None
        try:
            context.check_active()
            value = operation(context)
        except BaseException as exc:
            error = exc
            traceback_value = traceback.format_exc()
        finished = time.monotonic()
        results.put(('done', finished, value, error, traceback_value))
        completed.set()

    operation_thread = threading.Thread(target=invoke, name='diagnostic50-bounded-operation',
                                        daemon=True)
    try:
        operation_thread.start()
    except BaseException as exc:
        finished = time.monotonic()
        revoked.set()
        completed.set()
        watchdog.join(timeout=_WATCHDOG_JOIN_S)
        result = BoundedResult(
            status='ERROR', phase=phase, started_monotonic_s=started,
            deadline_monotonic_s=deadline_monotonic_s,
            started_unix_ns=started_unix_ns, deadline_unix_ns=deadline_unix_ns,
            finished_monotonic_s=finished, elapsed_s=max(0.0, finished - started),
            exception_type=type(exc).__name__, error='operation thread start failed: ' + str(exc),
            traceback_text=traceback.format_exc(), revoked=revoked, expired=expired,
            watchdog_process=watchdog)
        _retain_terminal(result)
        return result

    remaining = max(0.0, deadline_monotonic_s - time.monotonic())
    try:
        _, finished, value, error, traceback_value = results.get(timeout=remaining)
    except queue.Empty:
        finished = time.monotonic()
        expired.set()
        revoked.set()
        result = BoundedResult(
            status='TIMEOUT', phase=phase, started_monotonic_s=started,
            deadline_monotonic_s=deadline_monotonic_s,
            started_unix_ns=started_unix_ns, deadline_unix_ns=deadline_unix_ns,
            finished_monotonic_s=finished, elapsed_s=max(0.0, finished - started),
            error='deadline expired; operation remains unjoined and no child was terminated',
            revoked=revoked, expired=expired, operation_thread=operation_thread,
            watchdog_process=watchdog)
        _retain_terminal(result)
        return result

    received = time.monotonic()
    # A result delivered after its deadline is evidence only; it cannot turn a
    # timed-out operation into PASS or authorize a later command.
    expired_in_time = (finished >= deadline_monotonic_s or
                       received >= deadline_monotonic_s or expired.is_set())
    if expired_in_time:
        expired.set()
        revoked.set()
        result = BoundedResult(
            status='TIMEOUT', phase=phase, started_monotonic_s=started,
            deadline_monotonic_s=deadline_monotonic_s,
            started_unix_ns=started_unix_ns, deadline_unix_ns=deadline_unix_ns,
            finished_monotonic_s=finished, elapsed_s=max(0.0, finished - started),
            late_value=value, exception_type=type(error).__name__ if error else None,
            error=('operation completed after its deadline' if error is None else str(error)),
            traceback_text=traceback_value, revoked=revoked, expired=expired,
            operation_thread=operation_thread, watchdog_process=watchdog)
        _retain_terminal(result)
        return result

    completed.set()
    if error is not None:
        revoked.set()
        result = BoundedResult(
            status='ERROR', phase=phase, started_monotonic_s=started,
            deadline_monotonic_s=deadline_monotonic_s,
            started_unix_ns=started_unix_ns, deadline_unix_ns=deadline_unix_ns,
            finished_monotonic_s=finished, elapsed_s=max(0.0, finished - started),
            exception_type=type(error).__name__, error=str(error),
            traceback_text=traceback_value, revoked=revoked, expired=expired,
            operation_thread=operation_thread, watchdog_process=watchdog)
        _retain_terminal(result)
    else:
        result = BoundedResult(
            status='PASS', phase=phase, started_monotonic_s=started,
            deadline_monotonic_s=deadline_monotonic_s,
            started_unix_ns=started_unix_ns, deadline_unix_ns=deadline_unix_ns,
            finished_monotonic_s=finished, elapsed_s=max(0.0, finished - started),
            value=value, revoked=revoked, expired=expired,
            operation_thread=operation_thread, watchdog_process=watchdog)
    watchdog.join(timeout=min(_WATCHDOG_JOIN_S,
                              max(0.0, deadline_monotonic_s - time.monotonic())))
    return result
