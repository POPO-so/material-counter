import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))


class Buffer:
    def __init__(self):
        self.invalidations = 0

    def invalidate(self):
        self.invalidations += 1


class Driver:
    def __init__(self, direction):
        self._offset = 0x30 if direction == "rx" else 0
        self._flush_before = direction == "tx"
        self._active_buffer = None
        self.transferred = 0
        self.starts = []

    def transfer(self, buffer, *, nbytes):
        self.starts.append(nbytes)
        self._active_buffer = buffer


def polling(driver, read, **kwargs):
    from dma_polling import CooperativeDmaChannel
    return CooperativeDmaChannel(driver, read=read, **kwargs)


def test_receive_wait_yields_and_invalidates_once_without_threads():
    async def exercise():
        driver = Driver("rx")
        buffer = Buffer()
        polls = 0
        heartbeat = False

        def read(offset):
            nonlocal polls
            if offset == 0x34:
                polls += 1
                return 0x1002 if heartbeat else 0
            assert offset == 0x58
            return 76800

        channel = polling(driver, read)
        channel.transfer(buffer, nbytes=76800)

        async def other_task():
            nonlocal heartbeat
            heartbeat = True

        await asyncio.gather(channel.wait_async(), other_task())
        assert polls >= 2
        assert buffer.invalidations == 1
        assert driver.transferred == 76800
        assert driver._active_buffer is None
        assert driver.starts == [76800]
        assert not hasattr(channel, "wait")
        assert channel.timings[0]["polls"] == polls
        assert channel.timings[0]["cpu_ms"] >= 0

    asyncio.run(exercise())


def test_send_completion_does_not_invalidate_input():
    driver = Driver("tx")
    buffer = Buffer()
    channel = polling(driver, lambda offset: 2 if offset == 4 else 76832)
    channel.transfer(buffer, nbytes=76832)
    asyncio.run(channel.wait_async())
    assert buffer.invalidations == 0
    assert driver.transferred == 76832


@pytest.mark.parametrize("status", [1, 0x12, 0x22, 0x42])
def test_dma_fault_preserves_buffer_ownership(status):
    driver = Driver("rx")
    buffer = Buffer()
    channel = polling(driver, lambda offset: status)
    channel.transfer(buffer, nbytes=76800)
    with pytest.raises(RuntimeError, match="DMA"):
        asyncio.run(channel.wait_async())
    assert driver._active_buffer is buffer
    assert buffer.invalidations == 0


def test_cancelled_wait_does_not_touch_or_release_active_buffer():
    async def exercise():
        driver = Driver("rx")
        buffer = Buffer()
        channel = polling(driver, lambda offset: 0)
        channel.transfer(buffer, nbytes=76800)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(channel.wait_async(), timeout=0.005)
        assert driver._active_buffer is buffer
        assert buffer.invalidations == 0
        assert driver.transferred == 0

    asyncio.run(exercise())


def test_wrong_received_length_is_rejected():
    driver = Driver("rx")
    channel = polling(driver, lambda offset: 2 if offset == 0x34 else 10)
    channel.transfer(Buffer(), nbytes=76800)
    with pytest.raises(RuntimeError, match="length"):
        asyncio.run(channel.wait_async())
    assert driver._active_buffer is not None


def test_repeated_transfers_reuse_channel_and_reset_ownership():
    driver = Driver("rx")
    channel = polling(driver, lambda offset: 2 if offset == 0x34 else 76800)
    for _ in range(3):
        buffer = Buffer()
        channel.transfer(buffer, nbytes=76800)
        asyncio.run(channel.wait_async())
        assert buffer.invalidations == 1
    assert driver.starts == [76800] * 3
    assert len(channel.timings) == 3


def test_unsupported_driver_and_invalid_interval_rejected_before_transfer():
    with pytest.raises(TypeError, match="PYNQ"):
        polling(SimpleNamespace(), lambda offset: 0)
    for interval in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="interval"):
            polling(Driver("rx"), lambda offset: 0, interval_s=interval)


@pytest.mark.parametrize("stall", [False, True])
def test_polling_integrates_with_double_buffer_stream_and_fail_hold(stall):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wo0016"))
    from stream_counter import DoubleBufferStream, MaskCounter, TX_BYTES, RX_BYTES
    from packet_codec import gray320x240_to_gray_packet, validate_packet, binary_golden

    class Cma(np.ndarray):
        def invalidate(self):
            self.invalidations += 1

    def buffer(size, address):
        result = np.zeros(size, np.uint8).view(Cma)
        result.physical_address = address
        result.invalidations = 0
        return result

    tx = tuple(buffer(TX_BYTES, 0x10000000 + i * 0x100000) for i in range(2))
    rx = tuple(buffer(RX_BYTES, 0x11000000 + i * 0x100000) for i in range(2))
    send, receive = Driver("tx"), Driver("rx")
    original_start = send.transfer

    def start(buffer, *, nbytes):
        assert receive._active_buffer is not None
        original_start(buffer, nbytes=nbytes)
        receive._active_buffer[:] = binary_golden(validate_packet(buffer)).reshape(-1)

    send.transfer = start

    def read(offset):
        if offset in (4, 0x34):
            return 0 if stall else 2
        return TX_BYTES if offset == 0x28 else RX_BYTES

    stream = DoubleBufferStream(
        recv_channel=polling(receive, read), send_channel=polling(send, read),
        tx_buffers=tx, rx_buffers=rx, counter=MaskCounter(),
        packet_encoder=gray320x240_to_gray_packet, transfer_timeout_s=0.02)

    async def frames():
        for _ in range(4):
            yield np.zeros((240, 320), np.uint8)

    if stall:
        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(stream.run(frames()))
        assert stream.dma_may_be_active
        assert stream.held_slots
        assert receive._active_buffer is not None
        assert all(b.invalidations == 0 for b in rx)
    else:
        results = asyncio.run(stream.run(frames()))
        assert len(results) == 4
        assert not stream.dma_may_be_active
        assert sum(b.invalidations for b in rx) == 4
        assert all(r.total_count == 0 for r in results)
