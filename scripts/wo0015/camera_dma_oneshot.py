"""Prepare one OV9281 Gray8 frame and run a separately authorized PL DMA stage.

Importing this module does not open V4L2, import PYNQ, or touch MMIO. The
``prepare`` mode writes a hash-bound camera packet and Golden; only the
``camera-dma-single`` mode can attach to the loaded Overlay and start DMA.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping

import numpy as np

try:
    from . import board_once, board_preflight as preflight
    from .camera_adapter import gray320x240_to_gray_packet
except ImportError:  # pragma: no cover - direct script execution on PYNQ
    import board_once
    import board_preflight as preflight
    from camera_adapter import gray320x240_to_gray_packet


STEP3_MANIFEST_SHA256 = "2c24709bbe60711c095b5ccbdf4b3582e93e65d98a9538ad2b413136ad942e8b"
CAMERA_STAGE = "camera-dma-single"
CAMERA_GUARD = board_once.DEFAULT_GUARD_DIR / "camera-dma-single-consumed.json"
CAMERA_PASS_RECEIPT = board_once.DEFAULT_GUARD_DIR / "camera-dma-single-pass.json"
_WHITESPACE = b" \t\r\n\v\f"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _camera_result_json_default(value: Any) -> Any:
    """Encode binary metadata losslessly if a board API exposes it in evidence."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {
            "type": "bytes",
            "encoding": "hex",
            "byte_length": len(raw),
            "data": raw.hex(),
        }
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _write_exclusive(path: str | Path, data: bytes, *, mode: int = 0o600) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(str(target), flags, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            dir_fd = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except BaseException:
        try:
            target.unlink()
        except OSError:
            pass
        raise


def _write_json_exclusive(path: str | Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    _write_exclusive(path, encoded)


def _normalize_gray8(image: Any, *, maximum: int | None = None) -> np.ndarray:
    array = np.asarray(image)
    if array.ndim != 2 or array.size == 0:
        raise ValueError("camera source must be a nonempty 2-D grayscale frame")
    if array.dtype == np.uint8:
        return np.ascontiguousarray(array)
    if array.dtype not in (np.uint16, np.int16, np.int32, np.int64):
        raise ValueError("camera grayscale pixels must use an 8-bit or 16-bit integer type")
    values = array.astype(np.int64, copy=False)
    max_value = 65535 if maximum is None else maximum
    if max_value <= 0 or max_value > 65535 or int(values.min()) < 0 or int(values.max()) > max_value:
        raise ValueError("grayscale pixels are outside the declared range")
    scaled = np.rint(values.astype(np.float64) * (255.0 / max_value))
    return np.ascontiguousarray(np.clip(scaled, 0, 255).astype(np.uint8))


def _pgm_token(data: bytes, position: int) -> tuple[bytes, int]:
    size = len(data)
    while position < size:
        byte = data[position]
        if byte in _WHITESPACE:
            position += 1
            continue
        if byte == ord("#"):
            newline = data.find(b"\n", position)
            if newline < 0:
                raise ValueError("PGM header ends inside a comment")
            position = newline + 1
            continue
        break
    start = position
    while position < size and data[position] not in _WHITESPACE + b"#":
        position += 1
    if position == start:
        raise ValueError("PGM header is incomplete")
    return data[start:position], position


def _read_pgm(data: bytes) -> np.ndarray:
    magic, position = _pgm_token(data, 0)
    width_token, position = _pgm_token(data, position)
    height_token, position = _pgm_token(data, position)
    max_token, position = _pgm_token(data, position)
    if magic not in (b"P5", b"P2"):
        raise ValueError("only binary P5 and ASCII P2 grayscale PGM files are supported")
    try:
        width, height, maximum = int(width_token), int(height_token), int(max_token)
    except ValueError as exc:
        raise ValueError("PGM dimensions and max value must be integers") from exc
    if width <= 0 or height <= 0 or not 1 <= maximum <= 65535:
        raise ValueError("PGM dimensions or max value are out of range")
    count = width * height
    if magic == b"P2":
        text = re.sub(rb"#[^\r\n]*", b" ", data[position:]).split()
        if len(text) != count:
            raise ValueError("ASCII PGM pixel count differs from its declared geometry")
        try:
            samples = np.asarray([int(item) for item in text], dtype=np.int64)
        except ValueError as exc:
            raise ValueError("ASCII PGM contains a non-integer pixel") from exc
        if int(samples.min()) < 0 or int(samples.max()) > maximum:
            raise ValueError("ASCII PGM pixel is outside its max value")
    else:
        if position >= len(data) or data[position] not in _WHITESPACE:
            raise ValueError("binary PGM max value is not followed by a header separator")
        if data[position:position + 2] == b"\r\n":
            position += 2
        else:
            position += 1
        sample_bytes = 1 if maximum < 256 else 2
        end = position + count * sample_bytes
        if end != len(data):
            raise ValueError("binary PGM payload size differs from its declared geometry")
        dtype = np.uint8 if sample_bytes == 1 else np.dtype(">u2")
        samples = np.frombuffer(data[position:end], dtype=dtype).astype(np.int64)
        if int(samples.max()) > maximum:
            raise ValueError("binary PGM pixel is outside its max value")
    matrix = samples.reshape(height, width)
    return _normalize_gray8(matrix, maximum=maximum)


def load_gray_file(path: str | Path) -> np.ndarray:
    """Load one 2-D Gray8/Gray16 NPY, PGM, or PNG image without pickle data."""
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".npy":
        try:
            image = np.load(source, allow_pickle=False)
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot load grayscale NPY: {exc}") from exc
        return _normalize_gray8(image)
    if suffix == ".pgm":
        try:
            return _read_pgm(source.read_bytes())
        except OSError as exc:
            raise ValueError(f"cannot read grayscale PGM: {exc}") from exc
    if suffix == ".png":
        try:
            from PIL import Image

            with Image.open(source) as image:
                if image.mode in ("I;16", "I;16L", "I;16B", "I"):
                    return _normalize_gray8(np.asarray(image))
                return np.ascontiguousarray(np.asarray(image.convert("L"), dtype=np.uint8))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot read grayscale PNG: {exc}") from exc
    raise ValueError("camera input must be a .npy, .pgm, or .png grayscale image")


def capture_v4l2_once(device: str = "/dev/video0", *, cv2_module: Any = None) -> np.ndarray:
    """Request monochrome decode and read one single-channel OV9281 frame."""
    if cv2_module is None:
        try:
            import cv2 as cv2_module
        except ImportError as exc:
            raise RuntimeError("V4L2 capture requires OpenCV with CAP_V4L2 support") from exc
    capture = cv2_module.VideoCapture(device, cv2_module.CAP_V4L2)
    try:
        if not capture.isOpened():
            raise RuntimeError(f"cannot open V4L2 camera device {device}")
        fourcc = cv2_module.VideoWriter_fourcc(*"MJPG")
        requested_mode = (
            (cv2_module.CAP_PROP_FOURCC, fourcc),
            (cv2_module.CAP_PROP_FRAME_WIDTH, 640),
            (cv2_module.CAP_PROP_FRAME_HEIGHT, 400),
        )
        if any(not capture.set(prop, value) for prop, value in requested_mode):
            raise RuntimeError("V4L2 rejected the supported MJPG 640x400 capture mode")
        convert_rgb_property = getattr(cv2_module, "CAP_PROP_CONVERT_RGB", None)
        native_conversion_requested = False
        if convert_rgb_property is not None:
            try:
                native_conversion_requested = bool(capture.set(convert_rgb_property, 0))
            except Exception:
                native_conversion_requested = False
        ok, frame = capture.read()
        if not ok or frame is None or not np.asarray(frame).size:
            raise RuntimeError("V4L2 returned no frame on its single capture attempt")
        image = np.asarray(frame)
        fallback = False
        try:
            image = _camera_monochrome_view(
                image, verify_repeated_channels=True, expected_shape=(400, 640))
        except ValueError as first_error:
            frame_shape = image.shape[:2] if image.ndim in (2, 3) else None
            raw_layout_invalid = (image.ndim not in (2, 3)
                                  or (image.ndim == 3 and image.shape[2] not in (1, 3, 4))
                                  or frame_shape != (400, 640))
            if not (native_conversion_requested and raw_layout_invalid
                    and convert_rgb_property is not None):
                raise
            try:
                restored = bool(capture.set(convert_rgb_property, 1))
            except Exception:
                restored = False
            if not restored:
                raise RuntimeError("native monochrome decode was unsupported and RGB fallback was rejected") from first_error
            ok, frame = capture.read()
            if not ok or frame is None:
                raise RuntimeError("RGB fallback returned no V4L2 frame") from first_error
            image = _camera_monochrome_view(
                frame, verify_repeated_channels=True, expected_shape=(400, 640))
            fallback = True
        mode = ("GRAY8_NATIVE" if np.asarray(frame).ndim == 2 or
                (np.asarray(frame).ndim == 3 and np.asarray(frame).shape[2] == 1)
                else "BGR8_MONO_REPEATED")
        print("CAMERA_PIXEL_PATH format=%s convert_rgb_request=%s fallback=%s" % (
            mode, native_conversion_requested, fallback), flush=True)
        return _normalize_gray8(image)
    finally:
        capture.release()


def _camera_monochrome_view(image: Any, *, verify_repeated_channels: bool = False,
                            expected_shape: tuple[int, int] | None = (400, 640)) -> np.ndarray:
    """Return a view of a single-channel frame and optionally check repeated BGR planes."""
    source = np.asarray(image)
    if source.dtype != np.uint8 or source.size == 0:
        raise ValueError("V4L2 camera frame must be a nonempty uint8 image")
    if source.ndim == 2:
        gray = source
    elif source.ndim == 3 and source.shape[2] == 1:
        gray = source[:, :, 0]
    elif source.ndim == 3 and source.shape[2] in (3, 4):
        if verify_repeated_channels and (
                not np.array_equal(source[:, :, 0], source[:, :, 1])
                or not np.array_equal(source[:, :, 0], source[:, :, 2])):
            raise ValueError("decoded OV9281 B, G and R channels are not identical")
        gray = source[:, :, 0]
    else:
        raise ValueError("V4L2 frame has an unsupported channel layout")
    if expected_shape is not None and gray.shape != expected_shape:
        raise ValueError(f"V4L2 frame geometry is {gray.shape}; expected {expected_shape}")
    return gray


def resize_gray8(image: Any, *, width: int = preflight.WIDTH,
                 height: int = preflight.HEIGHT,
                 interpolation: str = "linear") -> np.ndarray:
    """Resize Gray8 to an exact geometry using the selected linear or nearest mapping."""
    source = _normalize_gray8(image)
    if (type(width) is not int or type(height) is not int
            or width <= 0 or height <= 0):
        raise ValueError("resize width and height must be positive integers")
    if interpolation not in ("linear", "nearest"):
        raise ValueError("interpolation must be 'linear' or 'nearest'")
    if source.shape == (height, width):
        return source.copy()
    if interpolation == "nearest":
        import cv2

        return cv2.resize(source, (width, height), interpolation=cv2.INTER_NEAREST)
    source_height, source_width = source.shape
    y = np.linspace(0, source_height - 1, height, dtype=np.float64)
    x = np.linspace(0, source_width - 1, width, dtype=np.float64)
    y0 = np.floor(y).astype(np.int64)
    x0 = np.floor(x).astype(np.int64)
    y1 = np.minimum(y0 + 1, source_height - 1)
    x1 = np.minimum(x0 + 1, source_width - 1)
    wy = (y - y0)[:, None]
    wx = (x - x0)[None, :]
    upper = source[y0[:, None], x0[None, :]] * (1.0 - wx) + source[y0[:, None], x1[None, :]] * wx
    lower = source[y1[:, None], x0[None, :]] * (1.0 - wx) + source[y1[:, None], x1[None, :]] * wx
    resized = upper * (1.0 - wy) + lower * wy
    return np.ascontiguousarray(np.clip(np.floor(resized + 0.5), 0, 255).astype(np.uint8))


def write_pgm(path: str | Path, image: Any) -> None:
    gray = _normalize_gray8(image)
    header = f"P5\n{gray.shape[1]} {gray.shape[0]}\n255\n".encode("ascii")
    _write_exclusive(path, header + gray.tobytes(order="C"))


def prepare_camera_bundle(*, template_manifest_path: str | Path,
                          output_root: str | Path,
                          input_path: str | Path | None = None,
                          capture_device: str | None = None,
                          expected_template_sha256: str | None = None,
                          frame_id: int | None = None,
                          capture_fn: Any = None) -> dict[str, Any]:
    """Capture/load, scale, packetize, Golden-check and freeze a camera run."""
    if (input_path is None) == (capture_device is None):
        raise ValueError("select exactly one of input_path or capture_device")
    template = preflight.validate_manifest(template_manifest_path, expected_template_sha256)
    if len(template.frames) != 3 or not template.ready_for_board:
        raise preflight.PreflightError("camera bridge template must be the reviewed three-frame WO-0015 manifest")
    next_frame_id = template.frames[-1].frame_id + 1
    if next_frame_id > 0xFFFFFFFF:
        raise preflight.PreflightError("template frame ID leaves no room for a camera sequence")
    if frame_id is None:
        first_frame_id = next_frame_id
    elif isinstance(frame_id, bool) or not isinstance(frame_id, int) or frame_id != next_frame_id:
        raise ValueError(f"camera frame_id must increment the template's last ID to {next_frame_id}")
    else:
        first_frame_id = frame_id
    if first_frame_id + 2 > 0xFFFFFFFF:
        raise ValueError("camera frame sequence exceeds uint32 frame_id range")

    if input_path is not None:
        source_gray = load_gray_file(input_path)
        source_label = str(Path(input_path).resolve())
        source_file_sha256 = preflight.sha256_file(input_path)
        capture_kind = "file"
    else:
        capture = capture_fn or capture_v4l2_once
        source_gray = _normalize_gray8(capture(str(capture_device)))
        source_label = str(capture_device)
        source_file_sha256 = None
        capture_kind = "v4l2-single-frame"

    resized = resize_gray8(source_gray,
                           interpolation="nearest" if capture_kind == "v4l2-single-frame"
                           else "linear")
    output_root = Path(output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / f"camera-{uuid.uuid4().hex}"
    run_dir.mkdir(mode=0o700)

    source_image_path = run_dir / "camera-source-gray.pgm"
    resized_image_path = run_dir / "camera-gray-320x240.pgm"
    write_pgm(source_image_path, source_gray)
    write_pgm(resized_image_path, resized)

    frames: list[dict[str, Any]] = []
    frame_files: list[dict[str, Any]] = []
    for offset in range(3):
        current_id = first_frame_id + offset
        packet = gray320x240_to_gray_packet(resized, frame_id=current_id)
        golden = preflight._expected_binary(packet)
        threshold_golden = np.where(resized >= 128, 0xFF, 0x00).astype(np.uint8).tobytes()
        if golden != threshold_golden or len(packet) != preflight.TX_BYTES or len(golden) != preflight.RX_BYTES:
            raise AssertionError("camera packet and independent Binary8 Golden disagree")
        packet_path = run_dir / f"frame-{current_id}.gray8.bin"
        golden_path = run_dir / f"frame-{current_id}.binary.bin"
        _write_exclusive(packet_path, packet)
        _write_exclusive(golden_path, golden)
        frames.append({
            "frame_id": current_id,
            "packet_path": str(packet_path.resolve()),
            "packet_sha256": _sha256(packet),
            "binary_path": str(golden_path.resolve()),
            "binary_sha256": _sha256(golden),
        })
        frame_files.append({
            "frame_id": current_id,
            "packet_path": str(packet_path.resolve()),
            "packet_sha256": _sha256(packet),
            "golden_path": str(golden_path.resolve()),
            "golden_sha256": _sha256(golden),
        })

    manifest = dict(template.manifest)
    manifest["bit_path"] = str(template.bit_path)
    manifest["hwh_path"] = str(template.hwh_path)
    manifest["frames"] = frames
    manifest_path = run_dir / "wo0015-camera-manifest.json"
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    _write_exclusive(manifest_path, manifest_bytes)
    manifest_sha256 = _sha256(manifest_bytes)
    camera_run_path = run_dir / "camera-run.json"
    camera_run = {
        "schema": 1,
        "work_order": "WO-0015",
        "camera_stage": CAMERA_STAGE,
        "capture_kind": capture_kind,
        "source_label": source_label,
        "source_file_sha256": source_file_sha256,
        "source_shape": list(source_gray.shape),
        "source_gray_sha256": _sha256(source_gray.tobytes(order="C")),
        "source_gray_file_sha256": preflight.sha256_file(source_image_path),
        "resized_shape": [preflight.HEIGHT, preflight.WIDTH],
        "resized_gray_sha256": _sha256(resized.tobytes(order="C")),
        "resized_gray_file_sha256": preflight.sha256_file(resized_image_path),
        "threshold": 128,
        "resize": "bilinear, endpoint-aligned, round-to-nearest",
        "channel_mapping": "Gray8 passthrough; no channel expansion",
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": manifest_sha256,
        "template_manifest_path": str(template.manifest_path),
        "template_manifest_sha256": template.manifest_sha256,
        "frame_id_to_send": first_frame_id,
        "reserved_unrun_frame_ids": [first_frame_id + 1, first_frame_id + 2],
        "authorized_transfer_count": 1,
        "frame_artifacts": frame_files,
        "source_gray_path": str(source_image_path.resolve()),
        "resized_gray_path": str(resized_image_path.resolve()),
        "camera_dma_script_sha256": preflight.sha256_file(Path(__file__).resolve()),
        "camera_adapter_sha256": preflight.sha256_file(Path(__file__).with_name("camera_adapter.py")),
        "board_once_sha256": preflight.sha256_file(Path(board_once.__file__).resolve()),
        "board_preflight_sha256": preflight.sha256_file(Path(preflight.__file__).resolve()),
    }
    _write_json_exclusive(camera_run_path, camera_run)
    validated = preflight.validate_manifest(manifest_path, manifest_sha256)
    return {
        "status": "CAMERA_BUNDLE_PREPARED",
        "camera_run_dir": str(run_dir),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": manifest_sha256,
        "camera_run_path": str(camera_run_path.resolve()),
        "source_gray_path": str(source_image_path.resolve()),
        "resized_gray_path": str(resized_image_path.resolve()),
        "packet_path": frame_files[0]["packet_path"],
        "packet_sha256": frame_files[0]["packet_sha256"],
        "golden_path": frame_files[0]["golden_path"],
        "golden_sha256": frame_files[0]["golden_sha256"],
        "frame_id_to_send": first_frame_id,
        "reserved_unrun_frame_ids": [first_frame_id + 1, first_frame_id + 2],
        "manifest_frames": len(validated.frames),
        "tx_bytes": preflight.TX_BYTES,
        "rx_bytes": preflight.RX_BYTES,
    }


class CameraHistoryAdapter:
    """Present the prior single-frame baseline while preserving hardware deltas.

    ``board_once.run_frames`` intentionally supports a DMA-three baseline of
    one earlier frame. This stage validates the complete Step 2 + Step 3
    four-frame history first, then virtualizes only cumulative Checker values
    so its existing per-frame DMA/cache/Golden comparison path remains intact.
    """

    _CUMULATIVE = {
        board_once.CHECKER_PACKET_DONE_COUNT: ("packet_done_count", 1),
        board_once.CHECKER_INPUT_BYTES: ("input_bytes", preflight.TX_BYTES),
        board_once.CHECKER_INPUT_BEATS: ("input_beats", preflight.TX_BEATS),
        board_once.CHECKER_INPUT_TLAST: ("input_tlast", 1),
        board_once.CHECKER_INPUT_CHECKSUM: ("input_checksum", None),
        board_once.CHECKER_OUTPUT_TOTAL: ("output_total", preflight.PIXEL_COUNT),
    }

    def __init__(self, api: Any, *, frame: preflight.FrameArtifact,
                 baseline: Mapping[str, int], prior_frame_id: int):
        if frame.frame_id != prior_frame_id + 1:
            raise preflight.PreflightError("camera frame ID must increment the verified prior Step 3 frame")
        self._api = api
        self._frame = frame
        self._baseline = dict(baseline)
        self._prior_frame_id = prior_frame_id

    def read32(self, base: int, offset: int) -> int:
        value = self._api.read32(base, offset)
        if base != preflight.CHECKER_BASE:
            return value
        raw_frame_id = (value if offset == board_once.CHECKER_FRAME_ID else
                        self._api.read32(preflight.CHECKER_BASE, board_once.CHECKER_FRAME_ID))
        if offset in self._CUMULATIVE:
            field, expected_base = self._CUMULATIVE[offset]
            if expected_base is None:
                expected_base = self._frame.packet_checksum
            raw_start = self._baseline[field]
            delta = (value - raw_start) & 0xFFFFFFFF
            return (expected_base + delta) & 0xFFFFFFFF
        if offset == board_once.CHECKER_FRAME_ID and value == self._prior_frame_id:
            return self._frame.frame_id
        if offset == board_once.CHECKER_OUTPUT_CHECKSUM and raw_frame_id == self._prior_frame_id:
            return self._frame.binary_checksum
        return value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._api, name)


def validate_prior_step3_evidence(evidence_path: str | Path,
                                  manifest_path: str | Path,
                                  expected_manifest_sha256: str) -> preflight.ValidatedRun:
    """Require a verified, same-boot Step 3 report with all three Golden matches."""
    plan = preflight.validate_manifest(manifest_path, expected_manifest_sha256)
    if len(plan.frames) != 3 or plan.boot_id != preflight.AUTHORIZED_BOOT_ID:
        raise preflight.PreflightError("prior Step 3 manifest is not the approved three-frame run")
    try:
        evidence = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise preflight.PreflightError(f"cannot read prior Step 3 PASS evidence: {exc}") from exc
    if not isinstance(evidence, dict) or evidence.get("status") != "DMA_THREE_PASS":
        raise preflight.PreflightError("prior Step 3 result does not report DMA_THREE_PASS")
    if evidence.get("boot_id") != plan.boot_id or evidence.get("manifest_sha256") != plan.manifest_sha256:
        raise preflight.PreflightError("prior Step 3 result boot/manifest identity differs")
    frames = evidence.get("frames")
    if evidence.get("frame_count") != 3 or not isinstance(frames, list) or len(frames) != 3:
        raise preflight.PreflightError("prior Step 3 result must contain exactly three frames")
    for expected, actual in zip(plan.frames, frames):
        if (not isinstance(actual, dict) or actual.get("frame_id") != expected.frame_id
                or actual.get("byte_mismatches") != 0):
            raise preflight.PreflightError("prior Step 3 result requires three ordered frames with zero byte mismatches")
    return plan


def validate_step3_checker_state(api: Any,
                                 step3_plan: preflight.ValidatedRun) -> dict[str, int]:
    """Fail closed unless the loaded Checker/Packer reflect Step 2 plus Step 3."""
    if len(step3_plan.frames) != 3:
        raise preflight.PreflightError("Step 3 manifest must contain three frames")
    snapshot = board_once._checker_snapshot(api)
    frame0, frame1, frame2 = step3_plan.frames
    expected = {
        "status": board_once.CHECKER_PACKET_DONE | board_once.CHECKER_OUTPUT_COMPLETE,
        "error_code": 0,
        "frame_id": frame2.frame_id,
        "packet_done_count": 4,
        "packet_error_count": 0,
        "input_bytes": 4 * preflight.TX_BYTES,
        "input_beats": 4 * preflight.TX_BEATS,
        "input_tlast": 4,
        "input_checksum": (2 * frame0.packet_checksum + frame1.packet_checksum + frame2.packet_checksum) & 0xFFFFFFFF,
        "pixels": preflight.PIXEL_COUNT,
        "sofs": 1,
        "eols": preflight.HEIGHT,
        "output_checksum": frame2.binary_checksum,
        "output_total": 4 * preflight.PIXEL_COUNT,
    }
    for name, value in expected.items():
        if snapshot[name] != value:
            raise preflight.PreflightError(
                f"Checker state after Step 3 differs at {name}: {snapshot[name]} != {value}"
            )
    if api.read32(preflight.CHECKER_BASE, board_once.PACKER_STATUS) != board_once.PACKER_DONE:
        raise preflight.PreflightError("Packer is not in DONE state after Step 3")
    if (api.read32(preflight.CHECKER_BASE, board_once.PACKER_ACCEPTED_PIXELS) != preflight.PIXEL_COUNT
            or api.read32(preflight.CHECKER_BASE, board_once.PACKER_SENT_BEATS) != preflight.RX_BEATS
            or api.read32(preflight.CHECKER_BASE, board_once.PACKER_ERROR_CODE) != 0):
        raise preflight.PreflightError("Packer counters/error do not match the completed final Step 3 frame")
    for label, offset in (("MM2S", board_once.DMA_MM2S_DMASR), ("S2MM", board_once.DMA_S2MM_DMASR)):
        status = api.read32(preflight.DMA_BASE, offset)
        board_once._require_dma_clean(status, label)
        if status & (board_once.DMA_IDLE | board_once.DMA_IOC_IRQ) != (board_once.DMA_IDLE | board_once.DMA_IOC_IRQ):
            raise preflight.PreflightError(f"{label} is not Idle with a completed IOC after Step 3")
    return snapshot


def require_camera_authorization(*, execute: bool, authorize_stage: str | None,
                                 approve_once: bool, manifest_sha256: str,
                                 expected_manifest_sha256: str,
                                 boot_id: str) -> None:
    if execute is not True:
        raise preflight.PreflightError("camera DMA requires --execute")
    if authorize_stage != CAMERA_STAGE:
        raise preflight.PreflightError(f"camera DMA requires --authorize-stage {CAMERA_STAGE}")
    if approve_once is not True:
        raise preflight.PreflightError("camera DMA requires --approve-once")
    expected = preflight._require_digest(expected_manifest_sha256, "camera manifest SHA-256")
    if manifest_sha256 != expected:
        raise preflight.PreflightError("camera manifest SHA-256 differs from the separately approved digest")
    if boot_id != preflight.AUTHORIZED_BOOT_ID:
        raise preflight.PreflightError("camera manifest is not bound to the approved WO-0015 boot")


def _verify_camera_sidecar(plan: preflight.ValidatedRun,
                           sidecar_path: Path) -> dict[str, Any]:
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise preflight.PreflightError(f"cannot read camera bundle sidecar: {exc}") from exc
    if not isinstance(sidecar, dict) or sidecar.get("manifest_sha256") != plan.manifest_sha256:
        raise preflight.PreflightError("camera sidecar does not bind the reviewed manifest")
    if sidecar.get("camera_dma_script_sha256") != preflight.sha256_file(Path(__file__).resolve()):
        raise preflight.PreflightError("camera DMA script differs from the prepared bundle")
    if sidecar.get("camera_adapter_sha256") != preflight.sha256_file(Path(__file__).with_name("camera_adapter.py")):
        raise preflight.PreflightError("camera adapter differs from the prepared bundle")
    if (sidecar.get("board_once_sha256") != preflight.sha256_file(Path(board_once.__file__).resolve())
            or sidecar.get("board_preflight_sha256") != preflight.sha256_file(Path(preflight.__file__).resolve())):
        raise preflight.PreflightError("board executor/preflight sources differ from the prepared bundle")
    if sidecar.get("authorized_transfer_count") != 1 or len(plan.frames) != 3:
        raise preflight.PreflightError("camera bundle must contain one authorized transfer and a three-frame manifest")
    expected_ids = list(range(int(sidecar.get("frame_id_to_send", -1)),
                              int(sidecar.get("frame_id_to_send", -1)) + 3))
    if [frame.frame_id for frame in plan.frames] != expected_ids:
        raise preflight.PreflightError("camera manifest frame IDs differ from its sidecar")
    first = plan.frames[0]
    files = sidecar.get("frame_artifacts")
    if not isinstance(files, list) or len(files) != 3:
        raise preflight.PreflightError("camera sidecar must list all three bound frame artifacts")
    for frame, record in zip(plan.frames, files):
        if (not isinstance(record, dict)
                or record.get("frame_id") != frame.frame_id
                or record.get("packet_sha256") != frame.packet_sha256
                or record.get("golden_sha256") != frame.binary_sha256):
            raise preflight.PreflightError("camera sidecar packet/Golden fingerprints differ from manifest")
    for key in ("source_gray_path", "resized_gray_path"):
        image_path = sidecar.get(key)
        if not isinstance(image_path, str) or not image_path or not Path(image_path).is_file():
            raise preflight.PreflightError(f"camera sidecar image is missing: {key}")
    for path_key, file_hash_key, pixels_hash_key in (
            ("source_gray_path", "source_gray_file_sha256", "source_gray_sha256"),
            ("resized_gray_path", "resized_gray_file_sha256", "resized_gray_sha256")):
        image_path = Path(sidecar[path_key])
        if preflight.sha256_file(image_path) != sidecar.get(file_hash_key):
            raise preflight.PreflightError(f"camera sidecar image file hash differs: {path_key}")
        image = load_gray_file(image_path)
        if _sha256(image.tobytes(order="C")) != sidecar.get(pixels_hash_key):
            raise preflight.PreflightError(f"camera sidecar image pixels differ: {path_key}")
    if first.frame_id != sidecar["frame_id_to_send"]:
        raise preflight.PreflightError("camera sidecar does not select the first manifest frame")
    return sidecar


def _claim_camera_guard(plan: preflight.ValidatedRun) -> dict[str, Any]:
    payload = {
        "schema": 1,
        "work_order": "WO-0015",
        "stage": CAMERA_STAGE,
        "boot_id": plan.boot_id,
        "manifest_sha256": plan.manifest_sha256,
        "frame_id": plan.frames[0].frame_id,
        "consumed": True,
    }
    _write_json_exclusive(CAMERA_GUARD, payload)
    return payload


def _write_camera_result(plan: preflight.ValidatedRun, sidecar: Mapping[str, Any],
                         run_result: Mapping[str, Any], output_path: Path,
                         api: Any, step3_plan: preflight.ValidatedRun,
                         afi1_sync: Mapping[str, Any], afi1_snapshots: list[dict[str, Any]],
                         runtime_preflight: Mapping[str, Any], post_stage_audit: Mapping[str, Any]) -> dict[str, Any]:
    frame_evidence = run_result["frames"][0]
    raw_capture = frame_evidence["raw_capture"]
    rx_path = Path(raw_capture["rx_path"])
    output = rx_path.read_bytes()
    expected = plan.frames[0].binary
    if len(output) != preflight.RX_BYTES or _sha256(output) != raw_capture["rx_sha256"]:
        raise preflight.PreflightError("saved PL output Binary8 size/hash differs from DMA evidence")
    if output != expected or frame_evidence["byte_mismatches"] != 0:
        raise board_once.ExecutionFailure(
            "camera frame PL output differs from its software Golden",
            transfer_started=True,
            dma_start_attempted=True,
            dma_may_be_active=False,
            evidence={"byte_mismatches": sum(a != b for a, b in zip(output, expected))},
        )
    run_dir = Path(sidecar["manifest_path"]).parent
    raw_output_path = run_dir / "pl-binary8-output.bin"
    image_output_path = run_dir / "pl-binary8-output.pgm"
    _write_exclusive(raw_output_path, output)
    write_pgm(image_output_path, np.frombuffer(output, dtype=np.uint8).reshape(preflight.HEIGHT, preflight.WIDTH))
    result = dict(run_result)
    result.update({
        "status": "CAMERA_DMA_SINGLE_PASS",
        "stage": CAMERA_STAGE,
        "boot_id": plan.boot_id,
        "manifest_sha256": plan.manifest_sha256,
        "step3_manifest_sha256": step3_plan.manifest_sha256,
        "frame_id": plan.frames[0].frame_id,
        "byte_mismatches": 0,
        "camera_input": {
            "source_gray_path": sidecar["source_gray_path"],
            "source_gray_sha256": sidecar["source_gray_sha256"],
            "resized_gray_path": sidecar["resized_gray_path"],
            "resized_gray_sha256": sidecar["resized_gray_sha256"],
            "packet_sha256": plan.frames[0].packet_sha256,
            "software_golden_sha256": plan.frames[0].binary_sha256,
            "channel_mapping": "Gray8 passthrough; no channel expansion",
            "threshold": 128,
        },
        "pl_output": {
            "binary_path": str(raw_output_path.resolve()),
            "binary_sha256": _sha256(output),
            "pgm_path": str(image_output_path.resolve()),
            "pgm_sha256": preflight.sha256_file(image_output_path),
            "bytes": len(output),
            "byte_mismatches": 0,
        },
        "afi1_sync": dict(afi1_sync),
        "afi1_snapshots": afi1_snapshots,
        "runtime_preflight": dict(runtime_preflight),
        "post_stage_audit": dict(post_stage_audit),
        "board_accessed": bool(getattr(api, "board_accessed", False)),
        "camera_dma_script_sha256": preflight.sha256_file(Path(__file__).resolve()),
    })
    _write_exclusive(output_path, json.dumps(
        result, ensure_ascii=False, sort_keys=True, indent=2,
        default=_camera_result_json_default,
    ).encode("utf-8") + b"\n")
    return result


def execute_camera_dma(*, manifest_path: str | Path,
                       expected_manifest_sha256: str,
                       step3_manifest_path: str | Path,
                       step3_manifest_sha256: str,
                       step3_evidence_path: str | Path,
                       execute: bool,
                       authorize_stage: str | None,
                       approve_once: bool,
                       timeout_s: float = board_once.DEFAULT_TIMEOUT_S,
                       evidence_path: str | Path | None = None) -> dict[str, Any]:
    """Run one camera frame through the already-loaded PL with separate guards."""
    board_once._validate_timeout(timeout_s)
    plan = preflight.validate_manifest(manifest_path, expected_manifest_sha256)
    sidecar = _verify_camera_sidecar(plan, Path(manifest_path).with_name("camera-run.json"))
    step3_plan = validate_prior_step3_evidence(
        step3_evidence_path, step3_manifest_path, step3_manifest_sha256)
    if step3_plan.manifest_sha256 != STEP3_MANIFEST_SHA256:
        raise preflight.PreflightError("camera stage is bound to the canonical Step 3 manifest")
    if (plan.boot_id != step3_plan.boot_id or plan.build_id != step3_plan.build_id
            or plan.bit_sha256 != step3_plan.bit_sha256 or plan.hwh_sha256 != step3_plan.hwh_sha256):
        raise preflight.PreflightError("camera manifest and Step 3 evidence do not share one build/boot")
    if sidecar.get("template_manifest_sha256") != step3_plan.manifest_sha256:
        raise preflight.PreflightError("camera bundle was not prepared from the completed Step 3 manifest")
    if plan.frames[0].frame_id != step3_plan.frames[-1].frame_id + 1:
        raise preflight.PreflightError("camera frame ID must continue after the completed Step 3 sequence")
    require_camera_authorization(
        execute=execute, authorize_stage=authorize_stage, approve_once=approve_once,
        manifest_sha256=plan.manifest_sha256,
        expected_manifest_sha256=expected_manifest_sha256,
        boot_id=plan.boot_id,
    )
    board_once._assert_no_terminal_failure()
    if CAMERA_GUARD.exists() or CAMERA_GUARD.is_symlink():
        raise preflight.PreflightError("camera DMA one-shot guard is already consumed")
    if CAMERA_PASS_RECEIPT.exists() or CAMERA_PASS_RECEIPT.is_symlink():
        raise preflight.PreflightError("camera DMA PASS receipt already exists")
    run_dir = Path(sidecar["manifest_path"]).parent
    result_path = (Path(evidence_path).expanduser().resolve() if evidence_path else
                   run_dir / "camera-dma-result.json")
    output_paths = (result_path, run_dir / "pl-binary8-output.bin",
                    run_dir / "pl-binary8-output.pgm")
    if any(path.exists() or path.is_symlink() for path in output_paths):
        raise preflight.PreflightError("camera DMA result/output path already exists; refusing overwrite")
    live_boot_id, meminfo, cma_ranges, runtime_snapshot = board_once._runtime_preflight(
        plan,
        boot_id_path="/proc/sys/kernel/random/boot_id",
        meminfo_path="/proc/meminfo",
        reserved_memory_path="/proc/device-tree/reserved-memory",
        stage="dma-single",
    )
    if live_boot_id != plan.boot_id or meminfo.get("CmaFree", 0) < preflight.MIN_CMA_KIB:
        raise preflight.PreflightError("camera live boot or static two-buffer CMA gate failed")
    api = board_once.PynqBoardAPI(plan, stage="dma-single", cma_ranges=cma_ranges)
    before = dict(runtime_snapshot)
    guard_claimed = False
    afi1_sync: dict[str, Any] = {}
    afi1_snapshots: list[dict[str, Any]] = []
    try:
        _claim_camera_guard(plan)
        guard_claimed = True
        api.open_mmio()  # Attach only; never create or download an Overlay.
        afi1_snapshots.append(board_once.read_afi1_snapshot(api, "camera_pre_sync"))
        reset_value = api.read_phys32(board_once.SLCR_FPGA_RST_CTRL)
        if reset_value != 0:
            raise preflight.PreflightError("camera stage requires FPGA_RST_CTRL exactly 0")
        for name, address in (("AFI1_RDCHAN_CTRL", board_once.AFI1_RDCHAN_CTRL),
                              ("AFI1_WRCHAN_CTRL", board_once.AFI1_WRCHAN_CTRL)):
            if api.read_phys32(address) & board_once.AFI1_WIDTH_32BIT == 0:
                raise preflight.PreflightError(f"{name} lost its Step 2 32-bit enable bit")
        afi1_sync = board_once.sync_afi1_hp1_32bit(api, plan, stage=CAMERA_STAGE)
        afi1_snapshots.append(board_once.read_afi1_snapshot(api, "camera_post_sync"))
        baseline = validate_step3_checker_state(api, step3_plan)
        frame = plan.frames[0]
        history = CameraHistoryAdapter(
            api, frame=frame, baseline=baseline, prior_frame_id=step3_plan.frames[-1].frame_id)
        run_result = board_once.run_frames(
            plan, history, timeout_s=timeout_s,
            frame_limit=1, expected_prior_frames=1,
            diagnostic_dir=Path(sidecar["manifest_path"]).parent / "camera-dma-diagnostics",
        )
        afi1_snapshots.append(board_once.read_afi1_snapshot(api, "camera_post_dma"))
        post_audit = board_once._post_stage_audit(api, plan, before)
        run_result["runtime_preflight"] = before
        run_result["post_stage_audit"] = post_audit
        result = _write_camera_result(
            plan, sidecar, run_result, result_path, api, step3_plan,
            afi1_sync, afi1_snapshots, before, post_audit,
        )
        if api.current_boot_id() != plan.boot_id:
            raise preflight.PreflightError("boot_id changed after the camera DMA stage")
        _write_json_exclusive(CAMERA_PASS_RECEIPT, {
            "schema": 1,
            "work_order": "WO-0015",
            "stage": CAMERA_STAGE,
            "status": "CAMERA_DMA_SINGLE_PASS",
            "boot_id": plan.boot_id,
            "manifest_sha256": plan.manifest_sha256,
            "step3_manifest_sha256": step3_plan.manifest_sha256,
            "frame_id": plan.frames[0].frame_id,
            "byte_mismatches": 0,
            "pl_output_sha256": result["pl_output"]["binary_sha256"],
        })
        return result
    except board_once.ExecutionFailure as exc:
        if guard_claimed:
            try:
                board_once._record_terminal_failure(CAMERA_STAGE, plan, f"{type(exc).__name__}: {exc}")
            except BaseException:
                pass
            if exc.dma_may_be_active or exc.evidence.get("buffers_retained_for_evidence"):
                payload = {
                    "task": "WO-0015", **exc.evidence,
                    "status": "CAMERA_DMA_SINGLE_FAIL_HOLD",
                    "hardware_stage": CAMERA_STAGE,
                    "board_accessed": bool(getattr(api, "board_accessed", False)),
                    "dma_start_attempted": exc.dma_start_attempted,
                    "transfer_started": exc.transfer_started,
                    "dma_may_be_active": exc.dma_may_be_active,
                    "hold_reason": "DMA may own CMA buffers; no reset, retry, or free is permitted",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                api.fail_hold(str(result_path), payload)
        raise
    except BaseException as exc:
        if guard_claimed:
            try:
                board_once._record_terminal_failure(CAMERA_STAGE, plan, f"{type(exc).__name__}: {exc}")
            except BaseException:
                pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WO-0015 OV9281 one-frame camera-to-PL bridge")
    parser.add_argument("--mode", required=True,
                        choices=("prepare", "mock", "camera-dma-single"))
    parser.add_argument("--template-manifest", help="reviewed three-frame Step 3 manifest")
    parser.add_argument("--template-manifest-sha256", default=STEP3_MANIFEST_SHA256)
    parser.add_argument("--input-file", help="one captured .npy/.pgm/.png grayscale image")
    parser.add_argument("--capture-device", help="one V4L2 capture, normally /dev/video0")
    parser.add_argument("--output-root", help="new directory root for prepared camera captures")
    parser.add_argument("--manifest", help="prepared camera manifest for DMA/mock mode")
    parser.add_argument("--manifest-sha256", help="separately reviewed camera manifest digest")
    parser.add_argument("--step3-manifest", help="the manifest used by the completed dma-three run")
    parser.add_argument("--step3-manifest-sha256", default=STEP3_MANIFEST_SHA256)
    parser.add_argument("--step3-evidence", help="same-boot DMA_THREE_PASS result JSON")
    parser.add_argument("--execute", action="store_true", help="required for camera board DMA")
    parser.add_argument("--authorize-stage", help="must be camera-dma-single for live DMA")
    parser.add_argument("--approve-once", action="store_true", help="consume the camera one-shot guard")
    parser.add_argument("--timeout-s", type=float, default=board_once.DEFAULT_TIMEOUT_S)
    parser.add_argument("--evidence-json", help="new camera DMA result JSON path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "prepare":
            if not args.template_manifest or not args.output_root:
                raise ValueError("prepare mode requires --template-manifest and --output-root")
            if args.template_manifest_sha256 != STEP3_MANIFEST_SHA256:
                raise preflight.PreflightError("camera preparation is bound to the canonical Step 3 manifest")
            prepared = prepare_camera_bundle(
                template_manifest_path=args.template_manifest,
                expected_template_sha256=args.template_manifest_sha256,
                input_path=args.input_file,
                capture_device=args.capture_device,
                output_root=args.output_root,
            )
            print(json.dumps(prepared, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if not args.manifest or not args.manifest_sha256:
            raise ValueError("mock/DMA mode requires --manifest and --manifest-sha256")
        plan = preflight.validate_manifest(args.manifest, args.manifest_sha256)
        if args.mode == "mock":
            api = board_once.MockBoardAPI(plan, initial_idle=True)
            result = board_once.run_frames(plan, api, timeout_s=args.timeout_s, frame_limit=1)
            result.update(status="MOCK_CAMERA_DMA_SINGLE_PASS", board_accessed=False, pynq_imported=False)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if not args.step3_manifest or not args.step3_evidence:
            raise ValueError("camera DMA requires --step3-manifest and --step3-evidence")
        board_once._REAL_BOARD_ACCESS_OCCURRED = False
        board_once._PYNQ_IMPORTED = False
        result = execute_camera_dma(
            manifest_path=args.manifest,
            expected_manifest_sha256=args.manifest_sha256,
            step3_manifest_path=args.step3_manifest,
            step3_manifest_sha256=args.step3_manifest_sha256,
            step3_evidence_path=args.step3_evidence,
            execute=args.execute,
            authorize_stage=args.authorize_stage,
            approve_once=args.approve_once,
            timeout_s=args.timeout_s,
            evidence_path=args.evidence_json,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except board_once.ExecutionFailure as exc:
        payload = {
            "status": "CAMERA_DMA_SINGLE_FAIL_HOLD" if exc.dma_may_be_active else "CAMERA_DMA_SINGLE_FAIL",
            "board_accessed": bool(getattr(exc, "board_accessed", False)),
            "dma_start_attempted": exc.dma_start_attempted,
            "transfer_started": exc.transfer_started,
            "dma_may_be_active": exc.dma_may_be_active,
            "error": f"{type(exc).__name__}: {exc}",
            **exc.evidence,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 3
    except (OSError, ValueError, RuntimeError, preflight.PreflightError) as exc:
        print(json.dumps({"status": f"{args.mode.upper().replace('-', '_')}_REJECTED",
                          "board_accessed": bool(board_once._REAL_BOARD_ACCESS_OCCURRED),
                          "pynq_imported": bool(board_once._PYNQ_IMPORTED),
                          "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
