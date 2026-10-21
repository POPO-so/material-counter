"""Camera-only WO-0016 Stage B-2 preview with live timing and ROI telemetry."""
from __future__ import annotations

import signal
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

_LOCAL_DIR = Path(__file__).resolve().parent
_BOARD_RUNTIME_DIR = Path(
    "/home/xilinx/wo0016/attempt5-boot-d9f65950"
)
_PREVIEW_MODULE_DIR = (
    _LOCAL_DIR if (_LOCAL_DIR / "mjpeg_preview.py").is_file() else _BOARD_RUNTIME_DIR
)
sys.path.insert(0, str(_PREVIEW_MODULE_DIR))
import mjpeg_preview  # noqa: E402


BOOT = "d9f65950-d59b-4ea4-bee4-b28656cb176f"
SOURCE_WIDTH = 640
SOURCE_HEIGHT = 400
WIDTH = 320
HEIGHT = 240
# Motion is rightward in the live image: the centered horizontal ROI lane is
# crossed by a vertical count-line guide.
LINE_X = 160
ROI_TOP = 96
ROI_BOTTOM_EXCLUSIVE = 145
THRESHOLD = 128
MIN_AREA = 12
FPS_EMA_ALPHA = 0.25


def _fps_metrics(
    frame_start_monotonic: float,
    previous_frame_start_monotonic: float | None,
    previous_fps_ema: float | None,
) -> tuple[float | None, float | None]:
    """Return start-to-start interval and smoothed preview-loop FPS."""
    if previous_frame_start_monotonic is None:
        return None, None
    interval_s = frame_start_monotonic - previous_frame_start_monotonic
    if interval_s <= 0:
        raise ValueError("frame start times must strictly increase")
    interval_ms = interval_s * 1000.0
    instant_fps = 1.0 / interval_s
    fps_ema = (
        instant_fps
        if previous_fps_ema is None
        else FPS_EMA_ALPHA * instant_fps + (1.0 - FPS_EMA_ALPHA) * previous_fps_ema
    )
    return interval_ms, fps_ema


def _pipeline_gray8(frame: np.ndarray) -> np.ndarray:
    image = np.asarray(frame)
    if image.dtype != np.uint8 or image.shape[:2] != (SOURCE_HEIGHT, SOURCE_WIDTH):
        raise RuntimeError(
            f"camera frame is {image.dtype} {image.shape}; expected uint8 640x400"
        )
    if image.ndim == 3 and image.shape[2] in (1, 3, 4):
        image = image[:, :, 0]
    if image.ndim != 2 or image.shape != (400, 640):
        raise RuntimeError(f"camera Gray8 source has unexpected geometry: {image.shape}")
    gray = cv2.resize(image, (WIDTH, HEIGHT), interpolation=cv2.INTER_NEAREST)
    if gray.dtype != np.uint8 or gray.shape != (HEIGHT, WIDTH):
        raise RuntimeError(f"resized Gray8 has unexpected format: {gray.dtype} {gray.shape}")
    return gray


def _camera_bgr(frame: np.ndarray) -> np.ndarray:
    """Preserve the negotiated 640x400 image for manual focus adjustment."""
    image = np.asarray(frame)
    if image.dtype != np.uint8 or image.shape[:2] != (SOURCE_HEIGHT, SOURCE_WIDTH):
        raise RuntimeError(
            f"camera frame is {image.dtype} {image.shape}; expected uint8 640x400"
        )
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.ndim != 3:
        raise RuntimeError(f"camera display frame has unexpected geometry: {image.shape}")
    if image.shape[2] == 1:
        return cv2.cvtColor(image[:, :, 0], cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 3:
        return image.copy()
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    raise RuntimeError(f"camera display frame has unexpected channels: {image.shape}")


def _component_count(binary8: np.ndarray) -> int:
    if int(np.count_nonzero(binary8)) < MIN_AREA:
        return 0
    fast_ccl = getattr(cv2, "connectedComponentsWithStatsWithAlgorithm", None)
    fast_algorithm = getattr(cv2, "CCL_GRANA", None)
    if callable(fast_ccl) and fast_algorithm is not None:
        count, _, stats, _ = fast_ccl(binary8, 8, cv2.CV_16U, fast_algorithm)
    else:
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            binary8, connectivity=8, ltype=cv2.CV_16U
        )
    areas = stats[1:count, cv2.CC_STAT_AREA]
    return int(np.count_nonzero(areas >= MIN_AREA))


def _camera() -> Any:
    capture = cv2.VideoCapture("/dev/video0", cv2.CAP_V4L2)
    try:
        if not capture.isOpened():
            raise RuntimeError("cannot open /dev/video0")
        requested = (
            (cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")),
            (cv2.CAP_PROP_FRAME_WIDTH, SOURCE_WIDTH),
            (cv2.CAP_PROP_FRAME_HEIGHT, SOURCE_HEIGHT),
            (cv2.CAP_PROP_FPS, 60),
        )
        if any(not capture.set(prop, value) for prop, value in requested):
            raise RuntimeError("camera rejected MJPG 640x400 at 60 FPS")
        fourcc = int(round(capture.get(cv2.CAP_PROP_FOURCC)))
        actual_fourcc = "".join(chr((fourcc >> (8 * i)) & 255) for i in range(4))
        actual_mode = (
            actual_fourcc,
            int(round(capture.get(cv2.CAP_PROP_FRAME_WIDTH))),
            int(round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))),
        )
        if actual_mode != ("MJPG", SOURCE_WIDTH, SOURCE_HEIGHT):
            raise RuntimeError(f"preview negotiated unexpected camera mode: {actual_mode}")
        for _ in range(2):
            ok, _ = capture.read()
            if not ok:
                raise RuntimeError("camera warm-up returned no frame")
        return capture
    except BaseException:
        capture.release()
        raise


