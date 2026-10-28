"""Board-side desktop runtime for the verified 640x400 RLE pipeline."""
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import traceback

import cv2
import numpy as np

import camera_only_preview as camera
from desktop_board import DesktopRuntime, DesktopServer
from frame_protocol import LatestFrameHub
from live_calibration import CalibrationControls
from live_monitor_state import LiveMonitorState


def read_camera_frame(capture):
    """Validate/decode with the shared intake and retain its same-frame JPEG."""
    class RememberPacket:
        packet = None

        def read(self):
            ok, self.packet = capture.read()
            return ok, self.packet

    source = RememberPacket()
    gray = camera.read_frame(source)
    return gray, np.ascontiguousarray(source.packet).tobytes()


class RleResourceView:
    """Expose board telemetry plus the verified static RLE build resources."""

    def __init__(self, monitor, fpga_resources, transport):
        self.monitor = monitor
        self.fpga = dict(fpga_resources or {})
        self.transport = transport

    def control(self, *, enabled, mode):
        return self.monitor.control(enabled=enabled, mode=mode)

    def snapshot(self):
        result = self.monitor.snapshot()
        if result["enabled"] and result["mode"] == "detail":
            snapshot = self.transport.snapshot() if self.transport is not None else None
            dma_status = "尚未完成帧"
            if snapshot:
                dma_status = (
                    f"MM2S=0x{snapshot['mm2s_status']:08x}，"
                    f"S2MM=0x{snapshot['s2mm_status']:08x}，"
                    f"实际 {snapshot['actual_dma_bytes']} B"
                )
            result["detail"] = {
                "fpga": self.fpga,
                "camera": camera.CAMERA_PATH,
                "dma_status": dma_status,
            }
        return result

    def close(self):
        self.monitor.close()


