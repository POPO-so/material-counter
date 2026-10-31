"""Bounded report exports: freeze ordinary state, then serialize away from DMA."""
from copy import deepcopy
import queue
import threading

from . import evidence


class EvidenceExporter:
    def __init__(self):
        self._queue=queue.Queue(maxsize=1)
        self._stop=threading.Event()
        self._lock=threading.Lock()
        self._stats=dict(submitted=0,saved=0,dropped=0,errors=0,last_error=None)
        self._thread=threading.Thread(target=self._run,name='report-export',daemon=True)
        self._thread.start()

    def status(self):
        with self._lock:
            return dict(self._stats,queue_depth=self._queue.qsize(),writer_alive=self._thread.is_alive())

    def submit(self,path,report):
        # Caller owns mutable report state and must hold any sampling lock while
        # this immutable snapshot is made. This thread never traverses live deques.
        if self._stop.is_set() or self._queue.full():
            with self._lock:self._stats['dropped']+=1
            return False
        frozen=deepcopy(report)
        try:self._queue.put_nowait((path,frozen))
        except queue.Full:
            with self._lock:self._stats['dropped']+=1
            return False
        with self._lock:self._stats['submitted']+=1
        return True

    def close(self,timeout=2):
        self._stop.set()
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self):
        while not self._stop.is_set() or not self._queue.empty():
            try:path,report=self._queue.get(timeout=.05)
            except queue.Empty:continue
            try:
                evidence.write_evidence(path,report)
                with self._lock:self._stats['saved']+=1
            except Exception as error:
                with self._lock:
                    self._stats['errors']+=1
                    self._stats['last_error']=repr(error)
            finally:self._queue.task_done()
