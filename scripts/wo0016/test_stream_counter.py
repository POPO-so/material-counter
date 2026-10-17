import asyncio
from dataclasses import dataclass

import numpy as np
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wo0015"))

from camera_adapter import gray320x240_to_rgb_packet
from mjpeg_preview import MjpegFrameHub, PeriodicMjpegPublisher
from packet_encoder import gray320x240_to_rgb_packet_into
from stream_counter import (
    BufferState,
    DoubleBufferStream,
    MaskCounter,
    RX_BYTES,
    TX_BYTES,
    allocate_ping_pong_buffers,
    transfer_async,
)


def _mask(center_y, *, center_x=80, size=5):
    mask = np.zeros((240, 320), dtype=np.uint8)
    half = size // 2
    mask[center_y - half:center_y + half + 1,
         center_x - half:center_x + half + 1] = 0xFF
    return mask


def test_area_filter_and_roi_keep_only_qualified_line_band_components():
    counter = MaskCounter(min_area=12, roi_half_height=12)
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[40:45, 30:35] = 0xFF        # outside the line ROI
    mask[110:113, 70:73] = 0xFF      # area 9, below min_area
    mask[110:115, 170:175] = 0xFF    # area 25, accepted

    result = counter.update(0, mask)

    assert len(result.detections) == 1
    assert result.detections[0].area == 25
    assert result.detections[0].centroid_x == pytest.approx(172.0)
    assert result.detections[0].centroid_y == pytest.approx(112.0)
    assert result.total_count == 0


def test_top_to_bottom_track_counts_once_after_confirmation_and_hysteresis():
    counter = MaskCounter(line_y=120, hysteresis=2, confirm_hits=2, max_step=12)

    counts = [counter.update(frame_id, _mask(y)).total_count
              for frame_id, y in enumerate((108, 116, 124, 132))]

    assert counts == [0, 0, 1, 1]


def test_two_separated_objects_can_cross_on_the_same_frame():
    counter = MaskCounter(line_y=120, hysteresis=2, confirm_hits=2, max_step=12)
    totals = []
    for frame_id, y in enumerate((108, 116, 124)):
        mask = _mask(y, center_x=50) | _mask(y, center_x=250)
        totals.append(counter.update(frame_id, mask).total_count)

    assert totals == [0, 0, 2]


def test_reverse_direction_is_supported_without_double_counting():
    counter = MaskCounter(line_y=120, direction=-1, hysteresis=2,
                          confirm_hits=2, max_step=12)

    totals = [counter.update(frame_id, _mask(y)).total_count
              for frame_id, y in enumerate((132, 124, 116, 108))]

    assert totals == [0, 0, 1, 1]


def test_frame_ids_must_increase_and_objects_born_below_line_do_not_count():
    counter = MaskCounter(line_y=120)
    counter.update(4, _mask(132))
    with pytest.raises(ValueError, match="strictly increase"):
        counter.update(4, _mask(140))

    fresh_counter = MaskCounter(line_y=120, confirm_hits=2, max_step=12)
    for frame_id, y in enumerate((132, 140, 148)):
        result = fresh_counter.update(frame_id, _mask(y))
    assert result.total_count == 0


class MockCmaBuffer:
    def __init__(self, size, physical_address):
        self.data = np.zeros((size,), dtype=np.uint8)
        self.physical_address = physical_address

    @property
    def nbytes(self):
        return self.data.nbytes

    def __array__(self, dtype=None, copy=None):
        array = np.asarray(self.data, dtype=dtype)
        return array.copy() if copy else array

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value


class MockAllocator:
    def __init__(self):
        self.allocations = []

    def __call__(self, *, shape, dtype):
        size = int(np.prod(shape))
        address = 0x10000000 + len(self.allocations) * 0x100000
        buffer = MockCmaBuffer(size, address)
        self.allocations.append((shape, np.dtype(dtype), buffer))
        return buffer


def test_ping_pong_allocates_two_aligned_tx_rx_pairs_once():
    allocator = MockAllocator()

    tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocator)

    assert len(allocator.allocations) == 4
    assert [buffer.nbytes for buffer in tx_buffers] == [TX_BYTES, TX_BYTES]
    assert [buffer.nbytes for buffer in rx_buffers] == [RX_BYTES, RX_BYTES]
    assert all(buffer.physical_address % 4 == 0
               for buffer in tx_buffers + rx_buffers)


