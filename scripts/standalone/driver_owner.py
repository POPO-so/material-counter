"""One driver thread with bounded callers and resource retention on uncertainty.

Timeout never kills the driver thread or releases DMA memory. The process using
this class must stay alive in FAIL_HOLD until hardware is independently safe.
Closing this worker does not stop hardware; its caller must stop DMA first.
"""
from __future__ import annotations

import asyncio
import math
import queue
import threading
import time
from dataclasses import dataclass, field


class FailHold(RuntimeError):
    pass


@dataclass
class _Job:
    function: object
    args: tuple
    operation: str
    submitted_at: float
    done: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: BaseException | None = None
    started_at: float | None = None
    completed_at: float | None = None


class DriverOwner:
    def __init__(self, poll=None):
        self.state = "RUNNING"
        self.failure = None
        self._poll = poll
        self._resources = []
        self._pending = None
        self._active_job = None
        self._last_failed = None
        self._diagnostics_lock = threading.Lock()
        self._jobs = queue.Queue()
        self._serial = threading.Lock()
        self._hold = threading.Event()
        self._ready = threading.Event()
        self._loop = None
        self._wake = None
        self._background = set()
        self.failure_errors = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="video-hardware-owner", daemon=True)
        self._thread.start()
        if not self._ready.wait(1):
            raise RuntimeError("driver owner loop did not initialize")

    @property
    def retained(self):
        return tuple(self._resources)

    def retain(self, resource):
        self._resources.append(resource)
        return resource

    def diagnostics(self):
        """Return a read-only snapshot of driver state and task timing."""
        with self._diagnostics_lock:
            job = self._active_job
            if job is not None and not job.done.is_set():
                operation = job.operation
                submitted_at = job.submitted_at
                started_at = job.started_at
                completed_at = job.completed_at
            elif self._last_failed is not None:
                operation, submitted_at, started_at, completed_at = self._last_failed
            else:
                operation = submitted_at = started_at = completed_at = None
            return {
                "state": self.state,
                "failure": self.failure,
                "operation": operation,
                "submitted_at": submitted_at,
                "started_at": started_at,
                "completed_at": completed_at,
                "retained_count": len(self._resources),
            }

    @staticmethod
    def _operation_name(function):
        name = getattr(function, "__qualname__", None)
        return name if isinstance(name, str) and name else type(function).__name__

    def _fail(self, reason, job=None):
        failure = str(reason)
        with self._diagnostics_lock:
            self.failure = failure
            self.state = "FAIL_HOLD"
            if job is not None:
                self._last_failed = (job.operation, job.submitted_at,
                                     job.started_at, job.completed_at)
        return FailHold(self.failure)

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._wake = asyncio.Event()

        async def legacy_poll():
            while True:
                await asyncio.sleep(0.01)
                try:
                    self._poll()
                except BaseException as exc:
                    self._fail(exc)
                    return

        if self._poll is not None:
            poll_task = loop.create_task(legacy_poll())
            poll_task.add_done_callback(self._background_done)
            self._background.add(poll_task)
        self._ready.set()
        while True:
            if self.state == "FAIL_HOLD":
                self._hold.wait()  # Deliberate quarantine; keep all references.
                return
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                try:
                    loop.run_until_complete(self._wait_for_wakeup())
                except BaseException as exc:
                    self._fail(exc)
                continue
            if job is None:
                self._cancel_all_background()
                loop.close()
                return
            with self._diagnostics_lock:
                self._active_job = job
                job.started_at = time.monotonic()
            try:
                job.result = job.function(*job.args)
                # Run callbacks and give each background coroutine one scheduling
                # turn while the loop is stopped between synchronous PYNQ jobs.
                loop.run_until_complete(asyncio.sleep(0))
            except BaseException as exc:
                job.error = exc
                self._fail(exc, job)
            finally:
                with self._diagnostics_lock:
                    job.completed_at = time.monotonic()
                    if self._active_job is job:
                        self._active_job = None
                    if (self.state == "FAIL_HOLD" and self._last_failed is not None
                            and self._last_failed[:2] == (job.operation, job.submitted_at)):
                        self._last_failed = (job.operation, job.submitted_at,
                                             job.started_at, job.completed_at)
                job.done.set()
            # Do not let the worker's loop variable keep a successful job's
            # arguments or result alive while waiting for the next queue item.
            del job

    async def _wait_for_wakeup(self):
        await self._wake.wait()
        self._wake.clear()

    def _background_done(self, task):
        self._background.discard(task)
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is None:
            return
        self.failure_errors.put(error)

    def _cancel_all_background(self):
        tasks = tuple(task for task in self._background if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                self._loop.run_until_complete(asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=2.0))
            except BaseException as exc:
                self._fail(f"background task shutdown failed: {exc}")

    def call(self, function, *args, timeout=2.0):
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        with self._serial:
            if self.state != "RUNNING":
                raise FailHold(self.failure or self.state)
            operation = self._operation_name(function)
            job = _Job(function, args, operation, time.monotonic())
            self._pending = job
            self._jobs.put(job)
            self._loop.call_soon_threadsafe(self._wake.set)
            if not job.done.wait(timeout):
                raise self._fail(f"{operation} exceeded {timeout}s; resources retained", job)
            if job.error is not None:
                raise FailHold(str(job.error)) from job.error
            if self.state != "RUNNING":
                raise FailHold(self.failure or self.state)
            result = job.result
            self._pending = None
            return result

    def start_background(self, coro_factory, *, on_error=None):
        if not callable(coro_factory):
            raise TypeError("coro_factory must be callable")
        coroutine = coro_factory()
        if not asyncio.iscoroutine(coroutine):
            raise TypeError("coro_factory must return a coroutine")
        task = self._loop.create_task(coroutine)
        self._background.add(task)

        def completed(done):
            self._background.discard(done)
            try:
                error = done.exception()
            except asyncio.CancelledError:
                return
            if error is None:
                return
            self.failure_errors.put(error)
            if on_error is not None:
                try:
                    on_error(error)
                except BaseException as callback_error:
                    self.failure_errors.put(callback_error)

        task.add_done_callback(completed)
        return task

    def cancel_background(self, task, *, timeout=2.0):
        if task not in self._background:
            if task.done():
                return
            raise ValueError("task is not owned by this driver")
        task.cancel()
        self._loop.run_until_complete(asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True), timeout=timeout))

    def close(self):
        with self._serial:
            if self.state != "RUNNING":
                raise FailHold(self.failure or self.state)
            self._jobs.put(None)
            self._loop.call_soon_threadsafe(self._wake.set)
            self._thread.join(timeout=0.5)
            if self._thread.is_alive():
                raise self._fail("driver thread did not stop; resources retained")
            self.state = "CLOSED"
