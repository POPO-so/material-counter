"""Persistent, bounded mock/FPGA monitor. No frame archives or formal permits.

Hardware is imported/accessed only after explicit board opt-in and admission.
No overlay download, DMA reset, automatic retry or conveyor operation occurs.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import signal
import threading
import time

import cv2
import numpy as np

import run_stream_counter as finite
from live_monitor_state import LiveMonitorState
from mjpeg_preview import MjpegFrameHub, MjpegPreviewServer, PeriodicMjpegPublisher
from stream_counter import DoubleBufferStream, MaskCounter, TX_BYTES, RX_BYTES, allocate_ping_pong_buffers


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    # Reuse algorithm defaults, without exposing finite capture/permit switches.
    result.set_defaults(**vars(finite._parser().parse_args([])))
    result.set_defaults(host="127.0.0.1", port=8080, counting_profile="lighting-calibrated")
    result.add_argument("--mode", choices=("mock", "board"), default="mock")
    result.add_argument("--counting-profile", choices=("lighting-calibrated", "full-width-mixed"), default="lighting-calibrated")
    result.add_argument("--lighting-area-filter", choices=("source", "output-roi"), default="source",
                        help="output-roi skips full-resolution source contours and keeps the existing 320x180 >=15px count-area gate")
    result.add_argument("--direction", type=int, choices=(-1, 1), default=1)
    result.add_argument("--hysteresis", type=float, default=2.0)
    result.add_argument("--host", default="127.0.0.1")
    result.add_argument("--port", type=int, default=8080)
    result.add_argument("--timeout-s", type=float, default=5.0)
    result.add_argument("--camera", default="/dev/video0")
    result.add_argument("--execute", action="store_true")
    for name in ("manifest", "manifest-sha256", "expected-boot-id", "stage1-receipt", "stage1-receipt-sha256"):
        result.add_argument("--" + name)
    result.add_argument("--no-auth", action="store_true", help="enable same-origin live reset/view/stop without OTP")
    result.add_argument("--history-limit", type=int, default=120)
    result.add_argument("--mock-fps", type=float, default=30.0)
    result.add_argument("--mm-per-pixel", type=float, default=0.75,
                        help="assumed scale in mm/px for estimated conveyor speed (default: 0.75)")
    result.add_argument("--lock-path", default="/run/material-counter-live.lock")
    return result


def validate_board_options(args):
    if not args.execute:
        raise ValueError("board mode requires --execute")
    for name in ("manifest", "manifest_sha256", "expected_boot_id", "stage1_receipt", "stage1_receipt_sha256"):
        if not getattr(args, name, None):
            raise ValueError(f"board mode requires --{name.replace('_', '-')}")
    if os.environ.get("XILINX_XRT") != "/usr":
        raise ValueError("board mode requires XILINX_XRT=/usr")
    if args.camera != "/dev/video0":
        raise ValueError("board monitor camera is pinned to /dev/video0 (OV9281)")


def synthetic_frame(frame_id, *, direction=1):
    """One moving hexagonal nut with a hole; no frame list or camera fallback."""
    phase = frame_id % 100
    x = -20 + phase * 4
    if direction < 0:
        x = 319 - x
    y = 110
    mask = np.zeros((240, 320), np.uint8)
    points = np.array([(x-8, y), (x-4, y-7), (x+4, y-7),
                       (x+8, y), (x+4, y+7), (x-4, y+7)], np.int32)
    cv2.fillPoly(mask, [points], 255)
    cv2.circle(mask, (x, y), 2, 0, -1)
    mask[:20] = mask[220:] = 0
    gray = np.full_like(mask, 45)
    gray[:20] = gray[220:] = 0
    gray[mask != 0] = 210
    return gray, mask


@contextmanager
def stop_signals(stop):
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: stop.set())
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


class BoardSession:
    """Exclusive lifetime owner of admitted hardware and four CMA buffers."""
    def __init__(self, args, state):
        self.args, self.state = args, state
        self.api = self.dma = self.stream = self.plan = None
        self.tx = self.rx = ()
        self.lock_fd = None
        self.driver_uncertain = False
        self.latest_dma = None

    def open(self):
        args = self.args
        validate_board_options(args)
        self.acquire_runtime_lock()
        pf, board = finite.preflight, finite.board_once
        self.plan = plan = pf.validate_manifest(args.manifest, args.manifest_sha256,
                                                approved_boot_id=args.expected_boot_id)
        self.admit_and_allocate(plan)

    def acquire_runtime_lock(self):
        args = self.args
        import fcntl
        if os.geteuid() != 0:
            raise ValueError("root is required for exclusive process/camera audit")
        lock_path = Path(args.lock_path)
        if not lock_path.is_absolute() or lock_path.parent != Path("/run"):
            raise ValueError("live lock must be a file directly under /run")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise RuntimeError("another live monitor owns the runtime lock")
        self.lock_fd = fd

    def admit_and_allocate(self, plan):
        args = self.args
        pf, board = finite.preflight, finite.board_once
        if not plan.ready_for_board or plan.manifest.get("clock_mhz") != 50:
            raise ValueError("manifest must be board-ready at 50 MHz")
        if pf.read_boot_id() != plan.boot_id:
            raise ValueError("live Boot ID differs from manifest")
        if finite.hashlib.sha256(Path(args.stage1_receipt).read_bytes()).hexdigest() != args.stage1_receipt_sha256:
            raise ValueError("Stage 1 receipt SHA-256 differs")
        board.require_overlay_pass_receipt(args.stage1_receipt, plan)
        # A past terminal hardware fault is never bypassed by selecting live mode.
        board._assert_no_terminal_failure()
        pf.audit_process_ownership()
        pf.read_fpga_manager()
        finite._require_camera_device_unowned(args.camera)
        if args.counting_profile != "lighting-calibrated":
            finite.require_standard_camera_controls(args.camera)
        meminfo = pf.read_cma_meminfo()
        self._watermark(meminfo["CmaFree"])
        admission = pf.read_cma_admission(meminfo=meminfo)
        self.api = api = board.PynqBoardAPI(plan, stage="live-monitor", cma_ranges=admission.ranges)
        audit = api.audit_environment(require_empty_shutdown=False, require_loaded_bit=True)
        if audit["boot_id"] != plan.boot_id:
            raise ValueError("Boot ID changed during admission")
        api.open_mmio()
        overlay = api._pynq.Overlay(str(plan.bit_path), download=False)
        api._overlay = overlay
        if (api.read32(pf.CHECKER_BASE, 0x04) != pf.PACKET_MAGIC
                or api.read32(pf.CHECKER_BASE, board.PACKER_SIGNATURE) != pf.PACKER_SIGNATURE):
            raise ValueError("Checker/Packer signature differs")
        finite.verify_afi1_readback(board.read_afi1_snapshot(api, "live-monitor-admission"))
        for offset in (0x04, 0x34):
            status = api.read32(pf.DMA_BASE, offset)
            if status & board.DMA_ERROR_MASK or not status & (board.DMA_IDLE | board.DMA_HALTED):
                raise ValueError("DMA is active or faulted before admission")

        def allocate(*, shape, dtype):
            self._watermark(api.cma_free_kib())
            return api.allocate(int(np.prod(shape)))

        def verify_watermark(_buffer):
            self._watermark(api.cma_free_kib())

        self.tx, self.rx = allocate_ping_pong_buffers(
            allocate, free=api.free, after_allocate=verify_watermark)
        for tx, rx in zip(self.tx, self.rx):
            pf.validate_dma_buffer_pair(tx_address=api.device_address(tx), tx_size=TX_BYTES,
                                        rx_address=api.device_address(rx), rx_size=RX_BYTES,
                                        cma_ranges=admission.ranges,
                                        hp0_range=plan.hp0_range, hp1_range=plan.hp1_range)
        finite._require_camera_device_unowned(args.camera)
        self.driver_uncertain = True
        self.dma = overlay.axi_dma_0
        self.driver_uncertain = False

    @staticmethod
    def _watermark(value):
        if value < finite.CMA_WATERMARK_KIB:
            raise RuntimeError("CmaFree is below 32768 KiB")

    def arm_frame(self, frame_id, slot):
        api, board, pf = self.api, finite.board_once, finite.preflight
        self._watermark(api.cma_free_kib())
        status = api.read32(pf.CHECKER_BASE, board.PACKER_STATUS)
        if status & (board.PACKER_BUSY | board.PACKER_FAULT) or api.read32(pf.CHECKER_BASE, board.PACKER_ERROR_CODE):
            raise RuntimeError("Packer busy/fault before frame")
        api.sync_to_device(slot.tx_buffer)
        slot.rx_buffer[:] = np.uint8(0xA5)
        api.sync_to_device(slot.rx_buffer)
        api.write32(pf.CHECKER_BASE, board.PACKER_ARM, 1)

    def verify_frame(self, frame_id, slot):
        api, board, pf = self.api, finite.board_once, finite.preflight
        mm2s = api.read32(pf.DMA_BASE, 0x04)
        s2mm = api.read32(pf.DMA_BASE, 0x34)
        self.latest_dma = {"mm2s": f"0x{mm2s:08x}", "s2mm": f"0x{s2mm:08x}"}
        if self.dma.recvchannel.transferred != RX_BYTES:
            raise RuntimeError("S2MM return length differs")
        if any(s != finite.EXPECTED_FRAME_DMASR for s in (mm2s, s2mm)):
            raise RuntimeError("DMA completed with unexpected status")
        status = api.read32(pf.CHECKER_BASE, board.PACKER_STATUS)
        if (status & (board.PACKER_BUSY | board.PACKER_FAULT) or not status & board.PACKER_DONE
                or api.read32(pf.CHECKER_BASE, board.PACKER_ACCEPTED_PIXELS) != RX_BYTES
                or api.read32(pf.CHECKER_BASE, board.PACKER_SENT_BEATS) != RX_BYTES // 4
                or api.read32(pf.CHECKER_BASE, board.PACKER_ERROR_CODE)):
            raise RuntimeError("Packer return frame validation failed")

    async def run(self, counter, publish):
        self.stream = DoubleBufferStream(
            recv_channel=self.dma.recvchannel, send_channel=self.dma.sendchannel,
            tx_buffers=self.tx, rx_buffers=self.rx, counter=counter,
            packet_encoder=(finite.lighting_profile.encode if self.args.counting_profile == "lighting-calibrated"
                            else finite.gray320x240_to_rgb_packet),
            packet_encoder_into=(finite.lighting_profile.encode_into if self.args.counting_profile == "lighting-calibrated"
                                 else finite.gray320x240_to_rgb_packet_into),
            before_transfer=self.arm_frame,
            before_rx_copy=lambda frame_id, slot: self.api.sync_from_device(slot.rx_buffer),
            after_transfer=self.verify_frame, preview_callback=publish,
            history_limit=1, transfer_timeout_s=self.args.timeout_s)
        # Only this admitted live owner may opt the private camera helper into
        # continuous mode. The public finite camera/operator gate is unchanged.
        frames = finite._camera_gray_frames_impl(
            self.args.camera, max_frames=None, live_stop_requested=self.state.stop.is_set,
            read_timeout_s=self.args.timeout_s,
            frame_mapping=finite.preprocessing_configuration(self.args)["mapping"],
            camera_controls_callback=self.state.set_camera_controls)
        await self.stream.run(frames, stop_requested=self.state.stop.is_set)

    def close(self):
        if self.api is not None and (self.tx or self.rx):
            safe = not self.driver_uncertain and finite._dma_buffers_safe_to_release(self.api, self.dma, self.stream)
            if not safe:
                self.state.set_phase("fail_hold", "DMA 所有权不确定，保留 CMA；禁止自动重试")
                # Retain the entire owner even if an injected fail_hold returns.
                finite.board_once.QUARANTINED_BUFFERS.append((self.tx, self.rx, self, "live-monitor"))
                self.api.fail_hold(None, {"status": "LIVE_MONITOR_FAIL_HOLD", "buffers_released": False})
                raise RuntimeError("FAIL_HOLD returned unexpectedly")
            finite._release_buffers(self.api, self.tx + self.rx)
            self.tx = self.rx = ()
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None


async def run_monitor(args, *, state=None, server_ready=None):
    if not math.isfinite(args.mm_per_pixel) or args.mm_per_pixel <= 0:
        raise ValueError("mm-per-pixel must be finite and positive")
    if args.counting_profile not in ("full-width-mixed", "lighting-calibrated"):
        raise ValueError("live monitor requires --counting-profile lighting-calibrated or full-width-mixed (X=160)")
    if args.history_limit < 2 or not math.isfinite(args.mock_fps) or args.mock_fps <= 0:
        raise ValueError("history-limit must be >=2 and mock-fps finite and positive")
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        raise ValueError("timeout-s must be finite and positive")
    if args.physical_calibration or args.wo0017_profile or args.capture_dir or args.no_consume_guard:
        raise ValueError("live monitor cannot use calibration, capture or formal permits")
    state = state or LiveMonitorState(mode=args.mode, history_limit=args.history_limit,
                                     mm_per_pixel=args.mm_per_pixel)
    state.mm_per_pixel = args.mm_per_pixel
    state.counting_profile = args.counting_profile
    state.preprocessing_config = finite.preprocessing_configuration(args)
    config = finite.counting_configuration(args)
    counter = MaskCounter(**config, discard_finished_tracks=True)
    hub = MjpegFrameHub()
    publisher = PeriodicMjpegPublisher(
        hub=hub, line_y=120, count_axis="x", line_x=160,
        interval=finite.PREVIEW_FRAME_INTERVAL if args.mode == "board" else 1,
        active_rows=(counter.roi_top, counter.roi_bottom) if args.counting_profile == "lighting-calibrated" else (20,220), history_limit=args.history_limit,
        count_label="TOTAL COUNT" if args.mode == "board" else "DEMO COUNT",
        pip_label="PL DMA MASK TH100" if args.mode == "board" and args.counting_profile == "lighting-calibrated" else "PL DMA MASK" if args.mode == "board" else "SYNTHETIC MASK",
        view_mode_provider=lambda: state.view_mode)
    state.invalidate_preview = publisher.invalidate
    server = MjpegPreviewServer(hub=hub, host=args.host, port=args.port,
                                state_provider=state.snapshot,
                                live_control_callback=state.control if args.no_auth else None)
    board = BoardSession(args, state) if args.mode == "board" else None
    last_health = -math.inf

    def publish(result, mask, gray):
        nonlocal last_health
        now = time.monotonic()
        if now - last_health >= 1:
            cma = None if board is None else board.api.cma_free_kib()
            state.update_health(cma_free_kib=cma, dma_status=None if board is None else board.latest_dma)
            last_health = now
        displayed = state.complete_frame(result)
        publisher(displayed, mask, gray)

    try:
        server.start()
        print(json.dumps({"mode": args.mode, "url": f"http://{args.host}:{server.port}/",
                          "controls": args.no_auth, "hardware_verified": False}), flush=True)
        if server_ready is not None:
            server_ready(server)
        if board is not None and not state.stop.is_set():
            board.open()
            await board.run(counter, publish)
        elif board is None:
            frame_id = 0
            while not state.stop.is_set():
                started = time.monotonic()
                gray, mask = synthetic_frame(frame_id, direction=args.direction)
                publish(counter.update(frame_id, mask), mask, gray)
                frame_id += 1
                await asyncio.sleep(max(0, 1 / args.mock_fps - (time.monotonic() - started)))
        state.set_phase("stopped", "监控已停止")
    except BaseException as exc:
        state.set_phase("failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        # FAIL_HOLD blocks here with owner references retained; never unwind
        # through CMA destructors while hardware ownership is uncertain.
        if board is not None:
            board.close()
        publisher.close()
        server.close()
    return state.snapshot()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        state = LiveMonitorState(mode=args.mode, history_limit=args.history_limit,
                                 mm_per_pixel=args.mm_per_pixel)
        with stop_signals(state.stop):
            summary = asyncio.run(run_monitor(args, state=state))
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Live monitor failed: {exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
