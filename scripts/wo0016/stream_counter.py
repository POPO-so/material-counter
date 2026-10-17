"""PYNQ-Z2 Binary8 double-buffer stream counter, with host-testable boundaries."""
from __future__ import annotations

import asyncio
import inspect
import math
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterable, Callable, Protocol

import cv2
import numpy as np

WIDTH = 320
HEIGHT = 240
PIXELS = WIDTH * HEIGHT
RX_BYTES = PIXELS
TX_BYTES = 16 + PIXELS * 3 + 16


class PacketEncoder(Protocol):
    def __call__(self, gray: np.ndarray, *, frame_id: int) -> bytes: ...


class PacketEncoderInto(Protocol):
    def __call__(self, gray: np.ndarray, output: Any, *, frame_id: int) -> None: ...


class BufferState(str, Enum):
    FREE = "FREE"
    DMA_OWNED = "DMA_OWNED"
    CPU_READY = "CPU_READY"
    CPU_OWNED = "CPU_OWNED"
    HELD = "HELD"


@dataclass
class DmaBufferSlot:
    index: int
    tx_buffer: Any
    rx_buffer: Any
    cached_mask: np.ndarray | None = None
    state: BufferState = BufferState.FREE
    frame_id: int | None = None


@dataclass(frozen=True)
class Detection:
    x_min: int
    x_max: int
    y_min: int
    y_max: int
    area: int
    centroid_x: float
    centroid_y: float


@dataclass(frozen=True)
class CountEvent:
    track_id: int
    frame_id: int
    centroid_x: float
    centroid_y: float


@dataclass(frozen=True)
class FrameResult:
    frame_id: int
    total_count: int
    detections: tuple[Detection, ...]
    events: tuple[CountEvent, ...]
    roi_top: int
    roi_bottom: int
    row_projection: tuple[int, ...]
    column_projection: tuple[int, ...]


@dataclass
class _Track:
    track_id: int
    detection: Detection
    last_frame: int
    streak: int = 1
    confirmed: bool = False
    armed: bool = False
    counted: bool = False
    missed: int = 0
    finished: bool = False


