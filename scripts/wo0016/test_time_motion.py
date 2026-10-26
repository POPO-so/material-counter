"""Opt-in timestamp gating must leave the legacy frame mode untouched."""
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stream_counter import MaskCounter


def mask(x):
    image = np.zeros((240, 320), np.uint8)
    image[100:108, x:x + 8] = 255
    return image


def counter(**kwargs):
    return MaskCounter(count_axis="x", roi_mode="full", line_x=160,
                       max_step=20, max_lateral_step=10, strict_lateral_gate=True,
                       **kwargs)


def test_time_gating_accepts_observed_displacement_at_twice_the_interval():
    frame_mode = counter()
    time_mode = counter(motion_timebase_s=0.05)
    for i, (x, elapsed) in enumerate([(125, 0), (140, 50_000_000), (170, 150_000_000)]):
        old = frame_mode.update(i, mask(x))
        new = time_mode.update(i, mask(x), capture_monotonic_ns=1_000_000_000 + elapsed)
    assert old.total_count == 0
    assert new.total_count == 1
    assert new.events[0].track_id == 1
    assert len(new.tracks) == 1


def test_default_motion_mode_ignores_optional_timestamps():
    plain, timestamped = counter(), counter()
    for i, x in enumerate([125, 140, 155, 170]):
        assert plain.update(i, mask(x)) == timestamped.update(i, mask(x), capture_monotonic_ns=99)


def test_time_mode_rejects_missing_or_nonincreasing_time_before_state_changes():
    tracker = counter(motion_timebase_s=0.05)
    with pytest.raises(ValueError, match="capture_monotonic_ns"):
        tracker.update(0, mask(125))
    assert tracker.last_frame_id is None
    tracker.update(0, mask(125), capture_monotonic_ns=1_000_000_000)
    for value in [999_999_999, 1_000_000_000, True, 1.5]:
        with pytest.raises(ValueError, match="capture_monotonic_ns"):
            tracker.update(1, mask(140), capture_monotonic_ns=value)
        assert tracker.last_frame_id == 0


def test_time_mode_equals_frame_mode_at_regular_intervals():
    plain, timed = counter(), counter(motion_timebase_s=0.05)
    for i, x in enumerate([125, 140, 155, 170]):
        assert plain.update(i, mask(x)) == timed.update(i, mask(x), capture_monotonic_ns=(i + 1) * 50_000_000)


def test_dma_slots_forward_their_own_capture_times_without_recording():
    import asyncio
    from test_stream_counter import MockAllocator, MockDma
    from stream_counter import DoubleBufferStream, FrameInput, TX_BYTES, allocate_ping_pong_buffers

    masks = [mask(x) for x in (125, 140, 170)]
    times = [1_000_000_000, 1_050_000_000, 1_150_000_000]
    dma = MockDma(masks)
    tx, rx = allocate_ping_pong_buffers(MockAllocator())
    tracker = counter(motion_timebase_s=0.05)

    async def source():
        for i, image in enumerate(masks):
            yield FrameInput(image, i, times[i], 0, 0.0)

    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel, send_channel=dma.sendchannel,
        tx_buffers=tx, rx_buffers=rx, counter=tracker,
        packet_encoder=lambda image, *, frame_id: bytes([frame_id]) * TX_BYTES,
        on_frame=lambda result, image: dma.release_frame1.set() if result.frame_id == 0 else None,
    )
    results = asyncio.run(stream.run(source()))
    assert results[-1].total_count == 1
    assert results[-1].events[0].track_id == 1
    assert stream.slots[0].capture_monotonic_ns == times[2]
    assert stream.slots[1].capture_monotonic_ns == times[1]