class RleDesktopRuntime(DesktopRuntime):
    """Count from PL run lengths while publishing only completed camera frames."""

    def __init__(self, session_id, counter, transport, hub, *, threshold=128,
                 intensity_offset=0, morph_mode=0):
        super().__init__(session_id, None, counter, hub)
        # Retain the old preview's estimated physical scale along the x axis:
        # 640 pixels now span the field previously represented by 320 pixels.
        self.state = LiveMonitorState(mode="board", mm_per_pixel=0.375, clock=self.clock)
        self.transport = transport
        self.threshold = int(threshold)
        self.intensity_offset = int(intensity_offset)
        self.morph_mode = int(morph_mode)
        self.invalid_frames = 0
        self.last_rle = None
        self.last_health = None
        self.stage_times = deque(maxlen=120)

    def process_frame(self, gray, *, preview_jpeg=None, capture_ms=0.0):
        captured_at_ns = time.time_ns()
        captured_monotonic_ns = time.monotonic_ns()
        self._apply_pending_calibration()
        started = time.perf_counter()
        frame = self.transport.transfer(
            gray,
            self.frame_id,
            params_version=1,
            roi=(0, 0, self.counter.width, self.counter.height),
            threshold=self.threshold,
            intensity_offset=self.intensity_offset,
            morph_mode=self.morph_mode,
        )
        transferred = time.perf_counter()
        self.last_rle = frame
        if not frame.valid:
            self.invalid_frames += 1
            self.state.set_phase(
                "running",
                f"RLE 帧 {self.frame_id} 被拒绝：status=0x{frame.status:08x}",
            )
            self.frame_id += 1
            return None

        result = self.counter.update_rle(
            frame, capture_monotonic_ns=captured_monotonic_ns
        )
        counted = time.perf_counter()
        displayed = self.state.complete_frame(result)
        now = self.clock()
        if self.last_health is None or now - self.last_health >= 1.0:
            self.state.update_health(dma_status=self.transport.snapshot())
            self.last_health = now
        if self.hub.client_count and (
                self.last_publish is None or now - self.last_publish >= 0.05):
            from scripts.rle.protocol import reconstruct

            mask = reconstruct(frame)
            # publish() serializes synchronously into immutable bytes. The
            # completed result can be read directly; recursively copying its
            # 1040 projection values costs ~24 ms on this board's Python 3.10.
            wire_result = dict(vars(displayed))
            for key in ("detections", "events", "raw_detections", "suppressed_events"):
                wire_result[key] = [vars(item) for item in getattr(displayed, key)]
            self.hub.publish({
                "session_id": self.session_id,
                "frame_id": result.frame_id,
                "source": "pl_rle",
                "gray": gray,
                "mask": mask,
                "preview_jpeg": preview_jpeg,
                "result": wire_result,
                "config": {
                    "width": self.counter.width,
                    "height": self.counter.height,
                    "line_x": self.counter.line_x,
                    "line_y": self.counter.line_y,
                    "count_axis": self.counter.count_axis,
                    "roi_top": result.roi_top,
                    "roi_bottom": result.roi_bottom,
                    "threshold": self.threshold,
                    "intensity_offset": self.intensity_offset,
                    "morph_mode": self.morph_mode,
                    "runs": frame.run_count,
                    "total_runs": frame.total_run_count,
                    "rx_bytes": frame.actual_bytes,
                    "hw_timestamp": frame.hw_timestamp,
                },
                "capture_time_ns": captured_at_ns,
            })
            self.last_publish = now
        finished = time.perf_counter()
        self.stage_times.append({
            "capture_ms": capture_ms,
            "dma_ms": (transferred - started) * 1000,
            "regions_tracking_ms": (counted - transferred) * 1000,
            "monitor_preview_ms": (finished - counted) * 1000,
        })
        self.frame_id += 1
        return displayed

    def status(self):
        raw = self.state.snapshot()
        telemetry = raw["dashboard"]
        telemetry["camera"] = "frames_received" if not telemetry["stale"] else "unknown"
        telemetry["source"] = "pl_rle"
        telemetry["mode"] = "camera"
        telemetry["invalid_frames"] = self.invalid_frames
        if self.stage_times:
            samples = tuple(self.stage_times)
            telemetry["rle_timings_ms"] = {
                key: sum(sample[key] for sample in samples) / len(samples)
                for key in samples[0]
            }
        if self.last_rle is not None:
            telemetry["rle"] = {
                "runs": self.last_rle.run_count,
                "total_runs": self.last_rle.total_run_count,
                "total_foreground": self.last_rle.total_fg,
                "rx_bytes": self.last_rle.actual_bytes,
                "valid": self.last_rle.valid,
                "status": self.last_rle.status,
            }
        running = telemetry["phase"] == "running"
        return {
            "schema": 1,
            "session_id": self.session_id,
            "telemetry": telemetry,
            "runtime_status": raw["status"],
            "preview": {
                "width": self.counter.width,
                "height": self.counter.height,
                "pipeline": "RLE_PACKET_V1",
            },
            "resource_monitor": self.resources.snapshot() if self.resources else None,
            "camera_controls": (
                dict(self.calibration_controls.values)
                if self.calibration_controls else None
            ),
            "debug": {"enabled": self.debug_enabled},
            "capabilities": {
                "start": False,
                "stop": running,
                "reset": running,
                "view_mode": True,
                "calibration": self.calibration_controls is not None,
            },
        }


def _write_state(path, state):
    camera.write_state(path, state)
    os.chmod(path, 0o644)