class MaskCounter:
    """Count confirmed connected components crossing a horizontal Binary8 line."""

    def __init__(self, *, width: int = WIDTH, height: int = HEIGHT,
                 line_y: int = 120, roi_half_height: int = 24,
                 min_area: int = 12, direction: int = 1,
                 hysteresis: float = 2.0, confirm_hits: int = 2,
                 max_missing: int = 1, max_step: float = 24.0,
                 max_x_step: float = 20.0, backward_tolerance: float = 2.0):
        self.width = _integer(width, "width", minimum=1)
        self.height = _integer(height, "height", minimum=1)
        self.line_y = _integer(line_y, "line_y", minimum=0)
        if self.line_y >= self.height:
            raise ValueError("line_y must be inside the frame")
        self.roi_half_height = _integer(roi_half_height, "roi_half_height", minimum=1)
        self.min_area = _integer(min_area, "min_area", minimum=1)
        if direction not in (-1, 1):
            raise ValueError("direction must be +1 (top to bottom) or -1")
        self.direction = direction
        self.hysteresis = _positive_float(hysteresis, "hysteresis")
        self.confirm_hits = _integer(confirm_hits, "confirm_hits", minimum=2)
        self.max_missing = _integer(max_missing, "max_missing", minimum=0)
        self.max_step = _positive_float(max_step, "max_step")
        self.max_x_step = _positive_float(max_x_step, "max_x_step")
        self.backward_tolerance = _nonnegative_float(backward_tolerance, "backward_tolerance")
        self.total_count = 0
        self.last_frame_id: int | None = None
        self._tracks: dict[int, _Track] = {}
        self._next_track_id = 1

    def update(self, frame_id: int, mask: np.ndarray) -> FrameResult:
        frame_id = _integer(frame_id, "frame_id", minimum=0)
        if self.last_frame_id is not None and frame_id <= self.last_frame_id:
            raise ValueError("frame IDs must strictly increase")
        image = np.asarray(mask)
        if image.dtype != np.uint8 or image.ndim != 2 or image.shape != (self.height, self.width):
            raise ValueError(f"mask must be a uint8 array with shape {(self.height, self.width)}")
        if np.any((image != 0) & (image != 0xFF)):
            raise ValueError("mask values must be Binary8 0x00 or 0xFF")

        detections, roi_top, roi_bottom, row_projection, column_projection = (
            self._extract_roi_detections(image)
        )
        self.last_frame_id = frame_id
        events = self._associate_and_update(frame_id, detections)
        return FrameResult(
            frame_id=frame_id,
            total_count=self.total_count,
            detections=tuple(detections),
            events=tuple(events),
            roi_top=roi_top,
            roi_bottom=roi_bottom,
            row_projection=row_projection,
            column_projection=column_projection,
        )

    def _extract_roi_detections(
        self, mask: np.ndarray
    ) -> tuple[list[Detection], int, int, tuple[int, ...], tuple[int, ...]]:
        top = max(0, self.line_y - self.roi_half_height)
        bottom = min(self.height, self.line_y + self.roi_half_height + 1)
        roi = mask[top:bottom, :]
        if not roi.flags.c_contiguous:
            roi = np.ascontiguousarray(roi)
        row_projection = tuple(int(value) for value in np.count_nonzero(roi, axis=1))
        column_projection = tuple(int(value) for value in np.count_nonzero(roi, axis=0))
        if cv2.countNonZero(roi) < self.min_area:
            return [], top, bottom, row_projection, column_projection
        fast_ccl = getattr(cv2, "connectedComponentsWithStatsWithAlgorithm", None)
        fast_algorithm = getattr(cv2, "CCL_GRANA", None)
        if callable(fast_ccl) and fast_algorithm is not None:
            count, _, stats, centroids = fast_ccl(
                roi, 8, cv2.CV_16U, fast_algorithm
            )
        else:
            count, _, stats, centroids = cv2.connectedComponentsWithStats(
                roi, connectivity=8, ltype=cv2.CV_16U
            )
        qualified_labels = np.flatnonzero(
            stats[1:count, cv2.CC_STAT_AREA] >= self.min_area
        ) + 1
        detections = []
        for label in qualified_labels:
            x, y, box_width, box_height, area = (
                int(value) for value in stats[label]
            )
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
        return detections, top, bottom, row_projection, column_projection

    def _side(self, detection: Detection) -> float:
        return self.direction * (detection.centroid_y - self.line_y)

    def _associate_and_update(self, frame_id: int,
                              detections: list[Detection]) -> list[CountEvent]:
        active = []
        for track in self._tracks.values():
            if track.finished:
                continue
            if frame_id - track.last_frame > self.max_missing + 1:
                track.finished = True
            else:
                active.append(track)

        edges = []
        for track in active:
            dt = frame_id - track.last_frame
            old = track.detection
            for index, detection in enumerate(detections):
                dx = abs(detection.centroid_x - old.centroid_x)
                dy = self.direction * (detection.centroid_y - old.centroid_y)
                area_ratio = detection.area / old.area
                if (dx <= self.max_x_step * dt
                        and -self.backward_tolerance <= dy <= self.max_step * dt
                        and 0.25 <= area_ratio <= 4.0):
                    edges.append((dx * dx + dy * dy, track.track_id, index))

        used_tracks: set[int] = set()
        used_detections: set[int] = set()
        events = []
        for _, track_id, index in sorted(edges):
            if track_id in used_tracks or index in used_detections:
                continue
            track = self._tracks[track_id]
            detection = detections[index]
            dt = frame_id - track.last_frame
            track.streak = track.streak + 1 if dt == 1 else 1
            track.confirmed = track.confirmed or track.streak >= self.confirm_hits
            track.missed = 0
            track.last_frame = frame_id
            track.detection = detection
            track.armed = track.armed or self._side(detection) < -self.hysteresis
            used_tracks.add(track_id)
            used_detections.add(index)
            if (track.armed and track.confirmed and not track.counted
                    and self._side(detection) >= self.hysteresis):
                track.counted = True
                self.total_count += 1
                events.append(CountEvent(track.track_id, frame_id,
                                         detection.centroid_x, detection.centroid_y))

        for track in active:
            if track.track_id not in used_tracks:
                track.missed = frame_id - track.last_frame
                track.streak = 0
                if track.missed > self.max_missing:
                    track.finished = True

        for index, detection in enumerate(detections):
            if index in used_detections:
                continue
            side = self._side(detection)
            born_after_arming_zone = side >= -self.hysteresis
            track = _Track(
                track_id=self._next_track_id,
                detection=detection,
                last_frame=frame_id,
                armed=side < -self.hysteresis,
                counted=born_after_arming_zone,
                confirmed=self.confirm_hits <= 1,
            )
            self._tracks[track.track_id] = track
            self._next_track_id += 1
        return sorted(events, key=lambda event: event.track_id)


