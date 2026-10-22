import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
import threading

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wo0015"))

from camera_adapter import gray320x240_to_rgb_packet
from mjpeg_preview import MjpegFrameHub, PeriodicMjpegPublisher
from packet_encoder import gray320x240_to_rgb_packet_into
from stream_counter import (
    BufferState,
    Detection,
    DoubleBufferStream,
    FrameInput,
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
    assert result.roi_foreground_pixels == 34
    assert result.ccl_executed is True


def test_glare_component_limits_are_configurable_and_validate_boundaries():
    defaults = MaskCounter()
    assert defaults.max_area == 900
    assert defaults.max_bbox_width == 85
    assert defaults.max_bbox_height == 85

    configured = MaskCounter(
        min_area=5, max_area=1200, max_bbox_width=90, max_bbox_height=70
    )
    assert configured.max_area == 1200
    assert configured.max_bbox_width == 90
    assert configured.max_bbox_height == 70
    assert MaskCounter(max_area=None).max_area is None

    for options in (
        {"max_area": 11},
        {"max_area": 0},
        {"max_bbox_width": 0},
        {"max_bbox_height": -1},
        {"max_bbox_width": 12.5},
        {"max_bbox_height": True},
    ):
        with pytest.raises(ValueError):
            MaskCounter(**options)


def test_component_limit_boundaries_include_exact_maximum_area_and_bbox_sizes():
    counter = MaskCounter(line_y=120, roi_half_height=60)
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[70:100, 10:40] = 0xFF          # area 900, exact max_area
    mask[110:120, 60:145] = 0xFF        # width 85, exact max_bbox_width
    mask[70:155, 160:170] = 0xFF        # height 85, exact max_bbox_height
    mask[112:119, 200:207] = 0xFF       # normal nut

    result = counter.update(0, mask)

    assert sorted(d.area for d in result.raw_detections) == [49, 850, 850, 900]
    assert counter.glare_rejected_count == 0
    assert counter.glare_area_px == 0
    assert counter.rejected_detections == ()
    assert counter.rejected_contours == ()


def test_oversized_blobs_are_removed_before_tracking_and_exposed_for_hud():
    counter = MaskCounter(
        line_y=120,
        roi_half_height=60,
        max_area=900,
        max_bbox_width=85,
        max_bbox_height=85,
        component_cluster_gap=12,
    )
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[70:100, 40:71] = 0xFF          # area 930, over max_area
    mask[112:119, 40:47] = 0xFF         # normal nut, close to the glare blob
    mask[110:120, 200:286] = 0xFF       # width 86, over max_bbox_width
    mask[70:156, 290:300] = 0xFF        # height 86, over max_bbox_height

    result = counter.update(0, mask)

    # The close nut stays separate because the oversized neighbor is removed
    # before component clustering and tracking.
    assert [d.area for d in result.raw_detections] == [49]
    assert [d.area for d in result.detections] == [49]
    assert len(result.tracks) == 1
    assert counter.glare_rejected_count == 3
    assert counter.glare_area_px == 930 + 860 + 860
    assert [d.area for d in counter.rejected_detections] == [930, 860, 860]
    assert len(counter.rejected_contours) == counter.glare_rejected_count
    assert all(contour.ndim == 3 and contour.shape[1:] == (1, 2)
               for contour in counter.rejected_contours)
    assert all(int(contour[:, 0, 1].min()) >= result.roi_top
               for contour in counter.rejected_contours)
    for detection, contour in zip(counter.rejected_detections, counter.rejected_contours):
        assert int(contour[:, 0, 0].min()) == detection.x_min
        assert int(contour[:, 0, 0].max()) == detection.x_max
        assert int(contour[:, 0, 1].min()) == detection.y_min
        assert int(contour[:, 0, 1].max()) == detection.y_max

    # Diagnostics are per-frame, including frames with no rejected components.
    empty = counter.update(1, np.zeros_like(mask))
    assert not empty.raw_detections
    assert counter.glare_rejected_count == 0
    assert counter.glare_area_px == 0
    assert counter.rejected_detections == ()
    assert counter.rejected_contours == ()


def test_recorded_detections_apply_the_same_component_limits():
    counter = MaskCounter(max_area=900, max_bbox_width=85, max_bbox_height=85)
    normal = Detection(10, 16, 110, 116, 49, 13.0, 113.0)
    oversized = Detection(100, 130, 100, 129, 901, 115.0, 114.0)

    result = counter.update_detections(0, [normal, oversized])

    assert result.raw_detections == (normal,)
    assert result.detections == (normal,)
    assert counter.glare_rejected_count == 1
    assert counter.glare_area_px == 901
    assert counter.rejected_detections == (oversized,)
    assert counter.rejected_contours == ()


def _legacy_roi_signature(mask, counter):
    top = max(0, counter.line_y - counter.roi_half_height)
    bottom = min(counter.height, counter.line_y + counter.roi_half_height + 1)
    foreground = mask[top:bottom, :] == 0xFF
    row_projection = tuple(int(value) for value in np.count_nonzero(foreground, axis=1))
    column_projection = tuple(int(value) for value in np.count_nonzero(foreground, axis=0))
    roi = np.ascontiguousarray(foreground, dtype=np.uint8)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(
        roi, connectivity=8, ltype=cv2.CV_32S
    )
    detections = []
    for label in range(1, count):
        x, y, box_width, box_height, area = (int(value) for value in stats[label])
        if area >= counter.min_area:
            detections.append((
                x, x + box_width - 1, y + top, y + top + box_height - 1, area,
                float(centroids[label, 0]), float(centroids[label, 1] + top),
            ))
    detections.sort(key=lambda item: (item[5], item[6], item[2], item[0]))
    return tuple(detections), row_projection, column_projection


class LegacyCclMaskCounter(MaskCounter):
    """Reference counter using the pre-optimization CV_32S ROI implementation."""

    def _roi_components(self, roi, top, foreground_pixels):
        if foreground_pixels < self.min_area:
            return []
        foreground = np.ascontiguousarray(roi == 0xFF, dtype=np.uint8)
        count, _, stats, centroids = cv2.connectedComponentsWithStats(
            foreground, connectivity=8, ltype=cv2.CV_32S
        )
        detections = []
        for label in range(1, count):
            x, y, box_width, box_height, area = (int(value) for value in stats[label])
            if area < self.min_area:
                continue
            detections.append(Detection(
                x_min=x,
                x_max=x + box_width - 1,
                y_min=y + top,
                y_max=y + top + box_height - 1,
                area=area,
                centroid_x=float(centroids[label, 0]),
                centroid_y=float(centroids[label, 1] + top),
            ))
        detections.sort(key=lambda d: (d.centroid_x, d.centroid_y, d.y_min, d.x_min))
        return detections


def test_cached_roi_outputs_match_legacy_results_on_synthetic_and_board_mask_samples():
    synthetic = np.zeros((240, 320), dtype=np.uint8)
    synthetic[75:81, 21:29] = 0xFF
    synthetic[112:119, 90:97] = 0xFF
    synthetic[120:125, 210:216] = 0xFF
    synthetic[232:240, 300:320] = 0xFF
    real_sample_path = (Path(__file__).resolve().parents[2]
                        / "data/results/wo0015-final-20261017/pl-binary8-output.png")
    real_sample = cv2.imread(str(real_sample_path), cv2.IMREAD_GRAYSCALE)
    assert real_sample is not None
    assert real_sample.shape == (240, 320)
    assert set(np.unique(real_sample)) <= {0, 0xFF}

    for sample in (synthetic, real_sample):
        counter = MaskCounter(
            line_y=120, roi_half_height=24, min_area=12,
            max_area=None, max_bbox_width=320, max_bbox_height=240,
        )
        expected = _legacy_roi_signature(sample, counter)
        actual = counter._extract_roi_detections(np.array(sample, copy=True))
        actual_signature = (
            tuple((d.x_min, d.x_max, d.y_min, d.y_max, d.area,
                   d.centroid_x, d.centroid_y) for d in actual[0]),
            actual[3],
            actual[4],
        )
        assert actual_signature == expected

    reference_counter = LegacyCclMaskCounter(
        line_y=120, roi_half_height=24, confirm_hits=2, max_step=12
    )
    cached_counter = MaskCounter(line_y=120, confirm_hits=2, max_step=12)
    crossing_sequence = [_mask(y) for y in (108, 116, 124, 132)]
    for frame_id, sample in enumerate(crossing_sequence):
        expected = reference_counter.update(frame_id, sample)
        actual = cached_counter.update(frame_id, np.array(sample, copy=True))
        assert actual == expected
        assert actual.total_count == expected.total_count


def test_roi_passes_cached_uint8_view_to_fast_connected_components(monkeypatch):
    mask = _mask(120, center_x=100, size=7)
    observed = {}
    fast_ccl = getattr(cv2, "connectedComponentsWithStatsWithAlgorithm", None)
    fast_algorithm = getattr(cv2, "CCL_GRANA", None)

    if callable(fast_ccl) and fast_algorithm is not None:
        def capture(image, connectivity, ltype, ccltype):
            observed.update(image=image, connectivity=connectivity,
                            ltype=ltype, ccltype=ccltype)
            return fast_ccl(image, connectivity, ltype, ccltype)

        monkeypatch.setattr(cv2, "connectedComponentsWithStatsWithAlgorithm", capture)
    else:
        default_ccl = cv2.connectedComponentsWithStats

        def capture(image, *args, **kwargs):
            observed.update(image=image, args=args, kwargs=kwargs)
            return default_ccl(image, *args, **kwargs)

        monkeypatch.setattr(cv2, "connectedComponentsWithStats", capture)

    counter = MaskCounter(line_y=120, roi_half_height=24, min_area=12)
    counter._extract_roi_detections(mask)

    assert observed["image"].dtype == np.uint8
    assert observed["image"].shape == (49, 320)
    assert np.shares_memory(observed["image"], mask)
    if fast_algorithm is not None and callable(fast_ccl):
        assert observed["connectivity"] == 8
        assert observed["ltype"] == cv2.CV_16U
        assert observed["ccltype"] == fast_algorithm


def test_default_line_roi_is_narrow_and_empty_band_skips_connected_components(monkeypatch):
    counter = MaskCounter()
    assert counter.roi_half_height == 24
    mask = np.zeros((240, 320), dtype=np.uint8)
    def forbidden_ccl(*args, **kwargs):
        pytest.fail("an empty line band must short-circuit before CCL")

    monkeypatch.setattr(cv2, "connectedComponentsWithStats", forbidden_ccl)
    fast_ccl = getattr(cv2, "connectedComponentsWithStatsWithAlgorithm", None)
    if callable(fast_ccl):
        monkeypatch.setattr(cv2, "connectedComponentsWithStatsWithAlgorithm", forbidden_ccl)

    detections, top, bottom, row_projection, column_projection = (
        counter._extract_roi_detections(mask)
    )

    assert (top, bottom) == (96, 145)
    assert len(row_projection) == 49
    assert not detections
    assert row_projection == (0,) * 49
    assert column_projection == (0,) * 320


def test_small_total_foreground_short_circuits_without_changing_projections(monkeypatch):
    counter = MaskCounter(line_y=120, roi_half_height=24, min_area=12)
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[118, 20:31] = 0xFF

    def forbidden_ccl(*args, **kwargs):
        pytest.fail("foreground below min_area must short-circuit before CCL")

    monkeypatch.setattr(cv2, "connectedComponentsWithStats", forbidden_ccl)
    fast_ccl = getattr(cv2, "connectedComponentsWithStatsWithAlgorithm", None)
    if callable(fast_ccl):
        monkeypatch.setattr(cv2, "connectedComponentsWithStatsWithAlgorithm", forbidden_ccl)

    detections, top, bottom, row_projection, column_projection = (
        counter._extract_roi_detections(mask)
    )

    assert (top, bottom) == (96, 145)
    assert not detections
    assert row_projection[22] == 11
    assert sum(row_projection) == 11
    assert sum(column_projection) == 11


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


class ReadCountingCmaBuffer(MockCmaBuffer):
    def __init__(self, size, physical_address):
        super().__init__(size, physical_address)
        self.array_reads = 0

    def __array__(self, dtype=None, copy=None):
        self.array_reads += 1
        return super().__array__(dtype=dtype, copy=copy)


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


def test_s2mm_mask_is_copied_before_pack_check_and_roi_uses_cached_storage():
    allocator = MockAllocator()

    def allocate(*, shape, dtype):
        size = int(np.prod(shape))
        address = 0x10000000 + len(allocator.allocations) * 0x100000
        buffer = ReadCountingCmaBuffer(size, address)
        allocator.allocations.append((shape, np.dtype(dtype), buffer))
        return buffer

    tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocate)
    source_mask = _mask(116)
    dma = MockDma([source_mask])
    processed_masks = []
    callback_order = []

    class InputCheckingCounter(MaskCounter):
        def __init__(self):
            super().__init__()
            self.input_masks = []

        def update(self, frame_id, mask):
            self.input_masks.append(mask)
            return super().update(frame_id, mask)

    counter = InputCheckingCounter()

    def sync_completed_frame(frame_id, slot):
        callback_order.append("sync")

    def validate_completed_frame(frame_id, slot):
        callback_order.append("packer-check")
        assert isinstance(slot.cached_mask, np.ndarray)
        assert slot.cached_mask.dtype == np.uint8
        assert slot.cached_mask.shape == (240, 320)
        assert slot.cached_mask.flags.c_contiguous
        assert slot.cached_mask.flags.writeable
        assert np.array_equal(slot.cached_mask, source_mask)
        assert not np.shares_memory(slot.cached_mask, slot.rx_buffer.data)
        assert slot.rx_buffer.array_reads == 1

    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel,
        send_channel=dma.sendchannel,
        tx_buffers=tx_buffers,
        rx_buffers=rx_buffers,
        counter=counter,
        packet_encoder=lambda gray, *, frame_id: bytes([frame_id]) * TX_BYTES,
        before_rx_copy=sync_completed_frame,
        after_transfer=validate_completed_frame,
        on_frame=lambda result, mask: processed_masks.append(mask),
        transfer_timeout_s=1.0,
    )
    rx_buffers[0].array_reads = 0

    asyncio.run(stream.run(_frames(1)))

    assert len(counter.input_masks) == 1
    assert callback_order == ["sync", "packer-check"]
    assert counter.input_masks[0] is stream.slots[0].cached_mask
    assert processed_masks[0] is stream.slots[0].cached_mask
    assert rx_buffers[0].array_reads == 1


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
    frame1_started = threading.Event()
    processed_while_next_dma_active = []
    original_tx_transfer = dma.sendchannel.transfer

    def signal_next_dma_start(buffer, *, nbytes):
        if int(np.asarray(buffer)[0]) == 1:
            frame1_started.set()
        return original_tx_transfer(buffer, nbytes=nbytes)

    dma.sendchannel.transfer = signal_next_dma_start

    class OverlapCounter(MaskCounter):
        def update(self, frame_id, mask):
            if frame_id == 0 and not frame1_started.wait(timeout=1.0):
                raise TimeoutError("next DMA did not start during frame-zero postprocess")
            return super().update(frame_id, mask)

    def on_frame(result, mask):
        if result.frame_id == 0:
            processed_while_next_dma_active.append(dma.recvchannel.active)
            dma.release_frame1.set()

    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel,
        send_channel=dma.sendchannel,
        tx_buffers=tx_buffers,
        rx_buffers=rx_buffers,
        counter=OverlapCounter(line_y=120, confirm_hits=2, max_step=12),
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


