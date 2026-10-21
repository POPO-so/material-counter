from __future__ import annotations

import signal
import time
from pathlib import Path

import cv2
import mjpeg_preview

BOOT_ID = "d9f65950-d59b-4ea4-bee4-b28656cb176f"
CAMERA = "/dev/video0"
WIDTH = 640
HEIGHT = 400
PORT = 8080

if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != BOOT_ID:
    raise RuntimeError("boot_id changed before camera-only preview")

mjpeg_preview._HTML_PAGE = (
    b"<!doctype html><html><head><meta charset='utf-8'>"
    b"<meta http-equiv='Cache-Control' content='no-store'>"
    b"<title>WO-0016 Camera Brightness Preview</title></head>"
    b"<body style='margin:16px;background:#171717;color:#eee;font:18px sans-serif'>"
    b"<h2>WO-0016 camera-only brightness preview</h2>"
    b"<p>Live camera image, MJPG 640x400. Counter, DMA and sampling are stopped. "
    b"Camera brightness control: 0 (default); exposure 50, anti-flicker 50 Hz, gamma 100. "
    b"Please adjust the physical light source while viewing this image.</p>"
    b"<img src='/stream.mjpg' alt='Live camera image' "
    b"style='width:100%;height:auto;max-width:1280px;border:1px solid #888'>"
    b"</body></html>"
)

capture = cv2.VideoCapture(CAMERA, cv2.CAP_V4L2)
if not capture.isOpened():
    raise RuntimeError("cannot open /dev/video0")
requested = (
    (cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")),
    (cv2.CAP_PROP_FRAME_WIDTH, WIDTH),
    (cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT),
)
if any(not capture.set(prop, value) for prop, value in requested):
    capture.release()
    raise RuntimeError("camera rejected MJPG 640x400")
fourcc = int(round(capture.get(cv2.CAP_PROP_FOURCC)))
actual_fourcc = "".join(chr((fourcc >> (8 * i)) & 255) for i in range(4))
mode = (
    actual_fourcc,
    int(round(capture.get(cv2.CAP_PROP_FRAME_WIDTH))),
    int(round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))),
)
if mode != ("MJPG", WIDTH, HEIGHT):
    capture.release()
    raise RuntimeError(f"unexpected camera mode: {mode}")
for _ in range(2):
    ok, _frame = capture.read()
    if not ok:
        capture.release()
        raise RuntimeError("camera warm-up returned no frame")

hub = mjpeg_preview.MjpegFrameHub()
server = mjpeg_preview.MjpegPreviewServer(hub=hub, host="0.0.0.0", port=PORT).start()
running = True

def stop(*_args):
    global running
    running = False

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
print(f"CAMERA_ONLY_PREVIEW_LISTENING port={server.port} boot={BOOT_ID} mode={mode}", flush=True)
frames = 0
try:
    while running:
        ok, frame = capture.read()
        if not ok or frame is None:
            raise RuntimeError("camera returned no preview frame")
        encoded, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not encoded:
            raise RuntimeError("preview JPEG encoding failed")
        hub.publish(jpeg.tobytes())
        frames += 1
        if frames == 1 or frames % 150 == 0:
            print(f"CAMERA_ONLY_PREVIEW_FRAMES={frames}", flush=True)
        time.sleep(0.02)
finally:
    server.close()
    capture.release()
    print(f"CAMERA_ONLY_PREVIEW_STOPPED frames={frames}", flush=True)
