"""Regression checks for shared capture/preview buffer ownership."""
import asyncio
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_stream_counter import MockAllocator, MockDma
from stream_counter import DoubleBufferStream, MaskCounter, TX_BYTES, allocate_ping_pong_buffers


def test_capture_and_preview_share_one_gray_snapshot_write(monkeypatch):
    tx, rx = allocate_ping_pong_buffers(MockAllocator())
    mask = np.zeros((240, 320), np.uint8)
    dma = MockDma([mask])
    gray = np.full_like(mask, 123)
    captured, previewed, writes = [], [], []
    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel, send_channel=dma.sendchannel,
        tx_buffers=tx, rx_buffers=rx, counter=MaskCounter(),
        packet_encoder=lambda image, *, frame_id: bytes(TX_BYTES),
        capture_callback=lambda result, slot: captured.append(slot.capture_gray.copy()),
        preview_callback=lambda result, mask, image: previewed.append(image.copy()),
    )
    snapshot_ids = {id(slot.capture_gray) for slot in stream.slots}
    original_copyto = np.copyto

    def spy(destination, source, **kwargs):
        if id(destination) in snapshot_ids:
            writes.append(id(destination))
        return original_copyto(destination, source, **kwargs)

    monkeypatch.setattr(np, "copyto", spy)

    async def frames():
        yield gray

    asyncio.run(stream.run(frames()))
    assert len(writes) == 1
    np.testing.assert_array_equal(captured[0], gray)
    np.testing.assert_array_equal(previewed[0], gray)
    assert not np.shares_memory(captured[0], gray)