def test_consumer_failure_unblocks_slot_wait_and_closes_frame_source():
    counter_entered = threading.Event()
    release_counter = threading.Event()
    allocator = MockAllocator()
    tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocator)
    dma = MockDma([_mask(108), _mask(116), _mask(124)])
    dma.release_frame1.set()

    class FailingCounter(MaskCounter):
        def update(self, frame_id, mask):
            if frame_id == 0:
                counter_entered.set()
                if not release_counter.wait(timeout=1.0):
                    raise TimeoutError("test did not release the counter")
                raise RuntimeError("injected counter failure")
            return super().update(frame_id, mask)

    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel,
        send_channel=dma.sendchannel,
        tx_buffers=tx_buffers,
        rx_buffers=rx_buffers,
        counter=FailingCounter(line_y=120),
        packet_encoder=lambda gray, *, frame_id: bytes([frame_id]) * TX_BYTES,
        transfer_timeout_s=1.0,
    )

    async def exercise():
        third_frame_requested = asyncio.Event()
        free_slot_wait_started = asyncio.Event()
        source_closed = asyncio.Event()
        original_get = stream._free_slots.get

        async def observe_free_slot_wait():
            if stream._free_slots.empty():
                free_slot_wait_started.set()
            return await original_get()

        stream._free_slots.get = observe_free_slot_wait

        async def frames():
            try:
                for frame_id in range(3):
                    if frame_id == 2:
                        third_frame_requested.set()
                    yield np.zeros((240, 320), dtype=np.uint8)
            finally:
                source_closed.set()

        run_task = asyncio.create_task(stream.run(frames()))
        assert await asyncio.to_thread(counter_entered.wait, 1.0)
        await asyncio.wait_for(third_frame_requested.wait(), timeout=1.0)
        await asyncio.wait_for(free_slot_wait_started.wait(), timeout=1.0)
        release_counter.set()
        with pytest.raises(RuntimeError, match="injected counter failure"):
            await asyncio.wait_for(run_task, timeout=1.0)
        assert source_closed.is_set()

    asyncio.run(exercise())