def test_async_stream_calls_real_camera_encoder_with_keyword_frame_id():
    allocator = MockAllocator()
    tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocator)

    class ImmediateChannel:
        def __init__(self):
            self.buffer = None
            self.nbytes = None

        def transfer(self, buffer, *, nbytes):
            self.buffer = buffer
            self.nbytes = nbytes

        async def wait_async(self):
            return None

    recv = ImmediateChannel()
    send = ImmediateChannel()
    stream = DoubleBufferStream(
        recv_channel=recv,
        send_channel=send,
        tx_buffers=tx_buffers,
        rx_buffers=rx_buffers,
        counter=MaskCounter(),
        packet_encoder=lambda gray, *, frame_id: pytest.fail("bytes encoder should not run"),
        packet_encoder_into=gray320x240_to_rgb_packet_into,
        stage_timings_ms={"encode": [], "dma": [], "packer_check": [],
                          "roi_count": [], "postprocess": []},
    )

    results = asyncio.run(stream.run(_frames(1)))

    assert results[0].frame_id == 0
    assert send.nbytes == TX_BYTES
    assert np.asarray(send.buffer).tobytes() == gray320x240_to_rgb_packet(
        np.zeros((240, 320), dtype=np.uint8), frame_id=0
    )
    assert len(stream.stage_timings_ms["encode"]) == 1
    assert len(stream.stage_timings_ms["dma"]) == 1
    assert len(stream.stage_timings_ms["roi_count"]) == 1
    assert len(stream.stage_timings_ms["postprocess"]) == 1


class MockRxChannel:
    def __init__(self, owner):
        self.owner = owner
        self.active = False
        self.pending_buffer = None

    def transfer(self, buffer, *, nbytes):
        if self.active:
            raise RuntimeError("simple S2MM channel is already active")
        assert nbytes == RX_BYTES
        self.active = True
        self.pending_buffer = buffer
        self.owner.events.append(("rx-start", buffer.physical_address))

    async def wait_async(self):
        if self.owner.current_frame_id == 1:
            await asyncio.to_thread(self.owner.release_frame1.wait, 1.0)
        else:
            await asyncio.sleep(0.004)
        self.active = False
        self.owner.events.append(("rx-done", self.pending_buffer.physical_address))


class MockTxChannel:
    def __init__(self, owner):
        self.owner = owner
        self.active = False

    def transfer(self, buffer, *, nbytes):
        if self.active:
            raise RuntimeError("simple MM2S channel is already active")
        assert nbytes == TX_BYTES
        self.active = True
        frame_id = int(np.asarray(buffer)[0])
        self.owner.current_frame_id = frame_id
        assert self.owner.recvchannel.pending_buffer is not None
        self.owner.recvchannel.pending_buffer[:] = self.owner.masks[frame_id].reshape(-1)
        self.owner.events.append(("tx-start", frame_id))
        if frame_id == self.owner.fail_on_frame:
            raise RuntimeError("mock MM2S start failure")

    async def wait_async(self):
        await asyncio.sleep(0.004)
        self.active = False
        self.owner.events.append(("tx-done", None))


@dataclass
class MockDma:
    masks: list
    fail_on_frame: int | None = None

    def __post_init__(self):
        import threading
        self.events = []
        self.current_frame_id = None
        self.release_frame1 = threading.Event()
        self.recvchannel = MockRxChannel(self)
        self.sendchannel = MockTxChannel(self)


async def _frames(count):
    for _ in range(count):
        await asyncio.sleep(0)
        yield np.zeros((240, 320), dtype=np.uint8)


