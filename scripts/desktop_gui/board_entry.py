"""Start the desktop camera service with the existing Gray8 deployment checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_artifacts(config):
    root = Path(config["runtime_root"])
    for name in ("manifest", "bit"):
        path = root / config[name]
        if digest(path) != config[name + "_sha256"]:
            raise RuntimeError(name + " SHA-256 与部署配置不一致")


def self_test(config, session_dir, boot):
    root = Path(config["runtime_root"])
    sys.path.insert(0, str(root / "scripts/wo0015"))
    import board_preflight as preflight
    preflight.audit_process_ownership()
    state = preflight.inspect_pynq_global_state(config["pynq_init"], require_empty_shutdown=True)
    load = state["pl_state_status"] == "COLD_BOOT_NO_GLOBAL_STATE"
    if not load:
        preflight.inspect_pynq_global_state(config["pynq_init"], require_empty_shutdown=True,
            require_loaded_bit=True, expected_bit_path=root / config["bit"],
            expected_bit_sha256=config["bit_sha256"])
    output = session_dir / "self-test.json"
    command = [config["python"], str(root / "scripts/gray8/run_gray8_counter.py"),
               "--manifest", str(root / config["manifest"]), "--manifest-sha256",
               config["manifest_sha256"], "--mode", "self-test", "--frames", "3",
               "--execute", "--boot-id", boot, "--output", str(output)]
    if load:
        command.append("--load-overlay")
    env = os.environ.copy()
    env["XILINX_XRT"] = "/usr"
    with (session_dir / "self-test.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, start_new_session=True)
        try:
            code = process.wait(timeout=90)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("FPGA 自检超时，保留进程与现场；请检查 self-test.log") from exc
    if code != 0 or not output.is_file():
        raise RuntimeError("FPGA 自检失败；请检查 self-test.log")
    result = json.loads(output.read_text(encoding="utf-8"))
    if not (result.get("status") == "GRAY8_PIXEL_VERIFIED" and result.get("boot_id") == boot
            and result.get("frames") == 3 and result.get("pixel_verified_frames") == 3
            and result.get("buffers_held") is False):
        raise RuntimeError("FPGA 自检结果不满足启动条件")
    if preflight.read_boot_id() != boot:
        raise RuntimeError("板卡启动 ID 已变化")


def run(config, session_dir):
    import fcntl
    import cv2
    import camera_only_preview as camera
    from desktop_board import DesktopRuntime, DesktopServer
    from frame_protocol import LatestFrameHub
    from live_calibration import CalibrationControls, MonochromePreprocessor
    from stream_counter import MaskCounter

    if os.geteuid() != 0:
        raise RuntimeError("板端服务需要 root 启动以执行硬件准入")
    session_dir = Path(session_dir).resolve(strict=True)
    boot = camera.live_boot_id()
    status_path = session_dir / "result.json"
    summary = {"session_id": config["session_id"], "boot_id": boot, "status": "STARTING",
               "source": "software", "frames": 0, "total_count": 0}
    camera.write_state(status_path, summary)
    # SSH runs as xilinx, while admission begins as root. The result contains
    # no credentials and must remain readable throughout admission.
    os.chmod(status_path, 0o644)
    lock = open("/tmp/material-counter-desktop.lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        summary.update(status="FAILED", error="已有桌面采集任务正在运行")
        camera.write_state(status_path, summary)
        os.chmod(status_path, 0o644)
        lock.close()
        raise RuntimeError("已有桌面采集任务正在运行") from exc
    capture = server = resources = hub = controls = runtime = None
    try:
        check_artifacts(config)
        self_test(config, session_dir, boot)
        camera.require_dma_idle_and_error_free(camera.read_dma_statuses())
        memory = camera.startup_memory_snapshot()
        camera.require_preview_memory(memory, calibration_mode=True, startup=True)
        subprocess.run(["v4l2-ctl", "-d", camera.CAMERA_PATH,
            "--set-fmt-video=width=640,height=400,pixelformat=MJPG", "--set-parm=60",
            "--set-ctrl=auto_exposure=1,exposure_time_absolute=50,brightness=0"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        camera.require_profile(camera.query_v4l2(), "before camera")
        cv2.setNumThreads(1)
        capture = cv2.VideoCapture(camera.CAMERA_INDEX, cv2.CAP_V4L2)
        if not capture.isOpened():
            raise RuntimeError("无法打开相机 /dev/video0")
        settings = [(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")),
                    (cv2.CAP_PROP_FRAME_WIDTH, 640), (cv2.CAP_PROP_FRAME_HEIGHT, 400),
                    (cv2.CAP_PROP_FPS, 60), (cv2.CAP_PROP_BUFFERSIZE, 1),
                    (cv2.CAP_PROP_CONVERT_RGB, 0)]
        if not all(capture.set(key, value) for key, value in settings):
            raise RuntimeError("相机拒绝 MJPG 或单帧缓冲配置")
        first = camera.read_frame(capture)
        camera.require_profile(camera.query_v4l2(), "after first frame")
        counter = MaskCounter(count_axis="x", line_x=160, hysteresis=15.0,
            line_y=120, roi_half_height=24, min_area=12, max_step=60.0,
            max_lateral_step=10.0, max_area=900, max_bbox_width=85,
            max_bbox_height=85, discard_finished_tracks=True)
        hub = LatestFrameHub()
        runtime = DesktopRuntime(config["session_id"], MonochromePreprocessor(), counter, hub)
        resources = camera.PreviewResourceView(camera.create_resource_monitor(),
                                               session_dir / "self-test.json", boot)
        controls = CalibrationControls(enabled=True, apply=camera.apply_v4l2_controls)
        runtime.resources = resources
        runtime.calibration_controls = controls
        runtime.process_frame(first)
        camera.drop_to_xilinx()
        if camera.live_boot_id() != boot:
            raise RuntimeError("相机启动时板卡启动 ID 已变化")
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: runtime.state.stop.set())
        server = DesktopServer(runtime, config["token"], host="0.0.0.0", port=config["board_port"]).start()
        summary["status"] = "RUNNING"
        camera.write_state(status_path, summary)
        print("DESKTOP_READY session=" + config["session_id"], flush=True)
        last_health = time.monotonic()
        while not runtime.state.stop.is_set():
            frame = camera.read_frame(capture)
            runtime.process_frame(frame)
            now = time.monotonic()
            if now - last_health >= 1.0:
                if camera.live_boot_id() != boot:
                    raise RuntimeError("运行中板卡启动 ID 已变化")
                camera.require_preview_memory(camera.preview_memory_snapshot(), calibration_mode=True)
                last_health = now
        summary.update(status="STOPPED", frames=runtime.frame_id,
                       total_count=runtime.state.snapshot()["dashboard"]["total_count"])
    except BaseException as exc:
        summary.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise
    finally:
        if runtime is not None:
            runtime.state.stop.set()
        if hub is not None:
            hub.close()
        if server is not None:
            server.close()
        if resources is not None:
            resources.close()
        if controls is not None and capture is not None:
            try:
                controls.close_and_restore()
                summary["standard_profile_restored"] = True
            except Exception as exc:
                summary.update(status="FAILED", standard_profile_restored=False, restore_error=str(exc))
        if capture is not None:
            capture.release()
        camera.write_state(status_path, summary)
        if os.geteuid() == 0:
            os.chmod(status_path, 0o644)
        lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args()
    path = Path(args.config).resolve(strict=True)
    config = json.loads(path.read_text(encoding="utf-8"))
    run(config, path.parent)


if __name__ == "__main__":
    main()