async def _wait_channel(channel: Any) -> None:
    wait = getattr(channel, "wait", None)
    if callable(wait):
        loop = asyncio.get_running_loop()
        completed = loop.create_future()

        def finish(error: BaseException | None) -> None:
            if completed.done():
                return
            if error is None:
                completed.set_result(None)
            else:
                completed.set_exception(error)

        def blocking_wait() -> None:
            error: BaseException | None = None
            try:
                wait()
            except BaseException as exc:
                error = exc
            try:
                loop.call_soon_threadsafe(finish, error)
            except RuntimeError:
                # A timed-out DMA wait may finish after the fail-hold loop closes.
                pass

        threading.Thread(target=blocking_wait, name="wo0016-dma-wait", daemon=True).start()
        await completed
        return

    wait_async = getattr(channel, "wait_async", None)
    if not callable(wait_async):
        raise TypeError("DMA channel must provide wait() or wait_async()")
    result = wait_async()
    if not inspect.isawaitable(result):
        raise TypeError("channel.wait_async() must return an awaitable")
    await result


def transfer_async(channel: Any, buffer: Any, nbytes: int) -> asyncio.Task:
    """Start PYNQ's non-blocking transfer and return an async completion task.

    PYNQ's ``transfer`` starts the operation and returns ``None``; completion
    methods belong to the channel. Prefer its blocking ``wait`` in a worker
    thread because PYNQ's ``wait_async`` requires a configured interrupt.
    """
    nbytes = _integer(nbytes, "nbytes", minimum=1)
    channel.transfer(buffer, nbytes=nbytes)
    return asyncio.create_task(_wait_channel(channel))


def allocate_ping_pong_buffers(allocate: Callable[..., Any]
                               ) -> tuple[tuple[Any, Any], tuple[Any, Any]]:
    """Allocate and validate two paired PYNQ CMA TX/RX slots exactly once."""
    allocated = []
    tx_buffers = []
    rx_buffers = []
    try:
        for _ in range(2):
            tx = allocate(shape=(TX_BYTES,), dtype=np.uint8)
            allocated.append(tx)
            rx = allocate(shape=(RX_BYTES,), dtype=np.uint8)
            allocated.append(rx)
            _validate_buffer(tx, TX_BYTES, 8, "TX")
            _validate_buffer(rx, RX_BYTES, 4, "RX")
            tx_buffers.append(tx)
            rx_buffers.append(rx)
        spans = []
        for kind, buffers, size in (("TX", tx_buffers, TX_BYTES),
                                    ("RX", rx_buffers, RX_BYTES)):
            for index, buffer in enumerate(buffers):
                start = int(buffer.physical_address)
                spans.append((start, start + size, kind, index))
        spans.sort()
        for left, right in zip(spans, spans[1:]):
            if left[1] > right[0]:
                raise ValueError("allocated DMA buffers overlap")
    except BaseException:
        # No DMA has started here, so partially allocated objects can be released.
        for buffer in allocated:
            close = getattr(buffer, "close", None)
            if callable(close):
                close()
        raise
    return tuple(tx_buffers), tuple(rx_buffers)


def _validate_buffer(buffer: Any, expected_nbytes: int, alignment: int, name: str) -> None:
    if getattr(buffer, "nbytes", None) != expected_nbytes:
        raise ValueError(f"{name} CMA buffer must be exactly {expected_nbytes} bytes")
    address = getattr(buffer, "physical_address", None)
    if isinstance(address, bool) or not isinstance(address, (int, np.integer)) or int(address) <= 0:
        raise ValueError(f"{name} CMA buffer needs a non-zero integer physical_address")
    if int(address) % alignment:
        raise ValueError(f"{name} CMA buffer address must be {alignment}-byte aligned")
    view = np.asarray(buffer)
    if not view.flags.c_contiguous or view.dtype != np.uint8 or view.nbytes != expected_nbytes:
        raise ValueError(f"{name} CMA buffer must be contiguous uint8 storage of the exact size")