def test_async_dma_pipeline_alternates_slots_and_overlaps_processing():
    allocator = MockAllocator()
    tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocator)
    masks = [_mask(108), _mask(116), _mask(124)]
    dma = MockDma(masks)
    processed_while_next_dma_active = []

    def on_frame(result, mask):
        if result.frame_id == 0:
            processed_while_next_dma_active.append(dma.recvchannel.active)
            dma.release_frame1.set()

    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel,
        send_channel=dma.sendchannel,
        tx_buffers=tx_buffers,
        rx_buffers=rx_buffers,
        counter=MaskCounter(line_y=120, confirm_hits=2, max_step=12),
        packet_encoder=lambda gray, *, frame_id: bytes([frame_id]) * TX_BYTES,
        on_frame=on_frame,
        transfer_timeout_s=1.0,
    )

    results = asyncio.run(stream.run(_frames(3)))

    rx_starts = [address for kind, address in dma.events if kind == "rx-start"]
    tx_starts = [frame_id for kind, frame_id in dma.events if kind == "tx-start"]
    assert rx_starts == [rx_buffers[0].physical_address,
                         rx_buffers[1].physical_address,
                         rx_buffers[0].physical_address]
    assert tx_starts == [0, 1, 2]
    assert results[-1].total_count == 1
    assert processed_while_next_dma_active == [True]
    assert all(slot.state is BufferState.FREE for slot in stream.slots)


def test_periodic_preview_does_not_skip_counter_updates():
    allocator = MockAllocator()
    tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocator)
    ys = (108, 112, 116, 120, 124, 128)
    dma = MockDma([_mask(y) for y in ys])
    dma.release_frame1.set()
    hub = MjpegFrameHub()
    publisher = PeriodicMjpegPublisher(hub=hub, line_y=120)
    counter = MaskCounter(line_y=120, confirm_hits=2, max_step=12)
    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel,
        send_channel=dma.sendchannel,
        tx_buffers=tx_buffers,
        rx_buffers=rx_buffers,
        counter=counter,
        packet_encoder=lambda gray, *, frame_id: bytes([frame_id]) * TX_BYTES,
        on_frame=publisher,
        transfer_timeout_s=1.0,
    )

    results = asyncio.run(stream.run(_frames(len(ys))))

    assert len(results) == len(ys)
    assert counter.last_frame_id == len(ys) - 1
    assert results[-1].total_count == 1
    assert publisher.encoded_frames == 2
    assert hub.sequence == 2
    hub.close()


def test_transfer_async_uses_nonblocking_start_and_async_wait_when_available():
    allocator = MockAllocator()
    _, rx_buffers = allocate_ping_pong_buffers(allocator)
    dma = MockDma([np.zeros((240, 320), dtype=np.uint8)])

    async def exercise():
        pending = transfer_async(dma.recvchannel, rx_buffers[0], RX_BYTES)
        assert dma.recvchannel.active
        await pending
        assert not dma.recvchannel.active

    asyncio.run(exercise())


def test_pynq_none_transfer_uses_channel_wait_when_interrupt_is_unconfigured():
    class PynqLikeChannel:
        def __init__(self):
            self._interrupt = None
            self.started = False
            self.wait_calls = 0
            self.wait_async_calls = 0
            self.transfer_return = "not-called"

        def transfer(self, buffer, *, nbytes):
            assert nbytes == RX_BYTES
            self.started = True
            self.transfer_return = None
            return None

        async def wait_async(self):
            self.wait_async_calls += 1
            await self._interrupt.wait()

        def wait(self):
            assert self.started
            self.wait_calls += 1
            self.started = False

    allocator = MockAllocator()
    _, rx_buffers = allocate_ping_pong_buffers(allocator)
    channel = PynqLikeChannel()

    async def exercise():
        pending = transfer_async(channel, rx_buffers[0], RX_BYTES)
        assert channel.transfer_return is None
        await pending

    asyncio.run(exercise())

    assert channel.wait_calls == 1
    assert channel.wait_async_calls == 0
    assert not channel.started







def test_non_binary_mask_is_rejected_before_tracking_state_changes():
    counter = MaskCounter()
    invalid = np.zeros((240, 320), dtype=np.uint8)
    invalid[120, 80] = 1

    with pytest.raises(ValueError, match="Binary8"):
        counter.update(0, invalid)
    assert counter.last_frame_id is None
    assert counter.total_count == 0


