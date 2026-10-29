"""Explicit, bounded independent-HDMI acceptance run; no camera or RLE counting.

Run as a module with the reviewed manifest, current boot ID and --execute.
FAIL_HOLD intentionally keeps this process and its DMA resources alive.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
import multiprocessing as mp
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
from .shared_frames import READING, WRITING, SharedFrames


RLE_BIT_SHA256 = "ca55911e6f286a986fcdeb35f99a2245e74383f739e93dd38a6fa3c52f1868a6"


def _probe_display_buffers(owner, allocate):
    """Check the four Gray8 scan slots before touching display hardware."""
    probes = []
    try:
        for _ in range(4):
            buffer = allocate(shape=(720, 1280), dtype="u1")
            probes.append(buffer)
            owner.retain(buffer)
    finally:
        for buffer in probes:
            buffer.freebuffer()


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


def _wait_for_output_identity(owner, monitor, session_value, frame_id, timeout=0.3):
    """Return when the output monitor reports this submitted frame identity."""
    deadline = time.monotonic() + timeout
    latest = None
    while True:
        latest = owner.call(snapshot, monitor, timeout=0.5)
        if latest["session"] == session_value and latest["frame"] == frame_id:
            return latest, int(latest["sampled_monotonic"] * 1_000_000_000)
        if latest["session"] == session_value and latest["frame"] > frame_id:
            return latest, None
        if time.monotonic() >= deadline:
            return latest, None
        time.sleep(0.001)


def setup_hardware(files, owner):
    from pynq import Clocks, MMIO, Overlay, PL, allocate
    from pynq.lib.video import PIXEL_GRAY, VideoMode

    _probe_display_buffers(owner, allocate)
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


def _parse_proc_stat(text):
    """Parse per-core Linux CPU counters, excluding aggregate and guest fields."""
    cores = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields or not fields[0].startswith("cpu") or not fields[0][3:].isdigit():
            continue
        if len(fields) < 5:
            raise ValueError("per-core /proc/stat row has too few counters")
        try:
            values = tuple(int(value) for value in fields[1:9])
        except ValueError as exc:
            raise ValueError("per-core /proc/stat row contains a non-integer counter") from exc
        if any(value < 0 for value in values):
            raise ValueError("per-core /proc/stat counters cannot be negative")
        cores[fields[0]] = values + (0,) * (8 - len(values))
    if not cores:
        raise ValueError("/proc/stat has no per-core CPU rows")
    return cores


def _cpu_usage_percent(previous, current):
    """Calculate busy percentage from matched per-core tick deltas."""
    usage = {}
    for name, now in current.items():
        before = previous.get(name)
        if before is None:
            continue
        total = sum(now[:8]) - sum(before[:8])
        idle = (now[3] - before[3]) + (now[4] - before[4])
        if total > 0:
            usage[name] = 100.0 * max(0, total - idle) / total
    return usage


def _read_cpu_ticks():
    return _parse_proc_stat(Path("/proc/stat").read_text())


def _put_producer_error(error_queue, error):
    try:
        error_queue.put_nowait({"type": type(error).__name__, "message": str(error)})
    except queue.Full:
        pass


def _send_latest_state(updates, state):
    try:
        updates.put_nowait(state)
        return
    except queue.Full:
        pass
    try:
        updates.get_nowait()
    except queue.Empty:
        pass
    try:
        updates.put_nowait(state)
    except queue.Full:
        pass


def _compositor_process(frames, stop_event, updates, error_queue, counters, *, source,
                        camera_device, font_path, latin_font_path, target_fps, session_uuid):
    """Capture and compose in a child process; publish only through SharedFrames."""
    camera = None
    camera_index = -1
    composer = None
    attempted = published = dropped = 0
    frame_id = 0
    try:
        cv2.setNumThreads(1)
        composer = GrayComposer(font_path, latin_font_path=latin_font_path)
        session = uuid.UUID(session_uuid)
        if source == "camera":
            camera = LatestCamera(camera_device)
            camera_index, gray, _capture_time = camera.read(timeout=5)
            if gray.shape != (400, 640) or gray.dtype != np.uint8:
                raise ValueError("camera must provide 640x400 Gray8 frames")
        else:
            base = np.tile(np.arange(640, dtype=np.uint32) * 256 // 640,
                           (400, 1)).astype(np.uint8)
            gray = base.copy()
        binary = np.empty_like(gray)
        status_lines = ("G1.5 合成进程 / 三槽共享帧", "双画面、框线、中文和遥测缓存",
                        "独立 HDMI；未接 RLE 计数")
        binary_background = True
        frame_period = 1.0 / target_fps
        while not stop_event.is_set():
            begin = time.monotonic()
            while True:
                try:
                    state = updates.get_nowait()
                except queue.Empty:
                    break
                if isinstance(state, dict):
                    next_lines = state.get("status_lines", status_lines)
                    next_background = state.get("binary_background", binary_background)
                    if (isinstance(next_lines, (tuple, list))
                            and all(isinstance(line, str) for line in next_lines)
                            and isinstance(next_background, bool)):
                        status_lines = tuple(next_lines)
                        binary_background = next_background

            attempted += 1
            claimed = frames.claim_write()
            if claimed is None:
                dropped += 1
                frame_id += 1
                counters[0], counters[1], counters[2] = attempted, published, dropped
                remaining = frame_period - (time.monotonic() - begin)
                if remaining > 0:
                    stop_event.wait(remaining)
                continue
            slot, pixels = claimed
            try:
                x = (frame_id * 9) % 560
                if camera is not None:
                    camera_index, gray, _capture_time = camera.read(after=camera_index, timeout=3)
                    boxes, tracks = ((x, 95, x + 60, 165),), (("1", x, 95),)
                else:
                    gray[:] = base
                    cv2.rectangle(gray, (x, 95), (x + 60, 165), 255, -1)
                    boxes, tracks = ((x, 95, x + 60, 165),), (("1", x, 95),)
                cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY, dst=binary)
                render_started = time.perf_counter_ns()
                composer.render(gray, binary, session, frame_id,
                                boxes=boxes, tracks=tracks, status_lines=status_lines,
                                binary_background=binary_background, output=pixels)
                compose_ns = time.perf_counter_ns() - render_started
                timings = composer.last_timings_ms
                frames.publish(slot, session, frame_id,
                               scale_ns=max(0, round(timings["scale_ms"] * 1_000_000)),
                               draw_ns=max(0, round(timings["draw_ms"] * 1_000_000)),
                               compose_ns=compose_ns)
                published += 1
            except BaseException:
                try:
                    if frames.statuses()[slot] == WRITING:
                        frames.abort_write(slot)
                except BaseException:
                    pass
                raise
            frame_id += 1
            counters[0], counters[1], counters[2] = attempted, published, dropped
            counters[3] = composer.panel_redraws
            remaining = frame_period - (time.monotonic() - begin)
            if remaining > 0:
                stop_event.wait(remaining)
    except BaseException as exc:
        _put_producer_error(error_queue, exc)
        raise
    finally:
        cleanup_error = None
        if camera is not None:
            try:
                camera.close()
            except BaseException as exc:
                cleanup_error = exc
                _put_producer_error(error_queue, exc)
        try:
            frames.close()
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
            _put_producer_error(error_queue, exc)
        if composer is not None:
            counters[3] = composer.panel_redraws
        counters[0], counters[1], counters[2] = attempted, published, dropped
        if cleanup_error is not None:
            raise cleanup_error


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
    ctx = mp.get_context("spawn")
    frames = producer = producer_stop = updates = producer_errors = producer_counters = None
    report = {"schema": "material-counter.g1-run.v1", "boot_id": boot,
              "session_id": str(uuid.uuid4()),
              "scope": "G1.5 independent display; three-slot compositor IPC; no RLE counting",
              "source": args.source, "camera": args.camera if args.source == "camera" else None,
              "pipeline": "spawned compositor -> ordinary-RAM SharedFrames -> HDMI CMA",
              "shared_frame_slots": 3, "shared_frame_shape": [720, 1280],
              "manifest_sha256": args.manifest_sha256, "ownership": ownership,
              "before_memory": _memory(), "samples": [], "buttons": deque(maxlen=2048),
              "button_counts": {}, "cpu_samples": [], "sync_missed": 0,
              "state": "STARTING"}
    session = uuid.UUID(report["session_id"])
    stages = {name: deque(maxlen=20000) for name in
              ("scale_ms", "draw_ms", "compose_ms", "ipc_handoff_ms",
               "copy_ms", "submit_ms", "sync_ms")}
    frame = None
    start = None
    try:
        hw = owner.call(setup_hardware, files, owner, timeout=60)
        report["clocks_mhz"] = hw.clocks
        report["hp2_read32"] = hw.hp2_read32
        first = owner.call(snapshot, hw.monitor)
        report["samples"].append(first)
        frames = SharedFrames.create(ctx)
        producer_stop = ctx.Event()
        updates = ctx.Queue(maxsize=1)
        producer_errors = ctx.Queue(maxsize=1)
        producer_counters = ctx.Array("Q", [0] * 4, lock=False)
        producer = ctx.Process(
            target=_compositor_process,
            args=(frames, producer_stop, updates, producer_errors, producer_counters),
            kwargs={"source": args.source, "camera_device": args.camera,
                    "font_path": args.font, "latin_font_path": args.latin_font,
                    "target_fps": args.fps, "session_uuid": report["session_id"]},
            name="material-gray-compositor",
        )
        producer.start()
        report.update(state="RUNNING", producer_pid=producer.pid,
                      compositor_process="separate spawn process",
                      ipc="three-slot non-blocking READY/READING lease")
        start = time.monotonic()
        next_metrics, next_memory, next_cpu = start + 2, start + 3, start + 3
        cpu_previous = _read_cpu_ticks()
        submitted = 0
        last_published = 0
        last_published_at = start
        current = first
        status = ("G1.5 三槽共享帧", "独立 HDMI 显示；未接 RLE 计数")
        latest_memory = report["before_memory"]
        print("G1_5_DISPLAY_RUNNING " + report["session_id"], flush=True)
        while running and time.monotonic() - start < args.duration:
            begin = time.monotonic()
            try:
                producer_error = producer_errors.get_nowait()
            except queue.Empty:
                producer_error = None
            if producer_error is not None:
                raise RuntimeError("compositor failed: {}: {}".format(
                    producer_error.get("type"), producer_error.get("message")))
            if producer.exitcode is not None:
                raise RuntimeError("compositor exited unexpectedly with code {}".format(
                    producer.exitcode))
            published_count = int(producer_counters[1])
            if published_count != last_published:
                last_published = published_count
                last_published_at = begin
            elif begin - last_published_at > 5:
                raise TimeoutError("compositor has not published a frame for 5 seconds")
            pending = []
            while True:
                try:
                    timestamp, command = events.get_nowait()
                    report["buttons"].append({"time": timestamp, "frame": submitted,
                                               "command": command})
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
                if submitted > 5 and current["unique"] > previous["unique"] and current["session"] != session.int:
                    raise RuntimeError("output identity belongs to another session")
                current["effective_fps"] = rate
                current["vdma_status"] = owner.call(hw.vdma.read, 4)
                if current["vdma_status"] & 0x770:
                    raise RuntimeError("VDMA reports a transport error")
                report["samples"].append(current)
                source_label = ("真实摄像头 / 模拟框线（无 RLE 计数）" if args.source == "camera"
                                else "合成测试图（未接计数）")
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
            if begin >= next_cpu:
                cpu_current = _read_cpu_ticks()
                per_core = _cpu_usage_percent(cpu_previous, cpu_current)
                if per_core:
                    report["cpu_samples"].append({"time": begin-start,
                                                  "per_core_percent": per_core})
                cpu_previous = cpu_current
                next_cpu = begin + 3
            detail_source = "真实摄像头与模拟框线" if args.source == "camera" else "合成图"
            detail = (f"G1.5 参数页（{detail_source}，无 RLE 计数）",
                      "输入 640×400 Gray8；显示 576×360",
                      "HDMI：1280×720@60Hz / Gray8",
                      f"FCLK MHz：{hw.clocks}", "显示独占 HP2；4 个 VDMA 槽",
                      "合成子进程；普通 RAM 三槽共享帧",
                      f"CMA 可用：{latest_memory.get('CmaFree', '未知')}",
                      f"帧身份：{current['frame']} / UUID128",
                      "0/128/255 灰阶应无色偏、无拉伸")
            live_status = (detail if controls.page else status) + (
                "计数已暂停（图像/显示继续）" if controls.paused else "计数状态：运行",
                f"BTN1 长按清零次数 {controls.clear_requests}",
                "BTN0 暂停　BTN1 长按清零", "BTN2 底图　BTN3 参数页")
            _send_latest_state(updates, {"status_lines": live_status,
                                         "binary_background": controls.binary_background})
            lease = frames.acquire_latest()
            if lease is None:
                time.sleep(0.001)
                continue
            acquired_ns = time.monotonic_ns()
            if lease.session_uuid != session:
                frames.release_read(lease.slot)
                raise RuntimeError("shared frame belongs to another session")
            stages["scale_ms"].append(lease.scale_ns / 1_000_000)
            stages["draw_ms"].append(lease.draw_ns / 1_000_000)
            stages["compose_ms"].append(lease.compose_ns / 1_000_000)
            stages["ipc_handoff_ms"].append(max(0, acquired_ns - lease.published_ns) / 1_000_000)
            frame_id = lease.frame_id
            try:
                frame = owner.call(hw.hdmi.newframe)
                if frame.shape != (720, 1280) or frame.dtype != np.uint8:
                    raise RuntimeError("HDMI did not provide a two-dimensional Gray8 buffer")
                copy_started = time.perf_counter_ns()
                np.copyto(frame, lease.array)
                stages["copy_ms"].append((time.perf_counter_ns()-copy_started) / 1_000_000)
            finally:
                frames.release_read(lease.slot)
            submit_started = time.perf_counter_ns()
            owner.call(hw.hdmi.writeframe, frame, timeout=1)
            frame = None  # DMA owns it; never read, modify or release it here.
            submit_returned_ns = time.monotonic_ns()
            stages["submit_ms"].append((submit_returned_ns-submit_started) / 1_000_000)
            observed, observed_ns = _wait_for_output_identity(owner, hw.monitor,
                                                               session.int, frame_id)
            if observed_ns is None:
                report["sync_missed"] += 1
            else:
                stages["sync_ms"].append((observed_ns-submit_returned_ns) / 1_000_000)
            submitted += 1
            if observed is not None and observed["session"] == session.int:
                report["last_synced_frame"] = observed["frame"]
        producer_stop.set()
        producer.join(timeout=15)
        if producer.is_alive():
            raise TimeoutError("compositor did not stop and release camera within 15 seconds")
        if producer.exitcode != 0:
            raise RuntimeError("compositor stopped with exit code {}".format(producer.exitcode))
        try:
            producer_error = producer_errors.get_nowait()
        except queue.Empty:
            producer_error = None
        if producer_error is not None:
            raise RuntimeError("compositor shutdown failed: {}".format(producer_error))
        slot_states = frames.statuses()
        if READING in slot_states:
            raise RuntimeError("shared frame reader lease remains active at stop")
        for slot, state in enumerate(slot_states):
            if state == WRITING:
                frames.reclaim_writer(slot, dead_producer=True)
        report["producer"] = {"pid": producer.pid, "exitcode": producer.exitcode,
                               "attempted": int(producer_counters[0]),
                               "published": int(producer_counters[1]),
                               "dropped_no_slot": int(producer_counters[2]),
                               "panel_redraws": int(producer_counters[3]),
                               "camera_released": args.source != "camera" or producer.exitcode == 0}
        report["samples"].append(owner.call(snapshot, hw.monitor))
        owner.call(hw.hdmi.close, timeout=2)
        if not owner.call(hw.vdma.read, 4) & 1:
            raise RuntimeError("VDMA halt not proven after close")
        owner.close()
        frames.close()
        report["camera_released"] = args.source != "camera" or producer.exitcode == 0
        report.update(state="STOPPED", submitted=submitted,
                      duration=time.monotonic()-start, cpu_frame_owned=False,
                      after_memory=_memory(), panel_redraws=int(producer_counters[3]))
        report["timings"] = {
            name: {"p50": float(np.percentile(values, 50)) if values else None,
                   "p95": float(np.percentile(values, 95)) if values else None,
                   "p99": float(np.percentile(values, 99)) if values else None,
                   "count": len(values), "unit": "ms"}
            for name, values in stages.items()}
        cpu_names = sorted({name for sample in report["cpu_samples"]
                            for name in sample["per_core_percent"]})
        report["cpu_percent_summary"] = {
            name: {"p50": float(np.percentile([s["per_core_percent"][name]
                                                for s in report["cpu_samples"]
                                                if name in s["per_core_percent"]], 50)),
                   "p95": float(np.percentile([s["per_core_percent"][name]
                                                for s in report["cpu_samples"]
                                                if name in s["per_core_percent"]], 95)),
                   "p99": float(np.percentile([s["per_core_percent"][name]
                                                for s in report["cpu_samples"]
                                                if name in s["per_core_percent"]], 99))}
            for name in cpu_names}
        cma_values = [int(sample["CmaFree"].split()[0])
                      for sample in report.get("memory_samples", [])
                      if "CmaFree" in sample]
        report["cma_free_kib_min"] = min(cma_values) if cma_values else None
        for process_queue in (updates, producer_errors):
            process_queue.close()
            process_queue.join_thread()
        _write_report(args.output, report)
        print("G1_5_STOPPED_BUFFERS_RELEASED", flush=True)
        return 0
    except BaseException as exc:
        if producer_stop is not None:
            producer_stop.set()
        if producer is not None and producer.pid is not None:
            producer.join(timeout=15)
        if frame is not None:
            owner.retain(frame)
        report.update(state="FAIL_HOLD", error=f"{type(exc).__name__}: {exc}",
                      driver_state=owner.state, failure=owner.failure,
                      driver_job_finished=owner._pending is None or owner._pending.done.is_set(),
                      driver_diagnostics=owner.diagnostics(),
                      producer_pid=producer.pid if producer is not None else None,
                      producer_exitcode=producer.exitcode if producer is not None else None,
                      producer_alive=producer.is_alive() if producer is not None else False)
        if frames is not None and not frames.closed:
            try:
                report["shared_frame_states_fail_hold"] = list(frames.statuses())
            except BaseException as state_error:
                report["shared_frame_state_error"] = str(state_error)
        _write_report(args.output, report)
        print("G1_5_FAIL_HOLD_RESOURCES_RETAINED " + str(exc), flush=True)
        # Never exit and let destructors release a buffer while VDMA might use it.
        while True:
            time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
