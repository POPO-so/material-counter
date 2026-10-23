"""Bounded, lossless capture storage and independent-GT evaluation for WO-0017."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import threading
import time
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np

WIDTH = 320
HEIGHT = 240
FRAME_SHAPE = (HEIGHT, WIDTH)
FRAME_BYTES = WIDTH * HEIGHT
SCHEMA_VERSION = 1
DEFAULT_MAX_BYTES = 96 * 1024 * 1024
RAW_FRAME_ARCHIVE_NAME = "frames_gray8_binary8.npz"
SOURCE_FRAME_ARCHIVE_NAME = "frames_source640x400.npz"
SOURCE_FRAME_SHAPE = (400, 640)
SOURCE_FRAME_BYTES = int(np.prod(SOURCE_FRAME_SHAPE))


class CaptureError(RuntimeError):
    """Capture cannot continue without losing or corrupting requested evidence."""


@dataclass(frozen=True)
class CapturedFrame:
    frame_id: int
    gray: np.ndarray
    mask: np.ndarray
    metadata: dict[str, Any]


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


_REQUIRED_FRAME_METADATA = {
    "frame_id", "source_sequence", "capture_monotonic_ns", "capture_wall_time_ns",
    "camera_read_ms", "capture_timestamp_kind", "dma_complete_monotonic_ns",
    "dma_slot_index", "software_pairing", "resolution", "threshold",
    "algorithm_config", "roi", "dma_status", "packer_status", "raw_detections",
    "detections", "associations", "tracks", "events", "suppressed_events",
    "total_count", "frame_result",
}


def _validate_frame_metadata(frame_id: int, metadata: Mapping[str, Any]) -> dict[str, Any]:
    safe = _json_safe(metadata)
    missing = sorted(_REQUIRED_FRAME_METADATA - set(safe))
    if missing:
        raise CaptureError(f"frame {frame_id} metadata is missing required fields: {', '.join(missing)}")
    if safe["frame_id"] != frame_id or safe["source_sequence"] != frame_id:
        raise CaptureError(f"frame {frame_id} metadata frame ID or camera sequence mismatch")
    capture_ns = safe["capture_monotonic_ns"]
    dma_ns = safe["dma_complete_monotonic_ns"]
    wall_ns = safe["capture_wall_time_ns"]
    for label, value in (("capture_monotonic_ns", capture_ns),
                         ("dma_complete_monotonic_ns", dma_ns),
                         ("capture_wall_time_ns", wall_ns)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise CaptureError(f"frame {frame_id} has invalid {label}")
    if dma_ns < capture_ns:
        raise CaptureError(f"frame {frame_id} DMA completion precedes camera capture")
    if isinstance(safe["dma_slot_index"], bool) or safe["dma_slot_index"] not in (0, 1):
        raise CaptureError(f"frame {frame_id} has invalid DMA slot index")
    for label in ("software_pairing", "resolution", "algorithm_config", "roi",
                  "dma_status", "packer_status", "frame_result"):
        if not isinstance(safe[label], dict):
            raise CaptureError(f"frame {frame_id} {label} must be an object")
    if (safe["resolution"].get("width") != WIDTH
            or safe["resolution"].get("height") != HEIGHT):
        raise CaptureError(f"frame {frame_id} resolution does not match the 320x240 capture format")
    threshold = safe["threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 255:
        raise CaptureError(f"frame {frame_id} threshold is invalid")
    if (safe["software_pairing"].get("input_sequence_equals_dma_frame_id") is not True
            or safe["software_pairing"].get("input_and_output_use_same_slot") is not True
            or safe["software_pairing"].get("mask_echoes_frame_id") is not False):
        raise CaptureError(f"frame {frame_id} software pairing lifecycle is incomplete or inconsistent")
    for label in ("raw_detections", "detections", "associations", "tracks",
                  "events", "suppressed_events"):
        if not isinstance(safe[label], list):
            raise CaptureError(f"frame {frame_id} {label} must be an array")
    if safe["dma_status"].get("frame_id") != frame_id:
        raise CaptureError(f"frame {frame_id} DMA metadata frame ID mismatch")
    if (safe["packer_status"].get("frame_id") != frame_id
            or safe["packer_status"].get("validation") != "PASS"):
        raise CaptureError(f"frame {frame_id} Packer validation is missing or failed")
    if isinstance(safe["total_count"], bool) or not isinstance(safe["total_count"], int):
        raise CaptureError(f"frame {frame_id} total_count is invalid")
    return safe


def estimated_capture_bytes(expected_frames: int, *, source_frame_count: int = 0) -> int:
    """Conservative cap for per-frame records plus the combined raw-frame NPZ."""
    if isinstance(expected_frames, bool) or not isinstance(expected_frames, int) or expected_frames < 1:
        raise ValueError("expected_frames must be a positive integer")
    if (isinstance(source_frame_count, bool) or not isinstance(source_frame_count, int)
            or source_frame_count < 0 or source_frame_count > expected_frames):
        raise ValueError("source_frame_count must be between zero and expected_frames")
    return (expected_frames * (4 * FRAME_BYTES + 4096) + 32768
            + source_frame_count * (SOURCE_FRAME_BYTES + 4096))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def preflight_capture_target(output_dir: str | Path, *, expected_frames: int,
                             max_bytes: int = DEFAULT_MAX_BYTES,
                             source_frame_count: int = 0) -> dict[str, Any]:
    target = Path(output_dir).expanduser().resolve(strict=False)
    normalized_parts = [part.lower() for part in target.parts]
    protected_archive = any(
        normalized_parts[index:index + 2] == ["data", "results"]
        and index + 2 < len(normalized_parts)
        and normalized_parts[index + 2].startswith("wo0016-")
        for index in range(len(normalized_parts))
    )
    if protected_archive:
        raise CaptureError("capture output cannot be placed inside a protected WO-0016 archive path")
    if target.exists() or target.is_symlink():
        raise CaptureError(f"capture output directory already exists: {target}")
    parent = target.parent
    if not parent.is_dir():
        raise CaptureError(f"capture parent directory must already exist: {parent}")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    estimate = estimated_capture_bytes(expected_frames,
                                       source_frame_count=source_frame_count)
    if estimate > max_bytes:
        raise CaptureError(
            f"capture capacity limit {max_bytes} bytes is below estimated requirement {estimate} bytes"
        )
    free_bytes = shutil.disk_usage(parent).free
    if free_bytes < estimate:
        raise CaptureError(
            f"insufficient free disk space: need about {estimate} bytes, have {free_bytes}"
        )
    return {"path": str(target), "expected_bytes": estimate,
            "max_bytes": max_bytes, "free_bytes_at_preflight": free_bytes}


class CaptureWriter:
    """Write immutable input/mask snapshots through a strictly bounded queue."""

    def __init__(self, output_dir: str | Path, *, expected_frames: int,
                 config: Mapping[str, Any], max_bytes: int = DEFAULT_MAX_BYTES,
                 queue_depth: int = 2, metadata: Mapping[str, Any] | None = None,
                 source_type: str = "camera_capture",
                 source_frame_ids: tuple[int, ...] = (),
                 defer_writes_until_close: bool = False):
        if type(defer_writes_until_close) is not bool:
            raise ValueError("defer_writes_until_close must be a boolean")
        self.defer_writes_until_close = defer_writes_until_close
        self._deferred_metadata: list[dict[str, Any]] = []
        if isinstance(queue_depth, bool) or not isinstance(queue_depth, int) or queue_depth < 1:
            raise ValueError("queue_depth must be a positive integer")
        if not isinstance(source_frame_ids, tuple):
            raise ValueError("source_frame_ids must be a tuple of selected frame indexes")
        # Validate frame counts before using them in index checks or set construction.
        estimated_capture_bytes(expected_frames, source_frame_count=len(source_frame_ids))
        if any(isinstance(value, bool) or not isinstance(value, int)
               or not 0 <= value < expected_frames for value in source_frame_ids):
            raise ValueError("source_frame_ids must be unique indexes inside the capture window")
        if len(set(source_frame_ids)) != len(source_frame_ids):
            raise ValueError("source_frame_ids must be unique indexes inside the capture window")
        self.source_frame_ids = source_frame_ids
        self.source_frame_index = {frame_id: index
                                   for index, frame_id in enumerate(source_frame_ids)}
        self.source_gray8 = (np.empty((len(source_frame_ids), *SOURCE_FRAME_SHAPE),
                                      dtype=np.uint8) if source_frame_ids else None)
        self._source_frames_recorded: set[int] = set()
        self.preflight = preflight_capture_target(
            output_dir, expected_frames=expected_frames, max_bytes=max_bytes,
            source_frame_count=len(source_frame_ids),
        )
        self.output_dir = Path(self.preflight["path"])
        self.expected_frames = expected_frames
        self.max_bytes = max_bytes
        self.config = _json_safe(config)
        self.metadata = _json_safe(metadata or {})
        self.source_type = source_type
        # These NumPy arrays live in ordinary process RAM. Only the four DMA
        # ping-pong buffers in the board runner use PYNQ/CMA allocation.
        self.raw_gray8 = np.empty((expected_frames, *FRAME_SHAPE), dtype=np.uint8)
        self.raw_binary8 = np.empty((expected_frames, *FRAME_SHAPE), dtype=np.uint8)
        self._raw_frames_recorded = 0
        self._raw_record_lock = threading.Lock()
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=queue_depth)
        self._thread = threading.Thread(target=self._writer_main,
                                        name="wo0017-capture-writer", daemon=True)
        self._error: str | None = None
        self._closed = False
        self._submitted = 0
        self._accepted = 0
        self._written = 0
        self._dropped = 0
        self._bytes_written = 0
        self._next_frame_id = 0
        self._records_path = self.output_dir / "frame-records.jsonl"
        self._manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "status": "IN_PROGRESS",
            "write_mode": ("deferred_memory" if defer_writes_until_close else "streaming_queue"),
            "source_type": source_type,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "expected_frames": expected_frames,
            "captured_frames": 0,
            "dropped_frames": 0,
            "frame_shape": list(FRAME_SHAPE),
            "gray_dtype": "uint8",
            "mask_dtype": "uint8",
            "mask_encoding": "raw Binary8 values 0x00/0xFF; uncompressed NPY",
            "gray_encoding": "unmodified FPGA input Gray8; uncompressed NPY",
            "config": self.config,
            "metadata": self.metadata,
            "capacity": self.preflight,
            "estimated_total_bytes": estimated_capture_bytes(
                expected_frames, source_frame_count=len(source_frame_ids)),
            "max_bytes": max_bytes,
            "raw_frame_archive": None,
            "source_frame_ids": list(source_frame_ids),
            "source_frame_archive": None,
        }
        try:
            self.output_dir.mkdir(mode=0o700)
        except OSError as exc:
            raise CaptureError(f"cannot create capture directory: {exc}") from exc
        try:
            self._write_manifest()
            (self.output_dir / "frames").mkdir(mode=0o700)
            template_path = self.output_dir / "ground-truth.template.json"
            with template_path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(create_ground_truth_template(), stream, ensure_ascii=False,
                          indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if not self.defer_writes_until_close:
                self._thread.start()
        except BaseException as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            self._manifest.update({"status": "INCOMPLETE", "error": self._error,
                                   "completed_utc": datetime.now(timezone.utc).isoformat()})
            try:
                self._write_manifest()
            except OSError:
                pass
            raise CaptureError(f"capture writer initialization failed: {self._error}") from exc

    def submit(self, frame_id: int, gray: np.ndarray, mask: np.ndarray,
               metadata: Mapping[str, Any]) -> None:
        if self._closed:
            raise CaptureError("capture writer is already closed")
        if self._error is not None:
            raise CaptureError(f"capture writer failed: {self._error}")
        if isinstance(frame_id, bool) or not isinstance(frame_id, int) or frame_id != self._next_frame_id:
            raise CaptureError(f"capture frame IDs must be contiguous from zero; got {frame_id}")
        if frame_id >= self.expected_frames:
            raise CaptureError("capture received more frames than requested")
        if not isinstance(metadata, Mapping):
            raise CaptureError("per-frame capture metadata must be a mapping")
        safe_metadata = _validate_frame_metadata(frame_id, metadata)
        safe_metadata["capture_config_sha256"] = hashlib.sha256(_json_bytes(self.config)).hexdigest()
        gray_array = self._validate_image(gray, "gray")
        mask_array = self._validate_image(mask, "mask")
        if np.any((mask_array != 0) & (mask_array != 0xFF)):
            raise CaptureError("raw mask contains values other than Binary8 0x00/0xFF")
        if frame_id >= self._raw_frames_recorded:
            self.record_raw_frame(frame_id, gray_array, mask_array)
        if self.defer_writes_until_close:
            # The existing full-run arrays own the DMA snapshots. Retain only
            # metadata here: no queue, duplicate image buffers, or disk I/O.
            self._deferred_metadata.append(safe_metadata)
            self._submitted += 1
            self._accepted += 1
            self._next_frame_id += 1
            return
        item = (frame_id, np.array(gray_array, copy=True, order="C"),
                np.array(mask_array, copy=True, order="C"), safe_metadata)
        self._submitted += 1
        self._next_frame_id += 1
        try:
            self._queue.put_nowait(item)
        except queue.Full as exc:
            self._dropped += 1
            self._error = f"bounded capture queue overflow at frame {frame_id}"
            raise CaptureError(self._error) from exc
        self._accepted += 1

    def record_raw_frame(self, frame_id: int, gray: np.ndarray, mask: np.ndarray) -> None:
        """Copy one completed DMA pair into preallocated ordinary-RAM arrays."""
        if self._closed:
            raise CaptureError("capture writer is already closed")
        if self._error is not None:
            raise CaptureError(f"capture writer failed: {self._error}")
        if isinstance(frame_id, bool) or not isinstance(frame_id, int):
            raise CaptureError("raw frame ID must be an integer")
        if frame_id < 0:
            raise CaptureError("raw frame ID must be nonnegative")
        if frame_id >= self.expected_frames:
            raise CaptureError("raw capture received more frames than requested")
        gray_array = self._validate_image(gray, "gray")
        mask_array = self._validate_image(mask, "mask")
        with self._raw_record_lock:
            if frame_id < self._raw_frames_recorded:
                return
            if frame_id != self._raw_frames_recorded:
                raise CaptureError(
                    f"raw frame IDs must be contiguous from zero; got {frame_id}"
                )
            np.copyto(self.raw_gray8[frame_id], gray_array, casting="no")
            np.copyto(self.raw_binary8[frame_id], mask_array, casting="no")
            self._raw_frames_recorded += 1

    def record_source_frame(self, frame_id: int, source: np.ndarray) -> None:
        """Retain only explicitly selected raw 640x400 source frames for calibration."""
        if self._closed:
            raise CaptureError("capture writer is already closed")
        if self._error is not None:
            raise CaptureError(f"capture writer failed: {self._error}")
        index = self.source_frame_index.get(frame_id)
        if index is None:
            return
        array = np.asarray(source)
        if (array.dtype != np.uint8 or array.shape != SOURCE_FRAME_SHAPE
                or not array.flags.c_contiguous):
            raise CaptureError(
                f"source frame must be contiguous uint8 {SOURCE_FRAME_SHAPE}; "
                f"got dtype={array.dtype}, shape={array.shape}"
            )
        if frame_id in self._source_frames_recorded:
            raise CaptureError(f"source frame {frame_id} was recorded more than once")
        if self.source_gray8 is None:
            raise CaptureError("source frame storage was not configured")
        np.copyto(self.source_gray8[index], array, casting="no")
        self._source_frames_recorded.add(frame_id)

    @staticmethod
    def _validate_image(image: np.ndarray, label: str) -> np.ndarray:
        array = np.asarray(image)
        if array.dtype != np.uint8 or array.shape != FRAME_SHAPE or not array.flags.c_contiguous:
            raise CaptureError(
                f"{label} must be contiguous uint8 with shape {FRAME_SHAPE}; "
                f"got dtype={array.dtype}, shape={array.shape}"
            )
        return array

    def _write_frame(self, item: tuple[int, np.ndarray, np.ndarray, dict[str, Any]]) -> None:
        frame_id, gray, mask, metadata = item
        base = f"frames/{frame_id:06d}"
        gray_rel = f"{base}.gray.npy"
        mask_rel = f"{base}.mask.npy"
        record = {
            "frame_id": frame_id,
            "gray_path": gray_rel,
            "mask_path": mask_rel,
            "gray_sha256": hashlib.sha256(memoryview(gray)).hexdigest(),
            "mask_sha256": hashlib.sha256(memoryview(mask)).hexdigest(),
            "metadata": metadata,
        }
        line = _json_bytes(record) + b"\n"
        estimate = int(gray.nbytes + mask.nbytes + 1024 + len(line))
        if self._bytes_written + estimate > self.max_bytes - 32768:
            raise CaptureError("capture files exceed configured capacity limit")
        gray_path = self.output_dir / gray_rel
        mask_path = self.output_dir / mask_rel
        gray_tmp = gray_path.with_suffix(gray_path.suffix + ".tmp")
        mask_tmp = mask_path.with_suffix(mask_path.suffix + ".tmp")
        try:
            with gray_tmp.open("wb") as stream:
                np.save(stream, gray, allow_pickle=False)
                stream.flush()
                os.fsync(stream.fileno())
            with mask_tmp.open("wb") as stream:
                np.save(stream, mask, allow_pickle=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(gray_tmp, gray_path)
            os.replace(mask_tmp, mask_path)
            with self._records_path.open("ab") as stream:
                stream.write(line)
                stream.flush()
            self._bytes_written += gray_path.stat().st_size + mask_path.stat().st_size + len(line)
            self._written += 1
        finally:
            for temp in (gray_tmp, mask_tmp):
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass

    def _writer_main(self) -> None:
        if self.defer_writes_until_close:
            # close() starts this thread only after the runner has checked that
            # both DMA channels are idle. Slow storage cannot back up sampling.
            for frame_id, metadata in enumerate(self._deferred_metadata):
                try:
                    if self._error is not None:
                        self._dropped += 1
                        continue
                    self._write_frame((frame_id, self.raw_gray8[frame_id],
                                       self.raw_binary8[frame_id], metadata))
                except BaseException as exc:
                    self._dropped += 1
                    if self._error is None:
                        self._error = f"{type(exc).__name__}: {exc}"
            return
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                if self._error is not None:
                    self._dropped += 1
                    continue
                self._write_frame(item)
            except BaseException as exc:
                self._dropped += 1
                if self._error is None:
                    self._error = f"{type(exc).__name__}: {exc}"
            finally:
                self._queue.task_done()

    def close(self) -> dict[str, Any]:
        if self._closed:
            return dict(self._manifest)
        self._closed = True
        flush_timeout_s = 300.0 if self.defer_writes_until_close else 35.0
        deadline = time.monotonic() + flush_timeout_s
        if self.defer_writes_until_close:
            self._thread.start()
        stop_sent = False
        while (not self.defer_writes_until_close and self._thread.is_alive()
               and time.monotonic() < deadline and not stop_sent):
            try:
                self._queue.put(None, timeout=min(1.0, max(0.01, deadline - time.monotonic())))
                stop_sent = True
            except queue.Full:
                continue
        remaining = max(0.0, deadline - time.monotonic())
        self._thread.join(timeout=remaining)
        writer_active = self._thread.is_alive()
        if writer_active:
            self._error = self._error or (
                "deferred writer did not stop within 300 seconds"
                if self.defer_writes_until_close else
                "writer thread did not stop within thirty-five seconds")
            self._manifest.update({
                "status": "INCOMPLETE",
                "writer_active": True,
                "completed_utc": datetime.now(timezone.utc).isoformat(),
                "captured_frames": self._written,
                "accepted_frames": self._accepted,
                "dropped_frames": self._dropped,
                "submitted_frames": self._submitted,
                "missing_frames": max(0, self.expected_frames - self._written - self._dropped),
                "bytes_written": self._bytes_written,
                "frame_records_sha256": None,
                "error": self._error,
            })
            self._write_manifest()
            return dict(self._manifest)

        if self._records_path.exists():
            with self._records_path.open("ab") as stream:
                stream.flush()
                os.fsync(stream.fileno())
        complete = (self._error is None and self._written == self.expected_frames
                    and self._accepted == self.expected_frames and self._dropped == 0
                    and self._raw_frames_recorded == self.expected_frames
                    and self._source_frames_recorded == set(self.source_frame_ids))
        if self._error is None and self._raw_frames_recorded != self.expected_frames:
            self._error = (f"raw frame capture is incomplete: {self._raw_frames_recorded}/"
                           f"{self.expected_frames}")
        if (self._error is None
                and self._source_frames_recorded != set(self.source_frame_ids)):
            self._error = "selected original-resolution source frame capture is incomplete"
        raw_frame_archive = None
        source_frame_archive = None
        if complete:
            try:
                raw_frame_archive = self._write_raw_frame_archive()
                if self.source_frame_ids:
                    source_frame_archive = self._write_source_frame_archive()
            except Exception as exc:
                self._error = f"{type(exc).__name__}: {exc}"
                complete = False
        self._manifest.update({
            "status": "COMPLETE" if complete else "INCOMPLETE",
            "writer_active": False,
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "captured_frames": self._written,
            "accepted_frames": self._accepted,
            "dropped_frames": self._dropped,
            "submitted_frames": self._submitted,
            "missing_frames": max(0, self.expected_frames - self._written - self._dropped),
            "bytes_written": self._bytes_written,
            "frame_records_sha256": (hashlib.sha256(self._records_path.read_bytes()).hexdigest()
                                      if self._records_path.exists() else None),
            "raw_frame_archive": raw_frame_archive,
            "source_frame_archive": source_frame_archive,
            "error": self._error,
        })
        self._write_manifest()
        return dict(self._manifest)

    def _write_raw_frame_archive(self) -> dict[str, Any]:
        archive_path = self.output_dir / RAW_FRAME_ARCHIVE_NAME
        archive_tmp = archive_path.with_suffix(archive_path.suffix + ".tmp")
        sidecar_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
        sidecar_tmp = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
        gray = self.raw_gray8[:self.expected_frames]
        mask = self.raw_binary8[:self.expected_frames]
        gray_sha256 = hashlib.sha256(memoryview(gray)).hexdigest()
        mask_sha256 = hashlib.sha256(memoryview(mask)).hexdigest()
        embedded_metadata = {
            "schema_version": 1,
            "captured_frames": self.expected_frames,
            "frame_shape": list(FRAME_SHAPE),
            "array_shape": [self.expected_frames, *FRAME_SHAPE],
            "dtype": "uint8",
            "raw_gray8_sha256": gray_sha256,
            "raw_binary8_sha256": mask_sha256,
        }
        try:
            with archive_tmp.open("xb") as stream:
                np.savez(stream, raw_gray8=gray, raw_binary8=mask,
                         archive_metadata_json=np.asarray(
                             _json_bytes(embedded_metadata).decode("utf-8")))
                stream.flush()
                os.fsync(stream.fileno())
            archive_sha256 = _sha256_file(archive_tmp)
            os.replace(archive_tmp, archive_path)
            sidecar_text = f"{archive_sha256}  {archive_path.name}\n"
            with sidecar_tmp.open("x", encoding="ascii", newline="\n") as stream:
                stream.write(sidecar_text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(sidecar_tmp, sidecar_path)
            committed_bytes = (self._bytes_written + archive_path.stat().st_size
                               + sidecar_path.stat().st_size)
            if committed_bytes > self.max_bytes - 8192:
                raise CaptureError("capture files exceed configured capacity limit")
            return {
                "filename": archive_path.name,
                "sha256_sidecar": sidecar_path.name,
                "sha256": archive_sha256,
                "size_bytes": archive_path.stat().st_size,
                "captured_frames": self.expected_frames,
                "shape": [self.expected_frames, *FRAME_SHAPE],
                "dtype": "uint8",
                "raw_gray8_sha256": gray_sha256,
                "raw_binary8_sha256": mask_sha256,
            }
        finally:
            for temporary in (archive_tmp, sidecar_tmp):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _write_source_frame_archive(self) -> dict[str, Any]:
        if self.source_gray8 is None or not self.source_frame_ids:
            raise CaptureError("source frame archive was not configured")
        archive_path = self.output_dir / SOURCE_FRAME_ARCHIVE_NAME
        archive_tmp = archive_path.with_suffix(archive_path.suffix + ".tmp")
        sidecar_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
        sidecar_tmp = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
        source = self.source_gray8
        source_sha256 = hashlib.sha256(memoryview(source)).hexdigest()
        embedded_metadata = {
            "schema_version": 1,
            "frame_ids": list(self.source_frame_ids),
            "frame_shape": list(SOURCE_FRAME_SHAPE),
            "array_shape": list(source.shape),
            "dtype": "uint8",
            "source_gray8_sha256": source_sha256,
        }
        try:
            with archive_tmp.open("xb") as stream:
                np.savez(stream, source_frame_ids=np.asarray(self.source_frame_ids, dtype=np.uint32),
                         source_gray8=source,
                         archive_metadata_json=np.asarray(
                             _json_bytes(embedded_metadata).decode("utf-8")))
                stream.flush()
                os.fsync(stream.fileno())
            archive_sha256 = _sha256_file(archive_tmp)
            os.replace(archive_tmp, archive_path)
            sidecar_text = f"{archive_sha256}  {archive_path.name}\n"
            with sidecar_tmp.open("x", encoding="ascii", newline="\n") as stream:
                stream.write(sidecar_text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(sidecar_tmp, sidecar_path)
            raw_path = self.output_dir / RAW_FRAME_ARCHIVE_NAME
            raw_sidecar = raw_path.with_suffix(".npz.sha256")
            committed_bytes = (self._bytes_written + archive_path.stat().st_size
                               + sidecar_path.stat().st_size
                               + (raw_path.stat().st_size if raw_path.exists() else 0)
                               + (raw_sidecar.stat().st_size if raw_sidecar.exists() else 0))
            if committed_bytes > self.max_bytes - 8192:
                raise CaptureError("capture files exceed configured capacity limit")
            return {"filename": archive_path.name,
                    "sha256_sidecar": f"{archive_path.name}.sha256",
                    "sha256": archive_sha256,
                    "size_bytes": archive_path.stat().st_size,
                    "frame_ids": list(self.source_frame_ids),
                    "shape": list(source.shape), "dtype": "uint8",
                    "source_gray8_sha256": source_sha256}
        finally:
            for temporary in (archive_tmp, sidecar_tmp):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _write_manifest(self) -> None:
        target = self.output_dir / "manifest.json"
        temp = target.with_suffix(".json.tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(self._manifest, stream, ensure_ascii=False, indent=2, sort_keys=True,
                      allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)


def load_dataset(output_dir: str | Path, *, require_complete: bool = True
                 ) -> tuple[dict[str, Any], tuple[CapturedFrame, ...]]:
    root = Path(output_dir).resolve(strict=True)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported capture dataset schema version")
    if manifest.get("writer_active") is True:
        raise ValueError("capture writer did not stop; dataset is not stable for replay or inspection")
    if require_complete and manifest.get("status") != "COMPLETE":
        raise ValueError(f"capture dataset is {manifest.get('status')}; complete evidence is required")
    lines_path = root / "frame-records.jsonl"
    if not lines_path.is_file():
        raise ValueError("dataset is missing frame-records.jsonl")
    raw_records = [json.loads(line) for line in lines_path.read_text(encoding="utf-8").splitlines() if line]
    expected = manifest.get("expected_frames")
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
        raise ValueError("dataset expected_frames metadata is invalid")
    if len(raw_records) > expected:
        raise ValueError("dataset contains more frame records than requested")
    if require_complete and len(raw_records) != expected:
        raise ValueError(f"dataset frame record count {len(raw_records)} does not match expected {expected}")
    if manifest.get("captured_frames") != len(raw_records):
        raise ValueError("manifest captured frame count does not match frame records")
    if require_complete and (manifest.get("status") != "COMPLETE"
                             or manifest.get("dropped_frames") != 0
                             or manifest.get("missing_frames") != 0):
        raise ValueError("dataset manifest reports incomplete or dropped evidence")
    records_hash = hashlib.sha256(lines_path.read_bytes()).hexdigest()
    if manifest.get("frame_records_sha256") != records_hash:
        raise ValueError("frame record manifest hash mismatch")
    config = manifest.get("config")
    if not isinstance(config, dict):
        raise ValueError("dataset manifest config is missing or malformed")
    config_digest = hashlib.sha256(_json_bytes(config)).hexdigest()
    reserved_config_keys = {
        "algorithm", "threshold", "input_resolution", "mask_resolution",
        "roi_bounds_convention", "counter_config",
    }
    effective_config = config.get("counter_config")
    if effective_config is None:
        effective_config = {key: value for key, value in config.items()
                            if key not in reserved_config_keys}
    if not isinstance(effective_config, dict):
        raise ValueError("dataset effective counter config is malformed")
    raw_gray_archive = None
    raw_mask_archive = None
    archive_info = manifest.get("raw_frame_archive")
    if archive_info is not None:
        if not isinstance(archive_info, dict):
            raise ValueError("raw frame archive metadata is malformed")
        if (archive_info.get("filename") != RAW_FRAME_ARCHIVE_NAME
                or archive_info.get("sha256_sidecar") != f"{RAW_FRAME_ARCHIVE_NAME}.sha256"):
            raise ValueError("raw frame archive path metadata is invalid")
        archive_path = root / RAW_FRAME_ARCHIVE_NAME
        sidecar_path = root / f"{RAW_FRAME_ARCHIVE_NAME}.sha256"
        if not archive_path.is_file() or not sidecar_path.is_file():
            raise ValueError("raw frame NPZ or its SHA-256 sidecar is missing")
        archive_sha256 = archive_info.get("sha256")
        if (not isinstance(archive_sha256, str)
                or _sha256_file(archive_path) != archive_sha256):
            raise ValueError("raw frame NPZ full-file SHA-256 mismatch")
        expected_sidecar = f"{archive_sha256}  {RAW_FRAME_ARCHIVE_NAME}"
        if sidecar_path.read_text(encoding="ascii").strip() != expected_sidecar:
            raise ValueError("raw frame NPZ SHA-256 sidecar mismatch")
        if archive_info.get("size_bytes") != archive_path.stat().st_size:
            raise ValueError("raw frame NPZ size does not match its manifest")
        with np.load(archive_path, allow_pickle=False) as stored:
            try:
                raw_gray_archive = stored["raw_gray8"]
                raw_mask_archive = stored["raw_binary8"]
                embedded_metadata = json.loads(str(stored["archive_metadata_json"].item()))
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("raw frame NPZ members or metadata are invalid") from exc
        expected_shape = (expected, *FRAME_SHAPE)
        for label, array in (("raw_gray8", raw_gray_archive),
                             ("raw_binary8", raw_mask_archive)):
            if array.dtype != np.uint8 or array.shape != expected_shape or not array.flags.c_contiguous:
                raise ValueError(f"raw frame NPZ {label} geometry or dtype mismatch")
        gray_digest = hashlib.sha256(memoryview(raw_gray_archive)).hexdigest()
        mask_digest = hashlib.sha256(memoryview(raw_mask_archive)).hexdigest()
        if (archive_info.get("captured_frames") != expected
                or archive_info.get("shape") != list(expected_shape)
                or archive_info.get("dtype") != "uint8"
                or archive_info.get("raw_gray8_sha256") != gray_digest
                or archive_info.get("raw_binary8_sha256") != mask_digest
                or embedded_metadata.get("captured_frames") != expected
                or embedded_metadata.get("schema_version") != 1
                or embedded_metadata.get("array_shape") != list(expected_shape)
                or embedded_metadata.get("frame_shape") != list(FRAME_SHAPE)
                or embedded_metadata.get("dtype") != "uint8"
                or embedded_metadata.get("raw_gray8_sha256") != gray_digest
                or embedded_metadata.get("raw_binary8_sha256") != mask_digest):
            raise ValueError("raw frame NPZ array SHA-256 or embedded metadata mismatch")
    source_ids = manifest.get("source_frame_ids", [])
    if (not isinstance(source_ids, list)
            or any(isinstance(frame_id, bool) or not isinstance(frame_id, int)
                   or not 0 <= frame_id < expected for frame_id in source_ids)
            or len(set(source_ids)) != len(source_ids)):
        raise ValueError("selected source-frame ID metadata is invalid")
    source_archive_info = manifest.get("source_frame_archive")
    if source_archive_info is None:
        if source_ids:
            raise ValueError("selected original source frames are missing their NPZ archive")
    else:
        if not source_ids or not isinstance(source_archive_info, dict):
            raise ValueError("source frame archive metadata is malformed")
        if (source_archive_info.get("filename") != SOURCE_FRAME_ARCHIVE_NAME
                or source_archive_info.get("sha256_sidecar")
                != f"{SOURCE_FRAME_ARCHIVE_NAME}.sha256"
                or source_archive_info.get("frame_ids") != source_ids):
            raise ValueError("source frame archive path or index metadata is invalid")
        source_path = root / SOURCE_FRAME_ARCHIVE_NAME
        source_sidecar = root / f"{SOURCE_FRAME_ARCHIVE_NAME}.sha256"
        if not source_path.is_file() or not source_sidecar.is_file():
            raise ValueError("source frame NPZ or its SHA-256 sidecar is missing")
        source_archive_sha256 = source_archive_info.get("sha256")
        if (not isinstance(source_archive_sha256, str)
                or _sha256_file(source_path) != source_archive_sha256):
            raise ValueError("source frame NPZ full-file SHA-256 mismatch")
        if (source_sidecar.read_text(encoding="ascii").strip()
                != f"{source_archive_sha256}  {SOURCE_FRAME_ARCHIVE_NAME}"):
            raise ValueError("source frame NPZ SHA-256 sidecar mismatch")
        if source_archive_info.get("size_bytes") != source_path.stat().st_size:
            raise ValueError("source frame NPZ size does not match its manifest")
        with np.load(source_path, allow_pickle=False) as stored:
            try:
                source_ids_array = stored["source_frame_ids"]
                source_gray_archive = stored["source_gray8"]
                source_metadata = json.loads(str(stored["archive_metadata_json"].item()))
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("source frame NPZ members or metadata are invalid") from exc
        expected_source_shape = (len(source_ids), *SOURCE_FRAME_SHAPE)
        if (source_ids_array.dtype != np.uint32
                or source_ids_array.shape != (len(source_ids),)
                or source_ids_array.tolist() != source_ids
                or source_gray_archive.dtype != np.uint8
                or source_gray_archive.shape != expected_source_shape
                or not source_gray_archive.flags.c_contiguous):
            raise ValueError("source frame NPZ array geometry, dtype, or index mismatch")
        source_pixels_sha256 = hashlib.sha256(memoryview(source_gray_archive)).hexdigest()
        if (source_archive_info.get("shape") != list(expected_source_shape)
                or source_archive_info.get("dtype") != "uint8"
                or source_archive_info.get("source_gray8_sha256") != source_pixels_sha256
                or source_metadata.get("schema_version") != 1
                or source_metadata.get("frame_ids") != source_ids
                or source_metadata.get("frame_shape") != list(SOURCE_FRAME_SHAPE)
                or source_metadata.get("array_shape") != list(expected_source_shape)
                or source_metadata.get("dtype") != "uint8"
                or source_metadata.get("source_gray8_sha256") != source_pixels_sha256):
            raise ValueError("source frame NPZ pixel hash or embedded metadata mismatch")
    frames = []
    for expected_id, record in enumerate(raw_records):
        if record.get("frame_id") != expected_id:
            raise ValueError(f"frame ID sequence mismatch at position {expected_id}")
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError(f"frame metadata is missing at position {expected_id}")
        try:
            metadata = _validate_frame_metadata(expected_id, metadata)
        except CaptureError as exc:
            raise ValueError(str(exc)) from exc
        if metadata.get("capture_config_sha256") != config_digest:
            raise ValueError(f"frame {expected_id} capture config hash does not match manifest")
        if metadata.get("algorithm_config") != effective_config:
            raise ValueError(f"frame {expected_id} algorithm config does not match manifest config")
        if "threshold" in config and metadata.get("threshold") != config["threshold"]:
            raise ValueError(f"frame {expected_id} threshold does not match manifest config")
        for key in ("input_resolution", "mask_resolution"):
            if key in config and config[key] != [WIDTH, HEIGHT]:
                raise ValueError(f"dataset manifest {key} is inconsistent with the capture geometry")
        arrays: dict[str, np.ndarray] = {}
        for label in ("gray", "mask"):
            relative = record.get(f"{label}_path")
            if not isinstance(relative, str):
                raise ValueError(f"frame {expected_id} is missing {label} path")
            path = (root / relative).resolve(strict=True)
            if root not in path.parents:
                raise ValueError("dataset array path escapes dataset directory")
            array = np.load(path, allow_pickle=False)
            if hashlib.sha256(array.tobytes(order="C")).hexdigest() != record.get(f"{label}_sha256"):
                raise ValueError(f"frame {expected_id} {label} pixel hash mismatch")
            if array.dtype != np.uint8 or array.shape != FRAME_SHAPE or not array.flags.c_contiguous:
                raise ValueError(f"frame {expected_id} {label} array geometry or dtype mismatch")
            arrays[label] = array
        if np.any((arrays["mask"] != 0) & (arrays["mask"] != 0xFF)):
            raise ValueError(f"frame {expected_id} mask is not raw Binary8")
        if raw_gray_archive is not None and not np.array_equal(
                arrays["gray"], raw_gray_archive[expected_id]):
            raise ValueError(f"frame {expected_id} Gray8 differs from the combined NPZ")
        if raw_mask_archive is not None and not np.array_equal(
                arrays["mask"], raw_mask_archive[expected_id]):
            raise ValueError(f"frame {expected_id} Binary8 differs from the combined NPZ")
        frames.append(CapturedFrame(expected_id, arrays["gray"], arrays["mask"], metadata))
    return manifest, tuple(frames)


def create_ground_truth_template() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "UNANNOTATED",
        "experiment_id": "",
        "experiment_type": "A_SINGLE | B_FIVE_SPACED | C_CLOSE_PAIR | D_EMPTY_BELT",
        "provenance": "human_observation | independent_video | external_measurement",
        "observer": "",
        "independent_record_reference": "",
        "actual_window_entity_count": None,
        "window_count_confirmed": False,
        "entities": [{
            "entity_id": "E001",
            "insertion_order": 1,
            "put_in_monotonic_ns": None,
            "entered_detection_window": None,
            "first_visible_frame": None,
            "last_visible_frame": None,
            "fully_visible": None,
            "completed_pass": None,
            "track_ids": [],
            "identity_reviewed": False,
            "identity_evidence": "",
            "notes": "",
        }],
        "empty_belt_first_frame": None,
        "empty_belt_last_frame": None,
        "empty_belt_observed_frames": None,
        "notes": "Ground truth must be entered from an independent observation source.",
    }


def evaluate_entities(ground_truth: Mapping[str, Any],
                      algorithm_frames: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]
                      ) -> dict[str, Any]:
    """Compare independently reviewed physical entities with replay events.

    A PASS requires complete independent observation and an event inside each
    entity's manually confirmed visible frame interval. Aggregate count alone
    can never establish entity correspondence.
    """
    if not isinstance(ground_truth, Mapping):
        raise ValueError("ground truth must be a mapping")

    def not_verified(reason: str) -> dict[str, Any]:
        return {"status": "NOT VERIFIED", "reason": reason, "entities": [],
                "duplicate_count": None, "missed_count": None,
                "wrong_merge_count": None, "unmatched_event_count": None}

    allowed_sources = {"human_observation", "independent_video", "external_measurement"}
    entities = ground_truth.get("entities")
    window_count = ground_truth.get("actual_window_entity_count")
    if (ground_truth.get("status") != "CONFIRMED"
            or ground_truth.get("provenance") not in allowed_sources
            or not isinstance(ground_truth.get("observer"), str)
            or not ground_truth["observer"].strip()
            or not isinstance(ground_truth.get("independent_record_reference"), str)
            or not ground_truth["independent_record_reference"].strip()
            or ground_truth.get("window_count_confirmed") is not True
            or isinstance(window_count, bool) or not isinstance(window_count, int)
            or window_count < 0 or not isinstance(entities, list)):
        return not_verified("independent Ground Truth is incomplete or unconfirmed")
    if not algorithm_frames:
        return not_verified("algorithm replay has no frame records")

    frame_ids = []
    event_rows = []
    track_observations: dict[int, list[dict[str, Any]]] = {}
    for frame in algorithm_frames:
        if not isinstance(frame, Mapping):
            return not_verified("algorithm replay contains a malformed frame record")
        frame_id = frame.get("frame_id")
        if isinstance(frame_id, bool) or not isinstance(frame_id, int) or frame_id < 0:
            return not_verified("algorithm replay contains a missing or invalid frame ID")
        frame_ids.append(frame_id)
        detections = frame.get("detections")
        raw_detections = frame.get("raw_detections")
        associations = frame.get("associations")
        events = frame.get("events")
        if (not isinstance(detections, list) or not isinstance(raw_detections, list)
                or not isinstance(associations, list) or not isinstance(events, list)):
            return not_verified(f"algorithm frame {frame_id} has incomplete raw detections, detections, associations, or events")
        for association in associations:
            if not isinstance(association, Mapping):
                return not_verified(f"algorithm frame {frame_id} has a malformed association")
            track_id = association.get("track_id")
            detection_index = association.get("detection_index")
            if (isinstance(track_id, bool) or not isinstance(track_id, int) or track_id < 1
                    or isinstance(detection_index, bool) or not isinstance(detection_index, int)
                    or not 0 <= detection_index < len(detections)):
                return not_verified(f"algorithm frame {frame_id} has a malformed association ID/index")
            track_observations.setdefault(track_id, []).append({
                "frame_id": frame_id,
                "detection": detections[detection_index],
                "association": association,
            })
        for event in events:
            if not isinstance(event, Mapping):
                return not_verified(f"algorithm frame {frame_id} has a malformed count event")
            track_id = event.get("track_id")
            event_frame = event.get("frame_id")
            if (isinstance(track_id, bool) or not isinstance(track_id, int) or track_id < 1
                    or isinstance(event_frame, bool) or not isinstance(event_frame, int)
                    or event_frame != frame_id):
                return not_verified(f"algorithm frame {frame_id} has a missing or mismatched event frame ID")
            event_rows.append({"track_id": track_id, "frame_id": event_frame})
    if frame_ids != list(range(frame_ids[0], frame_ids[0] + len(frame_ids))):
        return not_verified("algorithm replay frame IDs are not contiguous")
    frame_min, frame_max = frame_ids[0], frame_ids[-1]

    if window_count == 0:
        empty_first = ground_truth.get("empty_belt_first_frame")
        empty_last = ground_truth.get("empty_belt_last_frame")
        empty_frames = ground_truth.get("empty_belt_observed_frames")
        if (isinstance(empty_first, bool) or not isinstance(empty_first, int)
                or isinstance(empty_last, bool) or not isinstance(empty_last, int)
                or isinstance(empty_frames, bool) or not isinstance(empty_frames, int)
                or empty_first < frame_min or empty_last > frame_max
                or empty_last < empty_first
                or empty_frames != empty_last - empty_first + 1):
            return not_verified("empty-belt observation frame range is missing or inconsistent")
        if len(entities) != 0:
            return not_verified("empty-belt Ground Truth must not contain entity rows")
        observed_events = [event for event in event_rows
                           if empty_first <= event["frame_id"] <= empty_last]
        observed_frames = [frame for frame in algorithm_frames
                           if empty_first <= frame["frame_id"] <= empty_last]
        false_positive_detections = sum(len(frame["detections"]) for frame in observed_frames)
        false_positive_raw_components = sum(len(frame["raw_detections"])
                                            for frame in observed_frames)
        failed = bool(observed_events or false_positive_detections
                      or false_positive_raw_components)
        return {"status": "FAIL" if failed else "PASS",
                "ground_truth_entity_count": 0,
                "algorithm_event_count": len(event_rows), "entities": [],
                "duplicate_count": 0, "missed_count": 0, "wrong_merge_count": 0,
                "unmatched_event_count": len(observed_events),
                "false_positive_detection_count": false_positive_detections,
                "false_positive_raw_component_count": false_positive_raw_components,
                "ignored_outside_observation_event_count": len(event_rows) - len(observed_events),
                "empty_belt_frame_range": [empty_first, empty_last],
                "empty_belt_observed_frames": empty_frames}

    if any(not isinstance(entity, Mapping)
           or not isinstance(entity.get("entered_detection_window"), bool)
           for entity in entities):
        return not_verified("entity rows must explicitly state whether they entered the window")
    entered = [entity for entity in entities if entity["entered_detection_window"] is True]
    if len(entered) != window_count:
        return not_verified("entity rows do not match confirmed actual window count")

    ids = []
    orders = []
    track_owners: dict[int, list[str]] = {}
    for entity in entered:
        entity_id = entity.get("entity_id")
        order = entity.get("insertion_order")
        inserted_ns = entity.get("put_in_monotonic_ns")
        first = entity.get("first_visible_frame")
        last = entity.get("last_visible_frame")
        track_ids = entity.get("track_ids")
        if (not isinstance(entity_id, str) or not entity_id.strip()
                or isinstance(order, bool) or not isinstance(order, int) or order < 1
                or isinstance(inserted_ns, bool) or not isinstance(inserted_ns, int) or inserted_ns < 1
                or isinstance(first, bool) or not isinstance(first, int)
                or isinstance(last, bool) or not isinstance(last, int)
                or first < frame_min or last > frame_max or first > last
                or entity.get("fully_visible") is not True
                or entity.get("completed_pass") is not True
                or not isinstance(entity.get("identity_evidence"), str)
                or not entity["identity_evidence"].strip()
                or entity.get("identity_reviewed") is not True
                or not isinstance(track_ids, list)
                or any(isinstance(track_id, bool) or not isinstance(track_id, int) or track_id < 1
                       for track_id in track_ids)
                or len(set(track_ids)) != len(track_ids)):
            return not_verified("entity visibility, pass completion, insertion, identity evidence, or Track ID mapping is incomplete")
        ids.append(entity_id)
        orders.append(order)
        for track_id in track_ids:
            track_owners.setdefault(track_id, []).append(entity_id)
    if len(set(ids)) != len(ids):
        return not_verified("duplicate Ground Truth Entity ID rows are ambiguous")
    if len(set(orders)) != len(orders):
        return not_verified("duplicate insertion_order values are ambiguous")

    wrong_track_ids = {track_id for track_id, owners in track_owners.items()
                       if len(owners) > 1}
    wrong_merge_count = len(wrong_track_ids)
    by_entity = []
    mapped_events: set[int] = set()
    duplicate_count = 0
    missed_count = 0
    for entity in entered:
        entity_id = entity["entity_id"]
        first, last = entity["first_visible_frame"], entity["last_visible_frame"]
        track_ids = set(entity["track_ids"])
        candidates = [(index, event) for index, event in enumerate(event_rows)
                      if event["track_id"] in track_ids]
        in_window = [(index, event) for index, event in candidates
                     if first <= event["frame_id"] <= last]
        mapped_events.update(index for index, _ in in_window)
        duplicates = max(0, len(in_window) - 1)
        duplicate_count += duplicates
        missing = len(in_window) == 0
        missed_count += int(missing)
        observations = [observation for track_id in track_ids
                        for observation in track_observations.get(track_id, [])]
        detected_frames = sorted({observation["frame_id"] for observation in observations})
        event_frames = [event["frame_id"] for _, event in in_window]
        out_of_window_event_frames = [event["frame_id"] for _, event in candidates
                                      if not first <= event["frame_id"] <= last]
        evidence_candidates = sorted(set(detected_frames + event_frames + out_of_window_event_frames))
        if len(evidence_candidates) > 8:
            positions = {0, len(evidence_candidates) - 1,
                         len(evidence_candidates) // 2}
            positions.update(index for index, value in enumerate(evidence_candidates)
                             if value in event_frames or value in out_of_window_event_frames)
            evidence_candidates = [evidence_candidates[index]
                                   for index in sorted(positions)[:8]]
        by_entity.append({
            "ground_truth_entity_id": entity_id,
            "actual_frame_range": [first, last],
            "track_ids": sorted(track_ids),
            "detected_frame_range": ([detected_frames[0], detected_frames[-1]]
                                      if detected_frames else None),
            "detected_frames": detected_frames,
            "event_count": len(in_window),
            "event_frames": event_frames,
            "out_of_window_event_frames": out_of_window_event_frames,
            "key_evidence_frames": evidence_candidates,
            "duplicate_count": duplicates,
            "missed": missing,
            "wrong_merge": any(track_id in wrong_track_ids for track_id in track_ids),
        })
    unmatched = len(event_rows) - len(mapped_events)
    failed = bool(duplicate_count or missed_count or wrong_merge_count or unmatched)
    return {"status": "FAIL" if failed else "PASS",
            "ground_truth_entity_count": window_count,
            "algorithm_event_count": len(event_rows),
            "entities": by_entity,
            "duplicate_count": duplicate_count,
            "missed_count": missed_count,
            "wrong_merge_count": wrong_merge_count,
            "unmatched_event_count": unmatched}
