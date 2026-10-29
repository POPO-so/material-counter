"""Explicit, bounded independent-HDMI acceptance run; no camera or RLE counting.

Run as a module with the reviewed manifest, current boot ID and --execute.
FAIL_HOLD intentionally keeps this process and its DMA resources alive.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
import os
from pathlib import Path
import queue
import signal
import time
from types import SimpleNamespace
import uuid

import cv2
import numpy as np

from scripts.wo0015.board_preflight import audit_process_ownership
from .bundle import sha256, verify_bundle, verify_runtime
from .compositor import GrayComposer
from .camera import LatestCamera
from .hp2 import sync_hp2_read32
from .controls import ButtonDecoder, ControlState
from .driver_owner import DriverOwner, FailHold


RLE_BIT_SHA256 = "ca55911e6f286a986fcdeb35f99a2245e74383f739e93dd38a6fa3c52f1868a6"


def snapshot(mmio):
    capture_started = time.monotonic()
    previous = mmio.read(0x0C)
    mmio.write(0x04, 1)
    deadline = time.monotonic() + 0.2
    while True:
        status, seq = mmio.read(0x08), mmio.read(0x0C)
        if status & 3 == 2 and seq != previous:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("output monitor snapshot did not complete")
        time.sleep(0.0002)
    values = {name: mmio.read(offset) | (mmio.read(offset + 4) << 32)
              for name, offset in (("unique", 0x10), ("scans", 0x18), ("repeat", 0x20),
                                   ("invalid", 0x28), ("partial", 0x30), ("frame", 0x48))}
    values["session"] = sum(mmio.read(0x38 + i * 4) << (i * 32) for i in range(4))
    flags = mmio.read(0x50)
    for bit, name in enumerate(("gray_error", "marker_pixel_error", "pair_mismatch",
                                "top_invalid", "bottom_invalid")):
        values[name] = bool(flags & (1 << bit))
    for name, offset in (("top_marker", 0x60), ("bottom_marker", 0x80)):
        marker = sum(mmio.read(offset + i * 4) << (i * 32) for i in range(7))
        values[name] = f"{marker:056x}"
    if seq != mmio.read(0x0C):
        raise RuntimeError("snapshot changed during atomic read")
    values.update(sequence=seq, capture_started=capture_started, sampled_monotonic=time.monotonic())
    return values


def setup_hardware(files, owner):
    from pynq import Clocks, MMIO, Overlay, PL, allocate
    from pynq.lib.video import PIXEL_GRAY, VideoMode

    probe = owner.retain(allocate(shape=(24*1024*1024,), dtype="u1"))
    probe.freebuffer()  # CPU-only admission allocation; never submitted to any DMA.
    live = PL.bitfile_name
    live_digest = sha256(live) if live and Path(live).is_file() else None
    if live_digest and live_digest in (files["digest"], files.get("previous_display_sha256")):
        current = owner.retain(MMIO(0x43000000, 0x1000))
        if not current.read(4) & 1:
            raise RuntimeError("previous VDMA is still running; stopped ownership not proven")
        overlay = owner.retain(Overlay(str(files["bit"]), download=live_digest != files["digest"]))
    else:
        if not live or not Path(live).is_file() or sha256(live) != RLE_BIT_SHA256:
            raise RuntimeError("unrecognized loaded overlay; refuse automatic reconfiguration")
        old = owner.retain(MMIO(0x40400000, 0x1000))
        control = owner.retain(MMIO(0x43C20000, 0x1000))
        if control.read(4) != 0x31454C52 or control.read(8) & 5:
            raise RuntimeError("old RLE identity/state is not safe for transition")
        for offset in (0, 0x30):
            status = old.read(offset + 4)
            if status & 0x770 or not status & 2:
                raise RuntimeError("old RLE DMA is active or faulty")
            old.write(offset, old.read(offset) & ~1)
        deadline = time.monotonic() + 0.5
        while any(not old.read(offset) & 1 for offset in (4, 0x34)):
            if time.monotonic() >= deadline:
                raise TimeoutError("old RLE DMA did not halt")
            time.sleep(0.001)
        overlay = owner.retain(Overlay(str(files["bit"])))
    # The boot PLL differs from the Vivado preset; let the installed PYNQ clock
    # driver compute actual divisors before starting any display DMA.
    Clocks.fclk0_mhz = 50
    Clocks.fclk1_mhz = 100
    Clocks.fclk2_mhz = 142.857
    clocks = [Clocks.fclk0_mhz, Clocks.fclk1_mhz, Clocks.fclk2_mhz]
    if any(abs(a-b) > 0.15 for a, b in zip(clocks, (50, 100, 142.857))):
        raise RuntimeError(f"unexpected display clocks: {clocks}")
    hp2_read32 = sync_hp2_read32(files["hwh"], MMIO)
    hdmi = overlay.hdmi_out
    hdmi._vdma = overlay.axi_vdma  # Official VideoOut supports externally supplied VDMA.
    monitor = owner.retain(MMIO(0x43C40000, 0x1000))
    if monitor.read(0) != 0x4D435631:
        raise RuntimeError("output monitor signature mismatch")
    buttons = overlay.btns_gpio.channel1
    buttons.setlength(4)
    buttons.setdirection("in")
    hdmi.configure(VideoMode(1280, 720, 8, 60), PIXEL_GRAY)
    hdmi.start()
    return SimpleNamespace(overlay=overlay, hdmi=hdmi, monitor=monitor, buttons=buttons,
                           clocks=clocks, vdma=overlay.axi_vdma.mmio,
                           hp2_read32=hp2_read32)


def _memory():
    return {key: value for line in Path("/proc/meminfo").read_text().splitlines()
            for key, value in [line.split(":", 1)] if key in ("MemAvailable", "CmaFree", "CmaTotal")}


def _write_report(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2,
                  default=lambda item: list(item) if isinstance(item, deque) else str(item))
        stream.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--previous-display-sha256", help="reviewed stopped G1 bitstream allowed for the transition")
    parser.add_argument("--boot-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--font", default="/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf")
    parser.add_argument("--latin-font", default="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    parser.add_argument("--duration", type=float, default=660)
    parser.add_argument("--fps", type=float, default=20)
    parser.add_argument("--source", choices=("camera", "synthetic"), default="camera")
    parser.add_argument("--camera", default="/dev/v4l/by-id/usb-HSK_USB_Camera_20210616103-video-index0")
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("hardware run requires --execute")
    if not 0 < args.duration <= 7200 or not 10 <= args.fps <= 30:
        parser.error("duration must be 0..7200 seconds and requested FPS 10..30")
    if args.output.exists():
        parser.error("output already exists")
    files = verify_bundle(args.manifest, args.manifest_sha256)
    verify_runtime(json.loads(args.manifest.read_text())["runtime"], args.font, args.latin_font)
    files["digest"] = sha256(files["bit"])
    if args.previous_display_sha256:
        import re
        if not re.fullmatch(r"[0-9a-f]{64}", args.previous_display_sha256):
            parser.error("invalid previous display digest")
        files["previous_display_sha256"] = args.previous_display_sha256
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if boot != args.boot_id or os.geteuid() != 0:
        raise RuntimeError("current boot ID and root privileges are required")
    import fcntl
    locks = []
    for name in ("/tmp/material-counter-desktop.lock", "/run/material-standalone.lock"):
        stream = open(name, "a")
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.append(stream)
    ownership = audit_process_ownership()
    cv2.setNumThreads(1)
    composer = GrayComposer(args.font, latin_font_path=args.latin_font)
    decoder, controls = ButtonDecoder(), ControlState()
    events = queue.Queue(maxsize=64)
    hw = None
    running = True

    def stop(*_):
        nonlocal running
        running = False

    def poll_buttons():
        if hw is not None:
            for command in decoder.update(int(hw.buttons.read()) & 15, time.monotonic()):
                events.put_nowait((time.monotonic(), command))

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    owner = DriverOwner(poll=poll_buttons)
    report = {"schema": "material-counter.g1-run.v1", "boot_id": boot,
              "session_id": str(uuid.uuid4()), "scope": "G1 independent display; no RLE counting",
              "source": args.source, "camera": args.camera if args.source == "camera" else None,
              "manifest_sha256": args.manifest_sha256, "ownership": ownership,
              "before_memory": _memory(), "samples": [], "buttons": deque(maxlen=2048),
              "button_counts": {}, "state": "STARTING"}
    session = uuid.UUID(report["session_id"])
    base = np.tile(np.arange(640, dtype=np.uint32) * 256 // 640, (400, 1)).astype("u1")
    gray, binary = base.copy(), np.empty_like(base)
    stages = {k: deque(maxlen=20000) for k in ("compose_ms", "copy_ms", "submit_ms")}
    frame = None
    camera = None
    camera_index = -1
    try:
        if args.source == "camera":
            camera = LatestCamera(args.camera)
            camera_index, gray, _ = camera.read(timeout=5)
        hw = owner.call(setup_hardware, files, owner, timeout=60)
        report["clocks_mhz"] = hw.clocks
        report["hp2_read32"] = hw.hp2_read32
        first = owner.call(snapshot, hw.monitor)
        report["samples"].append(first)
        start = time.monotonic()
        next_metrics, next_memory = start, start
        index = 0
        status = ()
        latest_memory = {}
        print("G1_DISPLAY_RUNNING " + report["session_id"], flush=True)
        while running and time.monotonic() - start < args.duration:
            begin = time.monotonic()
            pending = []
            while True:
                try:
                    timestamp, command = events.get_nowait()
                    report["buttons"].append({"time": timestamp, "frame": index, "command": command})
                    report["button_counts"][command] = report["button_counts"].get(command, 0) + 1
                    pending.append(command)
                except queue.Empty:
                    break
            controls.apply(pending)  # Before this boundary's new counting events (none in G1).
            if begin >= next_metrics:
                current = owner.call(snapshot, hw.monitor)
                previous = report["samples"][-1]
                elapsed = current["sampled_monotonic"] - previous["sampled_monotonic"]
                rate = ((current["unique"] - previous["unique"]) & ((1 << 64)-1)) / elapsed
                if rate > 65:
                    raise RuntimeError("output counter jumped or reset during the session")
                if index > 5 and current["unique"] > previous["unique"] and current["session"] != session.int:
                    raise RuntimeError("output identity belongs to another session")
                current["effective_fps"] = rate
                current["vdma_status"] = owner.call(hw.vdma.read, 4)
                if current["vdma_status"] & 0x770:
                    raise RuntimeError("VDMA reports a transport error")
                report["samples"].append(current)
                source_label = "真实摄像头 / CPU 阈值显示验证" if camera else "合成测试图（未接计数）"
                status = (source_label, "本次运行累计：0；重启清零",
                          f"有效显示 {rate:.2f} FPS / 720p60",
                          f"输出新帧 {current['unique']}；重复 {current['repeat']}",
                          f"无效扫描 {current['invalid']}；页 {controls.page}")
                next_metrics = begin + 2
                print(json.dumps({"elapsed": begin-start, **current}), flush=True)
            if begin >= next_memory:
                latest_memory = {"time": begin, **_memory()}
                report.setdefault("memory_samples", []).append(latest_memory)
                next_memory = begin + 3
            if camera:
                camera_index, gray, capture_time = camera.read(after=camera_index)
            else:
                gray[:] = base
                x = (index * 9) % 560
                cv2.rectangle(gray, (x, 95), (x + 60, 165), 255, -1)
            cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY, dst=binary)
            detail = ("G1 参数页（合成图，无 RLE 计数）", "输入 640×400 Gray8；显示 576×360",
                      "HDMI：1280×720@60Hz / 8-bit DDR",
                      f"FCLK MHz：{hw.clocks}", "显示独占 HP2；4 个 VDMA 槽",
                      f"CMA 可用：{latest_memory.get('CmaFree', '未知')}",
                      f"帧身份：{current['frame']} / UUID128",
                      "0/128/255 灰阶应无色偏、无拉伸")
            live_status = (detail if controls.page else status) + (
                                    "计数已暂停（图像/显示继续）" if controls.paused else "计数状态：运行",
                                    f"BTN1 长按清零次数 {controls.clear_requests}",
                                    "BTN0 暂停　BTN1 长按清零", "BTN2 底图　BTN3 参数页")
            t = time.monotonic()
            canvas = composer.render(gray, binary, session, index,
                                     boxes=() if camera else ((x, 95, x + 60, 165),),
                                     tracks=() if camera else (("1", x, 95),),
                                     status_lines=live_status, binary_background=controls.binary_background)
            stages["compose_ms"].append((time.monotonic()-t)*1000)
            frame = owner.call(hw.hdmi.newframe)
            if frame.shape != (720, 1280) or frame.dtype != np.uint8:
                raise RuntimeError("HDMI did not provide a two-dimensional Gray8 buffer")
            t = time.monotonic()
            np.copyto(frame, canvas)
            stages["copy_ms"].append((time.monotonic()-t)*1000)
            t = time.monotonic()
            owner.call(hw.hdmi.writeframe, frame, timeout=1)
            frame = None  # DMA owns it; never read, modify or release it here.
            stages["submit_ms"].append((time.monotonic()-t)*1000)
            index += 1
            time.sleep(max(0, 1/args.fps - (time.monotonic()-begin)))
        # Allow the final submitted frame to finish; submitting is not display completion.
        time.sleep(0.1)
        report["samples"].append(owner.call(snapshot, hw.monitor))
        owner.call(hw.hdmi.close, timeout=2)
        if not owner.call(hw.vdma.read, 4) & 1:
            raise RuntimeError("VDMA halt not proven after close")
        owner.close()
        if camera:
            camera.close()
            report["camera_released"] = True
        report.update(state="STOPPED", submitted=index, duration=time.monotonic()-start,
                      cpu_frame_owned=False, after_memory=_memory(), panel_redraws=composer.panel_redraws)
        report["timings"] = {name: {"p50": float(np.percentile(values, 50)),
                                            "p95": float(np.percentile(values, 95)),
                                            "p99": float(np.percentile(values, 99))}
                             for name, values in stages.items() if values}
        _write_report(args.output, report)
        print("G1_STOPPED_BUFFERS_RELEASED", flush=True)
        return 0
    except BaseException as exc:
        if frame is not None:
            owner.retain(frame)
        report.update(state="FAIL_HOLD", error=f"{type(exc).__name__}: {exc}",
                      driver_state=owner.state, failure=owner.failure,
                      driver_job_finished=owner._pending is None or owner._pending.done.is_set())
        _write_report(args.output, report)
        print("G1_FAIL_HOLD_RESOURCES_RETAINED " + str(exc), flush=True)
        # Never exit and let destructors release a buffer while VDMA might use it.
        while True:
            time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
