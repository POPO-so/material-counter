"""Regression tests for the optional long-running stream history policy."""
from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
import sys
import threading
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stream_counter import (  # noqa: E402
    DoubleBufferStream,
    MaskCounter,
    RX_BYTES,
    TX_BYTES,
    allocate_ping_pong_buffers,
)


class Buffer(np.ndarray):
    def __new__(cls, size, address):
        value = np.zeros(size, dtype=np.uint8).view(cls)
        value.physical_address = address
        return value


class Allocator:
    def __init__(self):
        self.next_address = 0x10000000

    def __call__(self, *, shape, dtype):
        del dtype
        value = Buffer(shape[0], self.next_address)
        self.next_address += 0x100000
        return value


class Channel:
    def __init__(self, *, rx=False):
        self.rx = rx
        self.active = False
        self.transfers = 0
        self.buffer = None

    def transfer(self, buffer, *, nbytes):
        assert nbytes == (RX_BYTES if self.rx else TX_BYTES)
        self.active = True
        self.transfers += 1
        self.buffer = buffer
        if self.rx:
            np.asarray(buffer).fill(0)

    async def wait_async(self):
        self.active = False


def make_stream(history_limit, *, on_frame=None, stage_timings=None):
    tx, rx = allocate_ping_pong_buffers(Allocator())
    recv, send = Channel(rx=True), Channel()
    stream = DoubleBufferStream(
        recv_channel=recv,
        send_channel=send,
        tx_buffers=tx,
        rx_buffers=rx,
        counter=MaskCounter(),
        packet_encoder=lambda _gray, *, frame_id: bytes([frame_id & 255]) * TX_BYTES,
        on_frame=on_frame,
        history_limit=history_limit,
        stage_timings_ms=stage_timings,
        transfer_timeout_s=1.0,
    )
    return stream, recv, send


async def finite_gray_frames(count):
    gray = np.zeros((240, 320), dtype=np.uint8)
    for _ in range(count):
        yield gray


def test_bounded_stream_retains_only_recent_results_and_timings():
    timings = {"encode": [], "dma": []}
    stream, _, _ = make_stream(7, stage_timings=timings)

    asyncio.run(stream.run(finite_gray_frames(100)))

    assert isinstance(stream._results, deque)
    assert stream._results.maxlen == 7
    assert len(stream._results) == 7
    assert stream.frames_processed == 100
    assert stream.last_result.frame_id == 99
    assert all(isinstance(values, deque) and values.maxlen == 7
               for values in timings.values())
    assert all(len(values) <= 7 for values in timings.values())


def test_graceful_stop_stops_source_then_drains_completed_dma_frames():
    stream, recv, send = make_stream(4)

    async def infinite_gray_frames():
        gray = np.zeros((240, 320), dtype=np.uint8)
        while True:
            yield gray

    result = asyncio.run(stream.run(
        infinite_gray_frames(),
        stop_requested=lambda: recv.transfers >= 3,
    ))

    assert stream.frames_processed == 3
    assert len(result) == 3
    assert stream.last_result.frame_id == 2
    assert recv.transfers == send.transfers == 3
    assert not recv.active and not send.active
    assert not stream.dma_may_be_active


def test_stop_cancels_pending_camera_read_and_drains_current_frame():
    stop = threading.Event()
    read_closed = threading.Event()
    stream, recv, send = make_stream(4)

    async def source():
        yield np.zeros((240, 320), dtype=np.uint8)
        try:
            await asyncio.Event().wait()
        finally:
            read_closed.set()

    timer = threading.Timer(0.02, stop.set)
    timer.daemon = True
    timer.start()
    try:
        result = asyncio.run(stream.run(source(), stop_requested=stop.is_set))
    finally:
        timer.join(timeout=0.2)

    assert len(result) == stream.frames_processed == 1
    assert stream.last_result.frame_id == 0
    assert read_closed.is_set()
    assert recv.transfers == send.transfers == 1
    assert not recv.active and not send.active
    assert not stream.dma_may_be_active


def test_cancelled_frame_read_preserves_cleanup_notes():
    stream, _, _ = make_stream(4)

    async def source_read_with_cleanup_failure():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            exc.__notes__ = ["camera worker failed to stop"]
            raise

    async def check():
        task = asyncio.create_task(source_read_with_cleanup_failure())
        await asyncio.sleep(0)
        error = await stream._cancel_frame_read(task)
        assert isinstance(error, RuntimeError)
        assert "camera worker failed to stop" in getattr(error, "__notes__", ())

    asyncio.run(check())


def test_cancelled_frame_read_without_cleanup_notes_remains_normal():
    stream, _, _ = make_stream(4)

    async def source_read():
        await asyncio.Event().wait()

    async def check():
        task = asyncio.create_task(source_read())
        await asyncio.sleep(0)
        assert await stream._cancel_frame_read(task) is None

    asyncio.run(check())


def test_wire_frame_id_wrap_is_opt_in_for_bounded_live_stream():
    assert DoubleBufferStream._packet_frame_id(0x1_0000_0003, bounded=True) == 3
    assert DoubleBufferStream._packet_frame_id(0x1_0000_0003, bounded=False) == 0x1_0000_0003
