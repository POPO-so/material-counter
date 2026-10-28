"""Cooperative completion for the verified PYNQ simple-DMA driver contract.

Starts still use PYNQ (including TX cache flush). RX cache invalidation,
transferred bytes and active-buffer cleanup follow PYNQ 3.1.1 wait().
Cancellation deliberately leaves ownership intact for the stream's FAIL_HOLD.
"""
import asyncio
import math
import time


class CooperativeDmaChannel:
    def __init__(self, channel, *, read, interval_s=0.0005):
        if (not callable(getattr(channel, "transfer", None))
                or getattr(channel, "_offset", None) not in (0, 0x30)
                or type(getattr(channel, "_flush_before", None)) is not bool
                or not hasattr(channel, "_active_buffer")
                or not hasattr(channel, "transferred") or not callable(read)):
            raise TypeError("Unsupported PYNQ simple-DMA channel contract")
        if not math.isfinite(interval_s) or not 0 < interval_s <= 0.1:
            raise ValueError("poll interval must be finite and in (0, 0.1] seconds")
        if channel._flush_before != (channel._offset == 0):
            raise TypeError("Unsupported PYNQ cache direction")
        self._channel = channel
        self._read = read
        self._interval_s = interval_s
        self._requested = None
        self.timings = []

    def transfer(self, buffer, *, nbytes):
        if self._requested is not None or self._channel._active_buffer is not None:
            raise RuntimeError("DMA still owns the previous transfer")
        self._channel.transfer(buffer, nbytes=nbytes)
        self._requested = nbytes

    async def wait_async(self):
        channel = self._channel
        if self._requested is None or channel._active_buffer is None:
            raise RuntimeError("DMA has no active transfer")
        polls = 0
        started = time.perf_counter()
        cpu_ms = 0.0
        while True:
            cpu_started = time.thread_time()
            status = self._read(channel._offset + 4)
            polls += 1
            cpu_ms += (time.thread_time() - cpu_started) * 1000
            if status & 0x70:
                raise RuntimeError("DMA error status 0x%08x" % status)
            if status & 1:
                raise RuntimeError("DMA halted during active transfer")
            if status & 2:
                break
            await asyncio.sleep(self._interval_s)
        cpu_started = time.thread_time()
        actual = self._read(channel._offset + 0x28)
        if actual != self._requested:
            raise RuntimeError("DMA transferred length %d differs from requested %d" %
                               (actual, self._requested))
        if not channel._flush_before:
            channel._active_buffer.invalidate()
        channel.transferred = actual
        channel._active_buffer = None
        self._requested = None
        cpu_ms += (time.thread_time() - cpu_started) * 1000
        self.timings.append(dict(polls=polls, wall_ms=(time.perf_counter() - started) * 1000,
                                 cpu_ms=cpu_ms, actual_bytes=actual))