def run_session(config, session_dir):
    """Run the board service; the caller has selected RLE_PACKET_V1."""
    import fcntl
    import pynq
    from pynq import MMIO, Overlay, allocate

    import board_entry
    from scripts.rle.artifacts import load_manifest, sha256
    from scripts.rle.components import build_native
    from scripts.rle.counter import RleCounter
    from scripts.rle.protocol import RX_CAPACITY, TX_BYTES
    from scripts.rle.transport import RleDmaTransport
    from scripts.wo0015 import board_once, board_preflight as preflight

    if os.geteuid() != 0:
        raise RuntimeError("板端服务需要 root 启动以执行硬件准入")
    session_dir = Path(session_dir).resolve(strict=True)
    root = Path(config["runtime_root"])
    boot = camera.live_boot_id()
    status_path = session_dir / "result.json"
    summary = {
        "session_id": config["session_id"],
        "boot_id": boot,
        "status": "STARTING",
        "source": "pl_rle",
        "protocol": "RLE_PACKET_V1",
        "frames": 0,
        "total_count": 0,
    }
    _write_state(status_path, summary)

    lock = open("/tmp/material-counter-desktop.lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        summary.update(status="FAILED", error="已有桌面采集任务正在运行")
        _write_state(status_path, summary)
        lock.close()
        raise RuntimeError("已有桌面采集任务正在运行") from exc

    capture = server = resources = hub = controls = runtime = transport = None
    allocated = []
    self_test_ownership_uncertain = False
    try:
        board_entry.check_artifacts(config)
        board_entry.self_test(config, session_dir, boot)
        camera.require_dma_idle_and_error_free(camera.read_dma_statuses())
        memory = camera.startup_memory_snapshot()
        camera.require_preview_memory(memory, calibration_mode=True, startup=True)

        manifest = load_manifest(
            root / config["manifest"], config["manifest_sha256"]
        )
        bit_path = root / config["bit"]
        if sha256(bit_path) != config["bit_sha256"]:
            raise RuntimeError("RLE bitstream SHA-256 与部署配置不一致")
        preflight.inspect_pynq_global_state(
            pynq.__file__,
            require_empty_shutdown=True,
            require_loaded_bit=True,
            expected_bit_path=bit_path,
            expected_bit_sha256=config["bit_sha256"],
        )
        # The self-test leaves a completed frame marker in the PL. Reloading
        # the same reviewed artifact gives the live service a fresh generation
        # so its fail-closed first-frame ownership check begins from idle.
        overlay = Overlay(str(bit_path), download=True)
        api = type(
            "BoardApi",
            (),
            {
                "read_phys32": staticmethod(board_once.read_phys32_readonly),
                "write_phys32": staticmethod(board_once.write_afi1_phys32),
            },
        )
        board_once.sync_afi1_hp1_32bit(
            api(),
            type("Hwh", (), {"hwh_path": bit_path.with_suffix(".hwh")})(),
            stage="desktop-rle-start",
        )

        tx = allocate(shape=(TX_BYTES,), dtype=np.uint8)
        allocated.append(tx)
        rx = allocate(shape=(RX_CAPACITY,), dtype=np.uint8)
        allocated.append(rx)
        transport = RleDmaTransport(
            overlay.axi_dma_0.sendchannel,
            overlay.axi_dma_0.recvchannel,
            MMIO(manifest["dma_base"], 0x10000),
            MMIO(manifest["control_base"], 0x10000),
            tx,
            rx,
        )

        native = session_dir / "rle_components_v2.so"
        build_native(native)
        counter = RleCounter(
            library=native,
            count_axis="x",
            line_y=200,
            line_x=320,
            roi_mode="full",
            min_area=48,
            max_area=3600,
            max_bbox_width=170,
            max_bbox_height=170,
            max_step=48,
            max_x_step=40,
            hysteresis=4,
            component_cluster_dx=0,
        )

        subprocess.run(
            [
                "v4l2-ctl", "-d", camera.CAMERA_PATH,
                "--set-fmt-video=width=640,height=400,pixelformat=MJPG",
                "--set-parm=60",
                "--set-ctrl=auto_exposure=1,exposure_time_absolute=50,brightness=0",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
        )
        camera.require_profile(camera.query_v4l2(), "before camera")
        cv2.setNumThreads(1)
        capture = cv2.VideoCapture(camera.CAMERA_INDEX, cv2.CAP_V4L2)
        if not capture.isOpened():
            raise RuntimeError("无法打开相机 /dev/video0")
        settings = [
            (cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")),
            (cv2.CAP_PROP_FRAME_WIDTH, 640),
            (cv2.CAP_PROP_FRAME_HEIGHT, 400),
            (cv2.CAP_PROP_FPS, 60),
            (cv2.CAP_PROP_BUFFERSIZE, 1),
            (cv2.CAP_PROP_CONVERT_RGB, 0),
        ]
        if not all(capture.set(key, value) for key, value in settings):
            raise RuntimeError("相机拒绝 MJPG 或单帧缓冲配置")
        first_gray, first_jpeg = read_camera_frame(capture)
        camera.require_profile(camera.query_v4l2(), "after first frame")

        hub = LatestFrameHub()
        runtime = RleDesktopRuntime(
            config["session_id"], counter, transport, hub,
            threshold=128, intensity_offset=0, morph_mode=0,
        )
        resources = RleResourceView(
            camera.create_resource_monitor(),
            config.get("fpga_resources", {}),
            transport,
        )
        controls = CalibrationControls(
            enabled=True, apply=camera.apply_v4l2_controls
        )
        runtime.resources = resources
        runtime.calibration_controls = controls
        runtime.process_frame(first_gray, preview_jpeg=first_jpeg)
        camera.drop_to_xilinx()
        if camera.live_boot_id() != boot:
            raise RuntimeError("相机启动时板卡启动 ID 已变化")

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: runtime.state.stop.set())
        server = DesktopServer(
            runtime, config["token"], host="0.0.0.0", port=config["board_port"]
        ).start()
        summary["status"] = "RUNNING"
        _write_state(status_path, summary)
        print("DESKTOP_RLE_READY session=" + config["session_id"], flush=True)

        last_health = time.monotonic()
        while not runtime.state.stop.is_set():
            capture_started = time.perf_counter()
            gray, jpeg = read_camera_frame(capture)
            capture_ms = (time.perf_counter() - capture_started) * 1000
            runtime.process_frame(gray, preview_jpeg=jpeg, capture_ms=capture_ms)
            now = time.monotonic()
            if now - last_health >= 1.0:
                if camera.live_boot_id() != boot:
                    raise RuntimeError("运行中板卡启动 ID 已变化")
                camera.require_preview_memory(
                    camera.preview_memory_snapshot(), calibration_mode=True
                )
                last_health = now
        summary.update(
            status="STOPPED",
            frames=runtime.frame_id,
            invalid_frames=runtime.invalid_frames,
            total_count=runtime.state.snapshot()["dashboard"]["total_count"],
        )
    except BaseException as exc:
        self_test_ownership_uncertain = isinstance(
            exc, board_entry.SelfTestOwnershipUncertain)
        held = bool(transport and transport.dma_may_be_active) or self_test_ownership_uncertain
        summary.update(
            status="FAIL_HOLD" if held else "FAILED",
            error=f"{type(exc).__name__}: {exc}",
        )
        traceback.print_exc()
        raise
    finally:
        held = bool(transport and transport.dma_may_be_active) or self_test_ownership_uncertain
        summary["buffers_held"] = held
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
                summary.update(
                    status="FAILED",
                    standard_profile_restored=False,
                    restore_error=str(exc),
                )
        if capture is not None:
            capture.release()
        if transport is not None and not held:
            transport.close()
        elif transport is None and not held:
            for buffer in allocated:
                buffer.freebuffer()
        if held:
            summary["status"] = "FAIL_HOLD"
        _write_state(status_path, summary)
        if held:
            print(
                "RLE_FAIL_HOLD: retaining DMA-owned CMA buffers",
                flush=True,
            )
            while True:
                time.sleep(1)
        lock.close()
