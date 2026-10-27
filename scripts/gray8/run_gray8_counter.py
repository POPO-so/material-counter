#!/usr/bin/env python3
"""Explicit Gray8-only overlay entrypoint; reuses the existing PS counter."""
import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
# Load the Gray8 artifact module before adding runtime bundles that contain
# modules with the same basename.
from finalize_build import load_manifest, sha256
sys.path.insert(0, str(REPO / "scripts/wo0015"))
sys.path.insert(0, str(REPO / "scripts/wo0016"))
import board_preflight as preflight
import board_once
import run_stream_counter as runtime
from stream_counter import DoubleBufferStream, MaskCounter, allocate_ping_pong_buffers
from hardware_checks import Gray8Checks, require_dma_quiescent
from packet_codec import (TX_BYTES, gray320x240_to_gray_packet,
                          gray320x240_to_gray_packet_into)


def packet_encoders(profile, threshold):
    if profile != "lighting-calibrated":
        return gray320x240_to_gray_packet, gray320x240_to_gray_packet_into
    lut = runtime.lighting_profile.gray_to_pl_lut(threshold)
    scratch = np.empty((240, 320), dtype=np.uint8)

    def encode_into(gray, output, *, frame_id=0):
        cv2.LUT(gray, lut, dst=scratch)
        gray320x240_to_gray_packet_into(scratch, output, frame_id=frame_id)

    def encode(gray, *, frame_id=0):
        cv2.LUT(gray, lut, dst=scratch)
        return gray320x240_to_gray_packet(scratch, frame_id=frame_id)

    return encode, encode_into


async def synthetic_frames(count):
    yy, xx = np.indices((240, 320))
    for frame_id in range(count):
        # Each frame crosses all threshold boundary values and differs from its predecessor.
        yield ((xx + yy * 17 + frame_id * 73) % 256).astype(np.uint8)