def test_consumer_failure_cancels_pending_frame_read_without_masking_error():
    source_wait_started = asyncio.Event()
    source_closed = asyncio.Event()
    allocator = MockAllocator()
    tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocator)
    dma = MockDma([_mask(108)])
    dma.release_frame1.set()

    class FailingCounter(MaskCounter):
        def update(self, frame_id, mask):
            raise RuntimeError("injected counting failure")

    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel,
        send_channel=dma.sendchannel,
        tx_buffers=tx_buffers,
        rx_buffers=rx_buffers,
        counter=FailingCounter(line_y=120),
        packet_encoder=lambda gray, *, frame_id: bytes([frame_id]) * TX_BYTES,
        transfer_timeout_s=0.5,
    )

    async def frames():
        try:
            yield np.zeros((240, 320), dtype=np.uint8)
            source_wait_started.set()
            await asyncio.Event().wait()
        finally:
            source_closed.set()
            raise RuntimeError("injected source cleanup failure")

    async def exercise():
        run_task = asyncio.create_task(stream.run(frames()))
        await asyncio.wait_for(source_wait_started.wait(), timeout=1.0)
        with pytest.raises(RuntimeError, match="injected counting failure") as failure:
            await asyncio.wait_for(run_task, timeout=1.0)
        assert source_closed.is_set()
        notes = getattr(failure.value, "__notes__", ())
        cleanup_errors = getattr(failure.value, "cleanup_errors", ())
        assert notes or cleanup_errors
        assert any("source cleanup failure" in str(error) for error in (notes or cleanup_errors))

    asyncio.run(exercise())


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
    assert publisher.encoded_frames == 0
    assert hub.sequence == 0
    publisher.close()
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