def main() -> None:
    if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != BOOT:
        raise RuntimeError("boot_id changed before camera telemetry preview")

    camera = _camera()
    hub = mjpeg_preview.MjpegFrameHub()
    mjpeg_preview._HTML_PAGE = (
        b"<!doctype html><html><head><meta charset='utf-8'>"
        b"<title>WO-0016 Stage B-2 Camera Telemetry Preview</title></head>"
        b"<body style='margin:12px;background:#111;color:#eee;font:16px sans-serif'>"
        b"<h2>Stage B-2 camera telemetry preview</h2>"
        b"<p>Full-resolution 640x400 live camera image with ROI telemetry. "
        b"Counter, DMA and 165-frame sampling are stopped. "
        b"Stage B-2 vertical count-line guide x=320 crosses the ROI lane y=160..241 "
        b"in this full-resolution view. "
        b"Red pixels show ROI Gray8 &gt;= 128 (THRESHOLD=128).</p>"
        b"<img src='/stream.mjpg' alt='Live camera with ROI telemetry' "
        b"style='width:100%;max-width:960px'>"
        b"</body></html>"
    )
    server = None
    running = True
    frame_count = 0
    previous_frame_start_monotonic = None
    fps_ema = None
    previous_processing_ms = None
    previous_jpeg_kib = None

    def stop(*_args: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server = mjpeg_preview.MjpegPreviewServer(
            hub=hub, host="0.0.0.0", port=8080
        ).start()
        print("STAGEB1_CALIBRATION_HUD_LISTENING 0.0.0.0:8080", flush=True)
        while running:
            frame_start_monotonic = time.monotonic()
            interval_ms, fps_ema = _fps_metrics(
                frame_start_monotonic,
                previous_frame_start_monotonic,
                fps_ema,
            )
            previous_frame_start_monotonic = frame_start_monotonic
            ok, frame = camera.read()
            camera_read_done_monotonic = time.monotonic()
            if not ok or frame is None:
                raise RuntimeError("camera returned no calibration-preview frame")

            frame_count += 1
            camera_read_ms = (camera_read_done_monotonic - frame_start_monotonic) * 1000.0
            gray = _pipeline_gray8(frame)
            roi = gray[ROI_TOP:ROI_BOTTOM_EXCLUSIVE, :]
            foreground = roi >= THRESHOLD
            foreground_count = int(np.count_nonzero(foreground))
            components = _component_count(
                np.where(foreground, 255, 0).astype(np.uint8)
            )
            roi_mean = float(np.mean(roi, dtype=np.float64))
            roi_peak = int(np.max(roi))

            # Keep the full 640x400 image for manual focus; scale the measured
            # binary mask back with nearest-neighbor so red pixels remain exact.
            display = _camera_bgr(frame)
            roi_top_full = int(ROI_TOP * SOURCE_HEIGHT / HEIGHT)
            roi_bottom_full = int(ROI_BOTTOM_EXCLUSIVE * SOURCE_HEIGHT / HEIGHT)
            line_x_full = int(LINE_X * SOURCE_WIDTH / WIDTH)
            foreground_u8 = np.where(foreground, 255, 0).astype(np.uint8)
            foreground_full = cv2.resize(
                foreground_u8,
                (SOURCE_WIDTH, roi_bottom_full - roi_top_full),
                interpolation=cv2.INTER_NEAREST,
            )
            display_roi = display[roi_top_full:roi_bottom_full, :, :]
            display_roi[foreground_full > 0] = (0, 0, 255)
            cv2.rectangle(
                display, (0, roi_top_full),
                (SOURCE_WIDTH - 1, roi_bottom_full - 1), (0, 255, 0), 1
            )
            cv2.line(display, (line_x_full, roi_top_full),
                     (line_x_full, roi_bottom_full - 1), (0, 255, 255), 1)
            cv2.rectangle(display, (0, 0), (SOURCE_WIDTH - 1, 140), (0, 0, 0), -1)
            fps_text = "--" if fps_ema is None else f"{fps_ema:.1f}"
            interval_text = "--" if interval_ms is None else f"{interval_ms:.1f}"
            previous_processing_text = (
                "--" if previous_processing_ms is None
                else f"{previous_processing_ms:.1f}"
            )
            previous_jpeg_text = (
                "--" if previous_jpeg_kib is None else f"{previous_jpeg_kib:.1f}"
            )
            hud = (
                f"Preview FPS EMA: {fps_text} | Frame: {frame_count} | Interval: {interval_text} ms",
                f"Camera read: {camera_read_ms:.1f} ms | Previous process+JPEG: {previous_processing_text} ms",
                f"ROI mean: {roi_mean:.1f} (20-45 target) | peak: {roi_peak} (<128 target)",
                f"ROI foreground pixels: {foreground_count} | components >=12px: {components}",
                f"MJPG 640x400 | threshold: {THRESHOLD} | Previous JPEG: {previous_jpeg_text} KiB",
            )
            for index, line in enumerate(hud):
                cv2.putText(
                    display, line, (8, 22 + index * 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 1,
                    cv2.LINE_AA,
                )

            encoded, jpeg = cv2.imencode(
                ".jpg", display,
                [int(cv2.IMWRITE_JPEG_QUALITY), 78],
            )
            if not encoded:
                raise RuntimeError("calibration HUD JPEG encoding failed")
            previous_processing_ms = (time.monotonic() - camera_read_done_monotonic) * 1000.0
            previous_jpeg_kib = len(jpeg) / 1024.0
            hub.publish(jpeg.tobytes())
            time.sleep(0.05)
    finally:
        if server is not None:
            server.close()
        camera.release()


if __name__ == "__main__":
    main()