class DoubleBufferStream:
    """Overlap frame DMA with CPU processing using two exclusive TX/RX slots."""

    def __init__(self, *, recv_channel: Any, send_channel: Any,
                 tx_buffers: tuple[Any, Any], rx_buffers: tuple[Any, Any],
                 counter: MaskCounter,
                 packet_encoder: PacketEncoder,
                 packet_encoder_into: PacketEncoderInto | None = None,
                 before_transfer: Callable[[int, DmaBufferSlot], Any] | None = None,
                 before_rx_copy: Callable[[int, DmaBufferSlot], Any] | None = None,
                 after_transfer: Callable[[int, DmaBufferSlot], Any] | None = None,
                 on_frame: Callable[[FrameResult, np.ndarray], Any] | None = None,
                 stage_timings_ms: dict[str, list[float]] | None = None,
                 transfer_timeout_s: float = 5.0):
        if len(tx_buffers) != 2 or len(rx_buffers) != 2:
            raise ValueError("exactly two TX and two RX buffers are required")
        for buffer in tx_buffers:
            _validate_buffer(buffer, TX_BYTES, 8, "TX")
        for buffer in rx_buffers:
            _validate_buffer(buffer, RX_BYTES, 4, "RX")
        self.recv_channel = recv_channel
        self.send_channel = send_channel
        self.counter = counter
        self.packet_encoder = packet_encoder
        self.packet_encoder_into = packet_encoder_into
        self.before_transfer = before_transfer
        self.before_rx_copy = before_rx_copy
        self.after_transfer = after_transfer
        self.on_frame = on_frame
        self.stage_timings_ms = stage_timings_ms
        self._postprocess_ms: dict[int, float] = {}
        self.transfer_timeout_s = _positive_float(transfer_timeout_s, "transfer_timeout_s")
        self.slots = [
            DmaBufferSlot(i, tx_buffers[i], rx_buffers[i],
                          cached_mask=np.empty((HEIGHT, WIDTH), dtype=np.uint8))
            for i in range(2)
        ]
        self._free_slots: asyncio.Queue[DmaBufferSlot] = asyncio.Queue(maxsize=2)
        for slot in self.slots:
            self._free_slots.put_nowait(slot)
        self._ready_slots: asyncio.Queue[DmaBufferSlot | None] = asyncio.Queue(maxsize=2)
        self._results: list[FrameResult] = []
        self._pending_waits: list[asyncio.Task] = []
        self.held_slots: tuple[DmaBufferSlot, ...] = ()
        self.dma_may_be_active = False
        self._has_run = False

    async def run(self, frames: AsyncIterable[np.ndarray]) -> tuple[FrameResult, ...]:
        if self._has_run:
            raise RuntimeError("a DoubleBufferStream instance can run only once")
        self._has_run = True
        consumer = asyncio.create_task(self._consume_ready_slots())
        frame_iterator = frames.__aiter__()
        try:
            async for frame_id, gray in _enumerate_frames(frame_iterator):
                if consumer.done():
                    consumer.result()
                gray = np.asarray(gray)
                if gray.dtype != np.uint8 or gray.shape != (HEIGHT, WIDTH):
                    raise ValueError(f"source frames must be uint8 Gray8 {(HEIGHT, WIDTH)} arrays")
                slot = await self._free_slots.get()
                if slot.state is not BufferState.FREE:
                    raise RuntimeError("buffer pool returned a slot that is not free")
                slot.state = BufferState.CPU_OWNED
                slot.frame_id = frame_id
                encode_started = time.perf_counter()
                if self.packet_encoder_into is not None:
                    self.packet_encoder_into(gray, slot.tx_buffer, frame_id=frame_id)
                else:
                    packet = self.packet_encoder(gray, frame_id=frame_id)
                    if not isinstance(packet, (bytes, bytearray, memoryview)) or len(packet) != TX_BYTES:
                        raise ValueError(f"packet encoder must return exactly {TX_BYTES} bytes")
                    slot.tx_buffer[:] = np.frombuffer(packet, dtype=np.uint8)
                self._record_timing("encode", time.perf_counter() - encode_started)
                dma_started = time.perf_counter()
                if self.before_transfer is not None:
                    prepared = self.before_transfer(frame_id, slot)
                    if inspect.isawaitable(prepared):
                        await prepared

                self.dma_may_be_active = True
                slot.state = BufferState.DMA_OWNED
                rx_wait = transfer_async(self.recv_channel, slot.rx_buffer, RX_BYTES)
                self._pending_waits.append(rx_wait)
                try:
                    tx_wait = transfer_async(self.send_channel, slot.tx_buffer, TX_BYTES)
                except BaseException:
                    self._hold_nonfree_slots()
                    raise
                self._pending_waits.append(tx_wait)
                try:
                    await asyncio.wait_for(
                        asyncio.gather(rx_wait, tx_wait),
                        timeout=self.transfer_timeout_s,
                    )
                    self.dma_may_be_active = False
                    self._record_timing("dma", time.perf_counter() - dma_started)
                except BaseException:
                    self._hold_nonfree_slots()
                    raise
                finally:
                    self._pending_waits = [task for task in self._pending_waits if not task.done()]
                if self.before_rx_copy is not None:
                    sync_started = time.perf_counter()
                    prepared = self.before_rx_copy(frame_id, slot)
                    if inspect.isawaitable(prepared):
                        await prepared
                    self._record_timing("rx_sync", time.perf_counter() - sync_started)
                if slot.cached_mask is None:
                    raise RuntimeError("DMA slot has no cached CPU mask")
                copy_started = time.perf_counter()
                rx_view = np.asarray(slot.rx_buffer)
                np.copyto(slot.cached_mask.reshape(-1), rx_view, casting="no")
                self._record_timing("rx_copy", time.perf_counter() - copy_started)
                postprocess_ms = 0.0
                if self.after_transfer is not None:
                    postprocess_started = time.perf_counter()
                    validated = self.after_transfer(frame_id, slot)
                    if inspect.isawaitable(validated):
                        await validated
                    packer_check_seconds = time.perf_counter() - postprocess_started
                    postprocess_ms += packer_check_seconds * 1000.0
                    self._record_timing("packer_check", packer_check_seconds)
                self._postprocess_ms[frame_id] = postprocess_ms

                if consumer.done():
                    consumer.result()
                slot.state = BufferState.CPU_READY
                await self._ready_slots.put(slot)

            await self._ready_slots.put(None)
            await consumer
            return tuple(self._results)
        except BaseException:
            self._hold_nonfree_slots()
            if not consumer.done():
                consumer.cancel()
                try:
                    await consumer
                except asyncio.CancelledError:
                    pass
            raise
        finally:
            close_frames = getattr(frame_iterator, "aclose", None)
            if callable(close_frames):
                await close_frames()

    async def _consume_ready_slots(self) -> None:
        while True:
            slot = await self._ready_slots.get()
            if slot is None:
                return
            if slot.state is not BufferState.CPU_READY:
                raise RuntimeError("only completed DMA buffers may enter CPU processing")
            slot.state = BufferState.CPU_OWNED
            if slot.frame_id is None:
                raise RuntimeError("ready slot has no frame_id")
            mask = slot.cached_mask
            if mask is None:
                raise RuntimeError("completed DMA slot has no cached CPU mask")
            frame_id = slot.frame_id
            postprocess_started = time.perf_counter()
            roi_started = time.perf_counter()
            result = await asyncio.to_thread(self.counter.update, slot.frame_id, mask)
            roi_seconds = time.perf_counter() - roi_started
            self._record_timing("roi_count", roi_seconds)
            if self.on_frame is not None:
                if inspect.iscoroutinefunction(self.on_frame):
                    await self.on_frame(result, mask)
                else:
                    callback_result = await asyncio.to_thread(self.on_frame, result, mask)
                    if inspect.isawaitable(callback_result):
                        await callback_result
            postprocess_ms = self._postprocess_ms.pop(frame_id, 0.0)
            postprocess_ms += (time.perf_counter() - postprocess_started) * 1000.0
            self._record_timing("postprocess", postprocess_ms / 1000.0)
            self._results.append(result)
            slot.state = BufferState.FREE
            slot.frame_id = None
            self._free_slots.put_nowait(slot)

    def _record_timing(self, stage: str, elapsed_seconds: float) -> None:
        if self.stage_timings_ms is not None:
            self.stage_timings_ms.setdefault(stage, []).append(elapsed_seconds * 1000.0)

    def _hold_nonfree_slots(self) -> None:
        held = []
        for slot in self.slots:
            if slot.state is not BufferState.FREE:
                slot.state = BufferState.HELD
                held.append(slot)
        self.held_slots = tuple(held)


async def _enumerate_frames(frames: AsyncIterable[np.ndarray]):
    frame_id = 0
    async for frame in frames:
        yield frame_id, frame
        frame_id += 1


def _integer(value: Any, name: str, *, minimum: int) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    result = int(value)
    if result < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return result


def _positive_float(value: Any, name: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be finite and positive")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite and positive") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def _nonnegative_float(value: Any, name: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be finite and nonnegative")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite and nonnegative") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return result
