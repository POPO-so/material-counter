"""Camera-only WO-0016 Stage B-1 calibration preview with Gray8 HUD."""
from __future__ import annotations

import signal
import time
from typing import Any

import cv2
import numpy as np

import mjpeg_preview


BOOT = "58e6af6c-6660-4d44-b0c5-e18f4a564202"
WIDTH = 320
HEIGHT = 240
# Motion is rightward in the live image: the centered horizontal ROI lane is
# crossed by a vertical count-line guide.
LINE_X = 160
ROI_TOP = 96
ROI_BOTTOM_EXCLUSIVE = 145
THRESHOLD = 128
MIN_AREA = 12


def _pipeline_gray8(frame: np.ndarray) -> np.ndarray:
    image = np.asarray(frame)
    if image.dtype != np.uint8 or image.shape[:2] != (400, 640):
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
            (cv2.CAP_PROP_FRAME_WIDTH, 640),
            (cv2.CAP_PROP_FRAME_HEIGHT, 400),
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
        if actual_mode != ("MJPG", 640, 400):
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
    from pathlib import Path

    if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != BOOT:
        raise RuntimeError("boot_id changed before calibration preview")

    camera = _camera()
    hub = mjpeg_preview.MjpegFrameHub()
    mjpeg_preview._HTML_PAGE = (
        b"<!doctype html><html><head><meta charset='utf-8'>"
        b"<title>WO-0016 Stage B-1 Calibration Preview</title></head>"
        b"<body style='margin:12px;background:#111;color:#eee;font:16px sans-serif'>"
        b"<h2>Stage B-1 optical calibration</h2>"
        b"<p>CPU Gray8 HUD only. DMA and counting hardware are paused. "
        b"For rightward motion, vertical count line x=160 crosses the centered horizontal ROI lane "
        b"y=96..144 at right angles. "
        b"Red pixels are ROI pixels with Gray8 &gt;= 128.</p>"
        b"<img src='/stream.mjpg' alt='Live calibration preview' "
        b"style='width:100%;max-width:960px;image-rendering:pixelated'>"
        b"</body></html>"
    )
    server = None
    running = True

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
            ok, frame = camera.read()
            if not ok or frame is None:
                raise RuntimeError("camera returned no calibration-preview frame")

            gray = _pipeline_gray8(frame)
            roi = gray[ROI_TOP:ROI_BOTTOM_EXCLUSIVE, :]
            foreground = roi >= THRESHOLD
            foreground_count = int(np.count_nonzero(foreground))
            components = _component_count(
                np.where(foreground, 255, 0).astype(np.uint8)
            )
            roi_mean = float(np.mean(roi, dtype=np.float64))
            roi_peak = int(np.max(roi))

            # Render the exact Gray8 pixels used by the threshold calculation so
            # red mask pixels line up with their measured locations.
            display = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            display_roi = display[ROI_TOP:ROI_BOTTOM_EXCLUSIVE, :]
            display_roi[foreground] = (0, 0, 255)
            cv2.rectangle(
                display, (0, ROI_TOP),
                (WIDTH - 1, ROI_BOTTOM_EXCLUSIVE - 1), (0, 255, 0), 1
            )
            cv2.line(display, (LINE_X, ROI_TOP),
                     (LINE_X, ROI_BOTTOM_EXCLUSIVE - 1), (0, 255, 255), 1)
            cv2.rectangle(display, (0, 0), (WIDTH - 1, 88), (0, 0, 0), -1)
            hud = (
                f"ROI Mean: {roi_mean:.1f} (Target: 20-45)",
                f"ROI Peak: {roi_peak} (Must < 128)",
                f"ROI FG Pixels (>=128): {foreground_count} (Target: 0)",
                f"Components (>=12px): {components} (Target: 0)",
            )
            for index, line in enumerate(hud):
                cv2.putText(
                    display, line, (5, 17 + index * 19),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 255, 255), 1,
                    cv2.LINE_AA,
                )

            encoded, jpeg = cv2.imencode(
                ".jpg", display,
                [int(cv2.IMWRITE_JPEG_QUALITY), 78],
            )
            if not encoded:
                raise RuntimeError("calibration HUD JPEG encoding failed")
            hub.publish(jpeg.tobytes())
            time.sleep(0.05)
    finally:
        if server is not None:
            server.close()
        camera.release()


if __name__ == "__main__":
    main()