def test_update_validates_only_the_counting_roi_and_reports_subtimings(monkeypatch):
    counter = MaskCounter(line_y=120, roi_half_height=24)
    mask = np.zeros((240, 320), dtype=np.uint8)
    mask[10, 10] = 1  # Outside the ROI and irrelevant to line counting.

    def forbidden_ccl(*args, **kwargs):
        pytest.fail("a below-threshold ROI must skip connected components")

    monkeypatch.setattr(cv2, "connectedComponentsWithStats", forbidden_ccl)
    fast_ccl = getattr(cv2, "connectedComponentsWithStatsWithAlgorithm", None)
    if callable(fast_ccl):
        monkeypatch.setattr(cv2, "connectedComponentsWithStatsWithAlgorithm", forbidden_ccl)

    result = counter.update(0, mask)

    assert result.total_count == 0
    assert result.roi_foreground_pixels == 0
    assert result.ccl_executed is False
    assert set(result.roi_subtimings_ms) == {
        "validate_ms", "projection_ms", "ccl_ms", "track_ms",
    }
    assert all(value >= 0 for value in result.roi_subtimings_ms.values())

    invalid_in_roi = np.zeros((240, 320), dtype=np.uint8)
    invalid_in_roi[120, 10] = 1
    with pytest.raises(ValueError, match="Binary8"):
        MaskCounter().update(0, invalid_in_roi)


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



