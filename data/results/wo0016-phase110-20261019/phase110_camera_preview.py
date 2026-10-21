"""Camera-only live preview while Phase 1.10 waits for operator lighting."""
from __future__ import annotations

import signal
import time

import cv2

import mjpeg_preview


BOOT = "58e6af6c-6660-4d44-b0c5-e18f4a564202"


def main() -> None:
    from pathlib import Path

    if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != BOOT:
        raise RuntimeError("boot_id changed before camera preview")
    camera = cv2.VideoCapture("/dev/video0", cv2.CAP_V4L2)
    if not camera.isOpened():
        raise RuntimeError("cannot open /dev/video0")
    hub = mjpeg_preview.MjpegFrameHub()
    mjpeg_preview._HTML_PAGE = (
        b'<!doctype html><html><head><meta charset="utf-8">'
        b'<title>Phase 1.10 Camera Preview</title></head>'
        b'<body style="background:#111;color:#eee;font:16px sans-serif">'
        b'<h1>Live camera preview</h1><p>Camera only. DMA and counting are paused.</p>'
        b'<img src="/stream.mjpg" alt="Live camera" style="width:100%;max-width:960px">'
        b'</body></html>'
    )
    server = None
    running = True

    def stop(*_args):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        requested = (
            (cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")),
            (cv2.CAP_PROP_FRAME_WIDTH, 640),
            (cv2.CAP_PROP_FRAME_HEIGHT, 400),
            (cv2.CAP_PROP_FPS, 60),
        )
        if any(not camera.set(prop, value) for prop, value in requested):
            raise RuntimeError("camera rejected MJPG 640x400 at 60 FPS")
        actual_fourcc = int(round(camera.get(cv2.CAP_PROP_FOURCC)))
        actual_format = "".join(chr((actual_fourcc >> (8 * i)) & 255) for i in range(4))
        if (actual_format, int(camera.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))) != ("MJPG", 640, 400):
            raise RuntimeError("camera preview negotiated an unexpected V4L2 mode")
        server = mjpeg_preview.MjpegPreviewServer(hub=hub, host="0.0.0.0", port=8080).start()
        print("CAMERA_ONLY_PREVIEW_LISTENING 0.0.0.0:8080", flush=True)
        while running:
            ok, frame = camera.read()
            if not ok or frame is None:
                raise RuntimeError("camera returned no preview frame")
            image = cv2.resize(frame, (320, 240), interpolation=cv2.INTER_AREA)
            cv2.putText(image, "CAMERA ONLY - COUNTING PAUSED", (6, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.43, (0, 255, 255), 1)
            encoded, jpeg = cv2.imencode(".jpg", image,
                                        [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if not encoded:
                raise RuntimeError("preview JPEG encoding failed")
            hub.publish(jpeg.tobytes())
            time.sleep(0.1)
    finally:
        if server is not None:
            server.close()
        camera.release()


if __name__ == "__main__":
    main()