def _dma_idle(dma):
    return all(channel.idle for channel in (dma.sendchannel, dma.recvchannel))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--mode", choices=("offline", "self-test", "camera"), default="offline")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--load-overlay", action="store_true")
    parser.add_argument("--boot-id")
    parser.add_argument("--frames", type=int, default=30)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--profile", choices=("legacy", "full-width-mixed", "lighting-calibrated"), default="lighting-calibrated")
    parser.add_argument("--lighting-threshold", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Choose a new output path; existing evidence is never overwritten")
    if not 1 <= args.frames <= 100000:
        parser.error("frames must be in 1..100000")
    manifest = load_manifest(args.manifest, args.manifest_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = dict(status="OFFLINE_ARTIFACTS_VALID", protocol=manifest["protocol"],
                  manifest_sha256=sha256(args.manifest), mode=args.mode,
                  tx_bytes=TX_BYTES, rx_bytes=76800, count_accuracy="NOT_MEASURED",
                  verify_pixels=True, frames_requested=args.frames)
    if args.mode == "offline":
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        return 0
    if not args.execute or not args.boot_id:
        parser.error("Hardware modes require --execute and the exact --boot-id")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        parser.error("Hardware mode requires root")
    if os.environ.get("XILINX_XRT") != "/usr":
        parser.error("Hardware mode requires XILINX_XRT=/usr for the PYNQ device runtime")
    boot = preflight.read_boot_id()
    if boot != args.boot_id:
        parser.error("Board boot ID differs from the requested boot")
    preflight.audit_process_ownership()
    result["boot_id"] = boot
    result["cma_before"] = preflight.read_cma_meminfo()
    if result["cma_before"]["CmaFree"] < 1024:
        raise RuntimeError("Insufficient free CMA for Gray8 ping-pong buffers")
    result["fpga_manager_before"] = preflight.read_fpga_manager()
    result["kernel_log_before"] = preflight.read_kernel_log()
    # Import only after offline admission and explicit hardware intent.
    import pynq
    from pynq import Overlay, MMIO, allocate
    result["pynq_before"] = preflight.inspect_pynq_global_state(
        pynq.__file__, require_empty_shutdown=True, require_loaded_bit=False)
    bit_path = args.manifest.resolve().parent / manifest["bit_path"]
    overlay = Overlay(str(bit_path), download=args.load_overlay)
    result["pynq_loaded"] = preflight.inspect_pynq_global_state(
        pynq.__file__, require_empty_shutdown=True,
        expected_bit_path=bit_path, expected_bit_sha256=manifest["bit_sha256"], require_loaded_bit=True)
    api = SimpleNamespace(read_phys32=board_once.read_phys32_readonly,
                          write_phys32=board_once.write_afi1_phys32)
    result["clock_reset_after_load"] = board_once.read_afi1_snapshot(api, "gray8-after-load")
    if result["clock_reset_after_load"]["afi1_status"] != "READ":
        raise RuntimeError("FPGA clock/reset path is not ready for Gray8 MMIO")
    checker = MMIO(manifest["checker_base"], 0x10000)
    dma_registers = MMIO(manifest["dma_base"], 0x10000)
    result["dma_before_driver"] = require_dma_quiescent(dma_registers.read)
    checks = Gray8Checks(checker, verify_pixels=True, dma_read=dma_registers.read)
    checks.signatures()
    result["afi1"] = board_once.sync_afi1_hp1_32bit(
        api, SimpleNamespace(hwh_path=args.manifest.resolve().parent / manifest["hwh_path"]),
        stage="gray8-start")
    dma = overlay.axi_dma_0
    buffers, stream, held = (), None, False
    settings = runtime._parser().parse_args([
        "--counting-profile", args.profile, "--lighting-threshold", str(args.lighting_threshold)])
    if args.mode == "self-test":
        settings = runtime._parser().parse_args(["--counting-profile", "full-width-mixed"])
    counter = MaskCounter(**runtime.counting_configuration(settings))
    encoding, encoding_into = packet_encoders(settings.counting_profile, args.lighting_threshold)
    timings, capture_timings, mapping_timings, capture_metadata = {}, [], [], {}
    try:
        tx, rx = allocate_ping_pong_buffers(allocate, tx_bytes=TX_BYTES)
        buffers = tx + rx
        for buffer in buffers:
            address = int(buffer.physical_address)
            if not 0 <= address < address + buffer.nbytes <= 0x20000000:
                raise RuntimeError("DMA allocation is outside the validated HP DDR aperture")
        result["buffers"] = [dict(address=int(b.physical_address), nbytes=b.nbytes) for b in buffers]

        def sync_rx(frame_id, slot):
            slot.rx_buffer.sync_from_device()

        stream = DoubleBufferStream(
            recv_channel=dma.recvchannel, send_channel=dma.sendchannel,
            tx_buffers=tx, rx_buffers=rx, counter=counter,
            packet_encoder=encoding, packet_encoder_into=encoding_into,
            tx_bytes=TX_BYTES, before_transfer=checks.before_transfer,
            before_rx_copy=sync_rx, after_transfer=checks.after_transfer,
            transfer_timeout_s=5, stage_timings_ms=timings)
        source = synthetic_frames(args.frames)
        if args.mode == "camera":
            geometry = runtime.preprocessing_configuration(settings)
            source = runtime._camera_gray_frames_impl(
                args.device, max_frames=args.frames, include_capture_metadata=True,
                frame_mapping=geometry["mapping"], binary_threshold=args.lighting_threshold,
                capture_metadata=capture_metadata, capture_timings_ms=capture_timings,
                mapping_timings_ms=mapping_timings)
            result["geometry"] = geometry
        start = time.perf_counter()
        frames = asyncio.run(stream.run(source))
        elapsed = time.perf_counter() - start
        if not _dma_idle(dma):
            raise RuntimeError("DMA channels are not idle after stream completion")
        if preflight.read_boot_id() != boot:
            raise RuntimeError("Board boot changed during execution")
        result.update(status="GRAY8_PIXEL_VERIFIED", frames=len(frames),
                      pixel_verified_frames=checks.verified_frames,
                      elapsed_s=elapsed, throughput_fps=len(frames) / elapsed,
                      timing_scope="Includes startup/warmup and per-frame pixel verification",
                      total_count=counter.total_count,
                      stage_timings_ms=runtime.summarize_stage_timings_ms(timings),
                      camera_metadata=capture_metadata,
                      capture_timings_ms=capture_timings, mapping_timings_ms=mapping_timings,
                      results=[asdict(f) for f in frames], checker_frames=checks.completions)
    except BaseException as exc:
        try:
            held = bool(stream is not None and (stream.dma_may_be_active or not _dma_idle(dma)))
        except Exception:
            held = stream is not None
        result.update(status="FAIL_HOLD" if held else "FAILED", error=str(exc),
                      dma_may_be_active=held, checker_frames=checks.completions)
    finally:
        result["buffers_held"] = held
        if not held:
            for buffer in buffers:
                buffer.freebuffer()
        try:
            result["kernel_log_after"] = preflight.read_kernel_log()
        except Exception as exc:
            result["kernel_log_after_error"] = str(exc)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(result, output, indent=2)
        print(json.dumps({k: v for k, v in result.items() if k not in
                         ("results", "checker_frames", "capture_timings_ms", "mapping_timings_ms",
                          "kernel_log_before", "kernel_log_after")}), flush=True)
        if held:
            # Do not let Python destruction release buffers still owned by DMA.
            print("GRAY8_FAIL_HOLD: process retains CMA buffers; operator recovery required", flush=True)
            while True:
                time.sleep(1)
    return 0 if result["status"] == "GRAY8_PIXEL_VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