def test_capture_mode_preserves_gray_mask_pairing_across_ping_pong_reuse(tmp_path):
    from capture_dataset import CaptureWriter, load_dataset

    allocator = MockAllocator()
    tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocator)
    masks = []
    grays = []
    for frame_id in range(5):
        gray = np.full((240, 320), frame_id + 10, dtype=np.uint8)
        mask = np.zeros((240, 320), dtype=np.uint8)
        mask[100 + frame_id:105 + frame_id, 20 + frame_id:25 + frame_id] = 255
        grays.append(gray.copy())
        masks.append(mask)
    dma = MockDma(masks)
    writer = CaptureWriter(tmp_path / "capture", expected_frames=5,
                           config={"line_y": 120, "min_area": 12},
                           max_bytes=2_000_000, queue_depth=5)
    copy_events = []

    async def source():
        for frame_id, gray in enumerate(grays):
            yield FrameInput(gray, frame_id, 10_000 + frame_id,
                             20_000 + frame_id, 1.25)

    def capture(result, slot):
        from test_capture_dataset import _frame_metadata
        writer.submit(result.frame_id, slot.capture_gray, slot.capture_mask, {
            **_frame_metadata(result.frame_id),
            **slot.capture_metadata,
            "frame_id": result.frame_id,
            "dma_slot_index": slot.index,
            "dma_complete_monotonic_ns": slot.dma_complete_monotonic_ns,
            "dma_status": {"frame_id": result.frame_id, "state": "IDLE"},
            "packer_status": {"frame_id": result.frame_id, "validation": "PASS"},
        })

    def after_transfer(frame_id, _slot):
        copy_events.append(("dma-complete", frame_id))

    def capture_raw_frame(frame_id, gray, mask):
        copy_events.append(("raw-copy", frame_id))
        writer.record_raw_frame(frame_id, gray, mask)

    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel, send_channel=dma.sendchannel,
        tx_buffers=tx_buffers, rx_buffers=rx_buffers, counter=MaskCounter(),
        packet_encoder=lambda gray, *, frame_id: bytes([frame_id]) * TX_BYTES,
        after_transfer=after_transfer,
        raw_capture_callback=capture_raw_frame,
        capture_callback=capture,
        on_frame=lambda result, mask: (dma.release_frame1.set() if result.frame_id == 0 else None,
                                       mask.fill(0)),
        transfer_timeout_s=1.0,
    )
    results = asyncio.run(stream.run(source()))
    manifest = writer.close()
    assert manifest["status"] == "COMPLETE"
    assert copy_events == [event for frame_id in range(5)
                           for event in (("dma-complete", frame_id), ("raw-copy", frame_id))]
    np.testing.assert_array_equal(writer.raw_gray8, np.stack(grays))
    np.testing.assert_array_equal(writer.raw_binary8, np.stack(masks))
    assert [result.frame_id for result in results] == list(range(5))
    reference = MaskCounter()
    expected_results = [reference.update(frame_id, mask)
                        for frame_id, mask in enumerate(masks)]
    assert [result.total_count for result in results] == [
        result.total_count for result in expected_results]
    assert [result.detections for result in results] == [
        result.detections for result in expected_results]
    _, stored = load_dataset(tmp_path / "capture")
    for frame_id, frame in enumerate(stored):
        np.testing.assert_array_equal(frame.gray, grays[frame_id])
        np.testing.assert_array_equal(frame.mask, masks[frame_id])
        assert frame.metadata["source_sequence"] == frame_id
        assert frame.metadata["dma_slot_index"] == frame_id % 2
        assert frame.metadata["dma_complete_monotonic_ns"] is not None