def test_transfer_async_falls_back_to_channel_wait_in_a_worker_thread():
    import threading

    class BlockingWaitChannel:
        def __init__(self):
            self.started = False
            self.wait_thread = None
            self.main_thread = threading.get_ident()

        def transfer(self, buffer, *, nbytes):
            assert nbytes == RX_BYTES
            self.started = True

        def wait(self):
            self.wait_thread = threading.get_ident()
            self.started = False

    allocator = MockAllocator()
    _, rx_buffers = allocate_ping_pong_buffers(allocator)
    channel = BlockingWaitChannel()

    async def exercise():
        await transfer_async(channel, rx_buffers[0], RX_BYTES)

    asyncio.run(exercise())

    assert channel.wait_thread != channel.main_thread
    assert not channel.started


def test_dma_start_failure_holds_owned_slot_and_does_not_retry():
    allocator = MockAllocator()
    tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocator)
    dma = MockDma([_mask(108), _mask(116), _mask(124)], fail_on_frame=1)
    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel,
        send_channel=dma.sendchannel,
        tx_buffers=tx_buffers,
        rx_buffers=rx_buffers,
        counter=MaskCounter(line_y=120, max_step=12),
        packet_encoder=lambda gray, *, frame_id: bytes([frame_id]) * TX_BYTES,
        transfer_timeout_s=1.0,
    )

    with pytest.raises(RuntimeError, match="mock MM2S start failure"):
        asyncio.run(stream.run(_frames(3)))

    assert len([event for event in dma.events if event[0] == "rx-start"]) == 2
    assert len([event for event in dma.events if event[0] == "tx-start"]) == 2
    assert stream.held_slots
    assert all(slot.state is BufferState.HELD for slot in stream.held_slots)




def test_frame_callbacks_arm_before_dma_and_validate_completed_output():
    allocator = MockAllocator()
    tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocator)
    dma = MockDma([_mask(108)])

    def arm(frame_id, slot):
        dma.events.append(("arm", frame_id))

    def validate(frame_id, slot):
        assert not dma.recvchannel.active
        assert not dma.sendchannel.active
        dma.events.append(("validated", frame_id))

    def processed(result, mask):
        dma.events.append(("processed", result.frame_id))

    stage_timings_ms = {name: [] for name in (
        "packer_check", "roi_count", "postprocess")}
    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel,
        send_channel=dma.sendchannel,
        tx_buffers=tx_buffers,
        rx_buffers=rx_buffers,
        counter=MaskCounter(line_y=120, max_step=12),
        packet_encoder=lambda gray, *, frame_id: bytes([frame_id]) * TX_BYTES,
        before_transfer=arm,
        after_transfer=validate,
        on_frame=processed,
        stage_timings_ms=stage_timings_ms,
        transfer_timeout_s=1.0,
    )

    asyncio.run(stream.run(_frames(1)))

    names = [event[0] for event in dma.events]
    assert names.index("arm") < names.index("rx-start") < names.index("tx-start")
    assert names.index("rx-done") < names.index("validated")
    assert names.index("tx-done") < names.index("validated") < names.index("processed")
    assert all(len(samples) == 1 and samples[0] >= 0
               for samples in stage_timings_ms.values())

def test_timed_out_blocking_wait_does_not_block_asyncio_shutdown():
    import threading
    import time

    release = threading.Event()
    channel = type("HungWaitChannel", (), {})()
    channel.transfer = lambda buffer, *, nbytes: None
    channel.wait = release.wait
    allocator = MockAllocator()
    _, rx_buffers = allocate_ping_pong_buffers(allocator)
    timer = threading.Timer(0.35, release.set)
    timer.daemon = True
    timer.start()
    started = time.perf_counter()

    async def exercise():
        pending = transfer_async(channel, rx_buffers[0], RX_BYTES)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(pending, timeout=0.02)

    asyncio.run(exercise())
    elapsed = time.perf_counter() - started
    release.set()
    timer.join(timeout=0.1)

    assert elapsed < 0.25


def test_roi_row_and_column_projections_match_foreground_pixels():
    counter = MaskCounter(line_y=120, roi_half_height=10, min_area=12)
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[118:123, 78:83] = 0xFF
    mask[40:45, 78:83] = 0xFF  # outside the count-line ROI

    result = counter.update(0, mask)

    assert result.roi_top == 110
    assert result.roi_bottom == 131
    assert sum(result.row_projection) == 25
    assert sum(result.column_projection) == 25
    assert result.row_projection[10] == 5
    assert result.column_projection[80] == 5
