"""Camera-only MJPEG preview service; it does not load an Overlay or start DMA."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any

import cv2

SCRIPT_DIR = Path(__file__).resolve().parent
_BUNDLED_WO15 = SCRIPT_DIR / "wo0015"
WO15_DIR = (_BUNDLED_WO15 if (_BUNDLED_WO15 / "board_once.py").is_file()
            else SCRIPT_DIR.parent / "wo0015")
if str(WO15_DIR) not in sys.path:
    sys.path.insert(0, str(WO15_DIR))

import board_preflight as preflight  # noqa: E402
from mjpeg_preview import MjpegFrameHub, MjpegPreviewServer  # noqa: E402
from run_stream_counter import camera_gray_frames  # noqa: E402

MAX_PREVIEW_FRAMES = 2_147_483_647
JPEG_QUALITY = 70
PREVIEW_MODE = "camera-only; no Overlay or DMA"


def encode_gray_jpeg(gray: Any, *, cv2_module: Any = cv2) -> bytes:
    ok, encoded = cv2_module.imencode(
        ".jpg", gray,
        [getattr(cv2_module, "IMWRITE_JPEG_QUALITY", 1), JPEG_QUALITY],
    )
    if not ok:
        raise RuntimeError("OpenCV could not encode a camera preview frame")
    payload = encoded.tobytes()
    if not payload.startswith(b"\xff\xd8") or not payload.endswith(b"\xff\xd9"):
        raise RuntimeError("OpenCV returned an invalid JPEG preview frame")
    return payload


def write_status(path: Path, status: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError("preview status path must not be a symlink")
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    raw = json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def verify_preview_prerequisites(expected_boot_id: str, device: str) -> dict[str, Any]:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise preflight.PreflightError("camera preview must run as root")
    if expected_boot_id != preflight.AUTHORIZED_BOOT_ID:
        raise preflight.PreflightError("requested boot_id differs from the authorized Phase 1.6 boot")
    boot_id = preflight.read_boot_id()
    if boot_id != expected_boot_id:
        raise preflight.PreflightError("live boot_id differs from the authorized Phase 1.6 boot")
    fpga_manager = preflight.read_fpga_manager()
    ownership = preflight.audit_process_ownership()
    if ownership.get("findings") or ownership.get("denied"):
        raise preflight.PreflightError("camera preview refused because a device owner was found")
    camera_gate = preflight.read_ov9281_gate(device)
    if camera_gate.get("status") != "PASS":
        raise preflight.PreflightError("camera preview requires the reviewed OV9281 readback gate")
    return {
        "boot_id": boot_id,
        "fpga_manager": fpga_manager,
        "process_ownership": ownership,
        "camera_gate": camera_gate,
    }


async def serve_camera_preview(*, expected_boot_id: str, device: str,
                               host: str, port: int, status_json: str | Path,
                               stop_event: asyncio.Event | None = None,
                               cv2_module: Any = cv2) -> dict[str, Any]:
    status_path = Path(status_json).expanduser().resolve()
    if not status_path.is_absolute():
        raise ValueError("status_json must be an absolute path")
    if status_path.exists() or status_path.is_symlink():
        raise FileExistsError("preview status path already exists")
    gates = verify_preview_prerequisites(expected_boot_id, device)
    stop_event = stop_event or asyncio.Event()
    hub = MjpegFrameHub()
    server = MjpegPreviewServer(hub=hub, host=host, port=port)
    frame_source = camera_gray_frames(device, max_frames=MAX_PREVIEW_FRAMES)
    status: dict[str, Any] = {
        "schema": 1,
        "status": "PREVIEW_STARTING",
        "board_accessed": True,
        "pynq_overlay_dma_accessed": False,
        "boot_id": gates["boot_id"],
        "camera_device": device,
        "camera_gate": gates["camera_gate"],
        "fpga_manager": gates["fpga_manager"],
        "process_ownership": gates["process_ownership"],
        "preview_mode": PREVIEW_MODE,
        "capture_mode": "MJPG 640x400 at >=50 fps, resized to grayscale 320x240",
        "host": host,
        "port": port,
        "pid": os.getpid(),
        "frames_published": 0,
    }
    server_started = False
    frame_iterator = frame_source.__aiter__()
    last_status_write = 0.0
    try:
        first_gray = await frame_iterator.__anext__()
        hub.publish(encode_gray_jpeg(first_gray, cv2_module=cv2_module))
        server.start()
        server_started = True
        status["board_accessed"] = True
        status["status"] = "PREVIEW_READY"
        status["frames_published"] = 1
        status["ready_local_time"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        write_status(status_path, status)
        last_status_write = time.monotonic()

        while not stop_event.is_set():
            try:
                gray = await frame_iterator.__anext__()
            except StopAsyncIteration:
                break
            hub.publish(encode_gray_jpeg(gray, cv2_module=cv2_module))
            status["frames_published"] += 1
            now = time.monotonic()
            if now - last_status_write >= 1.0:
                write_status(status_path, status)
                last_status_write = now
        status["status"] = "PREVIEW_STOPPED"
        return status
    except BaseException as exc:
        status["status"] = "PREVIEW_FAILED"
        status["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            await frame_iterator.aclose()
        finally:
            if server_started:
                server.close()
            elif not hub.closed:
                hub.close()
            if status["status"] == "PREVIEW_STOPPED":
                status["stopped_local_time"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            if server_started or status["status"] == "PREVIEW_FAILED":
                write_status(status_path, status)


async def _serve_from_cli(args: argparse.Namespace) -> dict[str, Any]:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop_event.set)
    try:
        return await serve_camera_preview(
            expected_boot_id=args.expected_boot_id,
            device=args.device,
            host=args.host,
            port=args.port,
            status_json=args.status_json,
            stop_event=stop_event,
        )
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-boot-id", required=True)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--status-json", required=True,
                        help="new absolute status path, normally under the run evidence directory")
    args = parser.parse_args(argv)
    if not Path(args.status_json).is_absolute():
        parser.error("--status-json must be an absolute path")
    try:
        result = asyncio.run(_serve_from_cli(args))
    except BaseException as exc:
        print(json.dumps({
            "status": "PREVIEW_FAILED",
            "board_accessed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