def test_capture_disabled_allocates_no_gray_snapshots():
    allocator = MockAllocator()
    tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocator)
    dma = MockDma([_mask(120)])
    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel, send_channel=dma.sendchannel,
        tx_buffers=tx_buffers, rx_buffers=rx_buffers, counter=MaskCounter(),
        packet_encoder=lambda gray, *, frame_id: bytes([frame_id]) * TX_BYTES,
        transfer_timeout_s=1.0,
    )

    asyncio.run(stream.run(_frames(1)))

    assert all(slot.capture_gray is None and slot.capture_mask is None
               and slot.preview_gray is None
               for slot in stream.slots)


def test_bbox_gap_union_find_merges_split_components_with_area_weighted_centroid():
    counter = MaskCounter(count_axis="x", component_cluster_gap=16)
    components = [
        Detection(100, 103, 108, 117, 40, 101.5, 112.5),
        Detection(118, 121, 108, 122, 60, 119.5, 115.0),
    ]

    result = counter.update_detections(0, components)

    assert len(result.raw_detections) == 2
    assert len(result.detections) == 1
    merged = result.detections[0]
    assert (merged.x_min, merged.x_max, merged.y_min, merged.y_max) == (100, 121, 108, 122)
    assert merged.area == 100
    assert merged.centroid_x == pytest.approx((40 * 101.5 + 60 * 119.5) / 100)


