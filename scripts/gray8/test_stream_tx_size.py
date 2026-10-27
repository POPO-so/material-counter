import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wo0016"))
import stream_counter as stream  # noqa: E402


GRAY_TX_BYTES = 76832


class Buffer(np.ndarray):
    def __new__(cls, nbytes, address):
        obj = np.zeros(nbytes, dtype=np.uint8).view(cls)
        obj.physical_address = address
        return obj


def test_gray8_tx_size_allocates_validates_encodes_and_transfers_exact_length():
    allocated = []

    def allocate(*, shape, dtype):
        address = 0x100000 + len(allocated) * 0x100000
        buffer = Buffer(shape[0], address)
        allocated.append(buffer)
        return buffer

    tx, rx = stream.allocate_ping_pong_buffers(allocate, tx_bytes=GRAY_TX_BYTES)
    assert [len(buf) for buf in tx] == [GRAY_TX_BYTES, GRAY_TX_BYTES]
    assert [len(buf) for buf in rx] == [stream.RX_BYTES, stream.RX_BYTES]
    sent = []

    class Channel:
        def __init__(self, record=False):
            self.record = record
        def transfer(self, buffer, *, nbytes):
            if self.record:
                sent.append((buffer, nbytes))
        def wait(self):
            pass

    import asyncio
    async def frames():
        yield np.full((stream.HEIGHT, stream.WIDTH), 42, dtype=np.uint8)

    gray = np.full((stream.HEIGHT, stream.WIDTH), 42, dtype=np.uint8)
    encoder = lambda frame, *, frame_id: bytes([frame_id & 0xff]) * GRAY_TX_BYTES
    counter = stream.MaskCounter()
    pipeline = stream.DoubleBufferStream(
        recv_channel=Channel(), send_channel=Channel(record=True), tx_buffers=tx, rx_buffers=rx,
        counter=counter, packet_encoder=encoder, tx_bytes=GRAY_TX_BYTES,
    )
    asyncio.run(pipeline.run(frames()))
    assert sent and all(nbytes == GRAY_TX_BYTES for _, nbytes in sent)


def test_default_tx_size_is_canonical_gray8_length():
    assert stream.TX_BYTES == 76832
    assert "tx_bytes" in __import__("inspect").signature(stream.allocate_ping_pong_buffers).parameters
    assert "tx_bytes" in __import__("inspect").signature(stream.DoubleBufferStream).parameters
    allocated = []
    def allocate(*, shape, dtype):
        buffer = Buffer(shape[0], 0x200000 + len(allocated) * 0x100000)
        allocated.append(buffer)
        return buffer
    tx, _ = stream.allocate_ping_pong_buffers(allocate)
    assert [len(buffer) for buffer in tx] == [76832, 76832]

