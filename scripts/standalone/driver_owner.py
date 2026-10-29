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
from dataclasses import dataclass, field


class FailHold(RuntimeError):
    pass


@dataclass
class _Job:
    function: object
    args: tuple
    done: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: BaseException | None = None


class DriverOwner:
    def __init__(self, poll=None):
        self.state = "RUNNING"
        self.failure = None
        self._poll = poll
        self._resources = []
        self._pending = None
        self._jobs = queue.Queue()
        self._serial = threading.Lock()
        self._hold = threading.Event()
        self._thread = threading.Thread(target=self._run, name="video-hardware-owner", daemon=True)
        self._thread.start()

    @property
    def retained(self):
        return tuple(self._resources)

    def retain(self, resource):
        self._resources.append(resource)
        return resource

    def _fail(self, reason):
        self.failure = str(reason)
        self.state = "FAIL_HOLD"
        return FailHold(self.failure)

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            if self.state == "FAIL_HOLD":
                self._hold.wait()  # Deliberate quarantine; keep all references.
                return
            try:
                job = self._jobs.get(timeout=0.01)
            except queue.Empty:
                if self._poll:
                    try:
                        self._poll()
                    except BaseException as exc:
                        self._fail(exc)
                continue
            if job is None:
                loop.close()
                return
            try:
                job.result = job.function(*job.args)
            except BaseException as exc:
                job.error = exc
                self._fail(exc)
            finally:
                job.done.set()

    def call(self, function, *args, timeout=2.0):
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        with self._serial:
            if self.state != "RUNNING":
                raise FailHold(self.failure or self.state)
            job = _Job(function, args)
            self._pending = job
            self._jobs.put(job)
            if not job.done.wait(timeout):
                raise self._fail(f"driver call exceeded {timeout}s; resources retained")
            if job.error is not None:
                raise FailHold(str(job.error)) from job.error
            if self.state != "RUNNING":
                raise FailHold(self.failure or self.state)
            result = job.result
            self._pending = None
            return result

    def close(self):
        with self._serial:
            if self.state != "RUNNING":
                raise FailHold(self.failure or self.state)
            self._jobs.put(None)
            self._thread.join(timeout=0.5)
            if self._thread.is_alive():
                raise self._fail("driver thread did not stop; resources retained")
            self.state = "CLOSED"