def test_bbox_gap_union_find_uses_transitive_component_links():
    counter = MaskCounter(count_axis="x", component_cluster_gap=16)
    components = [
        Detection(x, x + 3, 110, 119, 40, x + 1.5, 114.5)
        for x in (100, 118, 136)
    ]

    result = counter.update_detections(0, components)

    assert len(result.detections) == 1
    assert result.detections[0].x_min == 100
    assert result.detections[0].x_max == 139
    assert result.detections[0].area == 120


def test_short_gap_prediction_counts_only_an_observed_crossing():
    counter = MaskCounter(count_axis="x", line_x=160, max_step=60,
                          max_lateral_step=10, max_missing=1,
                          prediction_max_missing=2)

    def nut(x):
        return Detection(x - 5, x + 5, 110, 120, 121, float(x), 115.0)

    counter.update_detections(0, [nut(130)])
    counter.update_detections(1, [nut(142)])
    counter.update_detections(2, [])
    counter.update_detections(3, [])
    assert counter.total_count == 0
    result = counter.update_detections(4, [nut(174)])
    assert counter.total_count == 1
    assert len(result.events) == 1
    assert result.events[0].frame_id == 4
    assert result.events[0].inferred_gap is True


def test_discard_finished_tracks_bounds_long_run_history_without_changing_counts():
    def make_counter(**kwargs):
        return MaskCounter(count_axis="x", line_x=160, max_step=40,
                           max_missing=1, confirm_hits=2, **kwargs)

    default_counter = make_counter()
    preview_counter = make_counter(discard_finished_tracks=True)

    def nut(x):
        return Detection(x - 5, x + 5, 110, 120, 121, float(x), 115.0)

    for cycle in range(30):
        first_frame = cycle * 7
        for offset, x in ((0, 130), (1, 142), (2, 174)):
            frame_id = first_frame + offset
            default_result = default_counter.update_detections(frame_id, [nut(x)])
            preview_result = preview_counter.update_detections(frame_id, [nut(x)])
            assert preview_result.total_count == default_result.total_count
        for offset in (3, 4, 5, 6):
            frame_id = first_frame + offset
            default_result = default_counter.update_detections(frame_id, [])
            preview_result = preview_counter.update_detections(frame_id, [])
            assert preview_result.total_count == default_result.total_count
            if offset == 4:
                assert preview_result.tracks == ()
                assert default_result.tracks[-1]["finished"] is True

    assert default_counter.total_count == preview_counter.total_count == 30
    assert preview_counter.tracking_snapshot() == ()
    assert len(default_counter.tracking_snapshot()) == 30
    assert all(track["finished"] for track in default_counter.tracking_snapshot())


def test_hysteresis_band_requires_observation_beyond_positive_boundary():
    counter = MaskCounter(count_axis="x", line_x=160, hysteresis=2,
                          confirm_hits=2, max_step=10)

    def nut(x):
        return Detection(x - 5, x + 5, 110, 120, 121, float(x), 115.0)

    for frame_id, x in enumerate((157, 160, 161)):
        result = counter.update_detections(frame_id, [nut(x)])
        assert result.total_count == 0
        assert not result.events
    result = counter.update_detections(3, [nut(162)])
    assert result.total_count == 1
    assert len(result.events) == 1


def test_empty_binary_masks_have_zero_components_and_events_for_115_frames():
    counter = MaskCounter(count_axis="x", line_x=160, component_cluster_gap=16)

    results = [counter.update(frame_id, np.zeros((240, 320), dtype=np.uint8))
               for frame_id in range(115)]

    assert len(results) == 115
    assert counter.total_count == 0
    assert all(result.roi_foreground_pixels == 0 for result in results)
    assert all(not result.raw_detections and not result.detections and not result.events
               for result in results)


def test_preview_callback_receives_gray_and_mask_from_the_same_dma_slot():
    allocator = MockAllocator()
    tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocator)
    dma = MockDma([_mask(116), _mask(120)])
    observed = []

    async def source_frames():
        for frame_id in range(2):
            gray = np.full((240, 320), 30 + frame_id, dtype=np.uint8)
            yield FrameInput(gray, frame_id, frame_id + 1, frame_id + 2, 0.1)

    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel,
        send_channel=dma.sendchannel,
        tx_buffers=tx_buffers,
        rx_buffers=rx_buffers,
        counter=MaskCounter(line_y=120),
        packet_encoder=lambda gray, *, frame_id: bytes([frame_id]) * TX_BYTES,
        on_frame=lambda result, mask: (dma.release_frame1.set()
                                       if result.frame_id == 0 else None),
        preview_callback=lambda result, mask, gray: observed.append(
            (result.frame_id, int(gray[0, 0]), int(np.count_nonzero(mask)))
        ),
        transfer_timeout_s=1.0,
    )

    results = asyncio.run(stream.run(source_frames()))

    assert len(results) == len(observed) == 2
    assert observed == [(0, 30, 25), (1, 31, 25)]


def test_blocked_jpeg_encoder_does_not_block_dma_counter_pipeline(monkeypatch):
    import mjpeg_preview

    encode_started = threading.Event()
    release_encode = threading.Event()

    def slow_render(mask, **_kwargs):
        encode_started.set()
        assert release_encode.wait(2.0)
        return b"jpeg"

    monkeypatch.setattr(mjpeg_preview, "render_preview_jpeg", slow_render)
    allocator = MockAllocator()
    tx_buffers, rx_buffers = allocate_ping_pong_buffers(allocator)
    ys = (108, 112, 116, 120, 124)
    dma = MockDma([_mask(y) for y in ys])
    hub = MjpegFrameHub()
    publisher = PeriodicMjpegPublisher(hub=hub, line_y=120, interval=1)
    stream = DoubleBufferStream(
        recv_channel=dma.recvchannel,
        send_channel=dma.sendchannel,
        tx_buffers=tx_buffers,
        rx_buffers=rx_buffers,
        counter=MaskCounter(line_y=120, confirm_hits=2, max_step=12),
        packet_encoder=lambda gray, *, frame_id: bytes([frame_id]) * TX_BYTES,
        on_frame=lambda result, mask: (dma.release_frame1.set()
                                       if result.frame_id == 0 else None),
        preview_callback=publisher,
        transfer_timeout_s=1.0,
    )

    try:
        with hub.streaming_client():
            results = asyncio.run(stream.run(_frames(len(ys))))
        assert encode_started.wait(0.5)
        assert len(results) == len(ys)
        assert results[-1].total_count == 1
        assert all(slot.state is BufferState.FREE for slot in stream.slots)
    finally:
        release_encode.set()
        publisher.close()
        hub.close()
