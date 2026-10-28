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
from dma_polling import CooperativeDmaChannel


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
    parser.add_argument("--validation", choices=("full", "runtime"), default="full",
                        help="full checks each pixel; runtime retains hardware and checksum checks")
    parser.add_argument("--dma-wait", choices=("driver", "cooperative"), default="driver",
                        help="driver uses PYNQ wait threads; cooperative yields between status polls")
    parser.add_argument("--check-execution", choices=("inline", "worker"), default="worker",
                        help="worker lets the event loop progress while completion checks run")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--load-overlay", action="store_true")
    parser.add_argument("--boot-id")
    parser.add_argument("--frames", type=int, default=30)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--profile", choices=("legacy", "full-width-mixed", "lighting-calibrated"), default="lighting-calibrated")
    parser.add_argument("--lighting-threshold", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == "self-test" and args.validation != "full":
        parser.error("self-test requires --validation full")
    if args.output.exists():
        parser.error("Choose a new output path; existing evidence is never overwritten")
    if not 1 <= args.frames <= 100000:
        parser.error("frames must be in 1..100000")
    manifest = load_manifest(args.manifest, args.manifest_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = dict(status="OFFLINE_ARTIFACTS_VALID", protocol=manifest["protocol"],
                  manifest_sha256=sha256(args.manifest), mode=args.mode,
                  tx_bytes=TX_BYTES, rx_bytes=76800, count_accuracy="NOT_MEASURED",
                  validation=args.validation, dma_wait=args.dma_wait,
                  check_execution=args.check_execution,
                  verify_pixels=args.validation == "full",
                  pixel_verified_frames=0, output_checksum_frames=0,
                  frames_requested=args.frames,
                  timing_scope="Offline artifact validation only; no frames were transferred")
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
    checks = Gray8Checks(checker, validation=args.validation, dma_read=dma_registers.read)
    checks.signatures()
    result["afi1"] = board_once.sync_afi1_hp1_32bit(
        api, SimpleNamespace(hwh_path=args.manifest.resolve().parent / manifest["hwh_path"]),
        stage="gray8-start")
    dma = overlay.axi_dma_0
    send_channel, recv_channel = dma.sendchannel, dma.recvchannel
    if args.dma_wait == "cooperative":
        send_channel = CooperativeDmaChannel(send_channel, read=dma_registers.read)
        recv_channel = CooperativeDmaChannel(recv_channel, read=dma_registers.read)
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

        stream = DoubleBufferStream(
            recv_channel=recv_channel, send_channel=send_channel,
            tx_buffers=tx, rx_buffers=rx, counter=counter,
            packet_encoder=encoding, packet_encoder_into=encoding_into,
            tx_bytes=TX_BYTES, before_transfer=checks.before_transfer,
            after_transfer=(checks.after_transfer_async if args.check_execution == "worker"
                            else checks.after_transfer),
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
        cpu_start = time.process_time()
        frames = asyncio.run(stream.run(source))
        cpu_elapsed = time.process_time() - cpu_start
        elapsed = time.perf_counter() - start
        if not _dma_idle(dma):
            raise RuntimeError("DMA channels are not idle after stream completion")
        if preflight.read_boot_id() != boot:
            raise RuntimeError("Board boot changed during execution")
        status = ("GRAY8_PIXEL_VERIFIED" if args.validation == "full"
                  else "GRAY8_RUNTIME_CHECKED")
        result.update(status=status, validation=args.validation, frames=len(frames),
                      pixel_verified_frames=checks.verified_frames,
                      output_checksum_frames=checks.output_checksum_frames,
                      elapsed_s=elapsed, throughput_fps=len(frames) / elapsed,
                      process_cpu_s=cpu_elapsed,
                      cpu_core_ms_per_frame=cpu_elapsed * 1000 / len(frames),
                      timing_scope=("Includes startup/warmup, MMIO checks, output checksum, "
                                    + ("and full input/output pixel verification" if args.validation == "full"
                                       else "without software per-pixel verification")),
                      check_timings_ms=checks.summarize_timings_ms(),
                      total_count=counter.total_count,
                      stage_timings_ms=runtime.summarize_stage_timings_ms(timings),
                      camera_metadata=capture_metadata,
                      capture_timings_ms=capture_timings, mapping_timings_ms=mapping_timings,
                      results=[asdict(f) for f in frames], checker_frames=checks.completions)
        if args.dma_wait == "cooperative":
            result["dma_wait_timings"] = {"tx": send_channel.timings, "rx": recv_channel.timings}
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
                          "dma_wait_timings",
                          "kernel_log_before", "kernel_log_after")}), flush=True)
        if held:
            # Do not let Python destruction release buffers still owned by DMA.
            print("GRAY8_FAIL_HOLD: process retains CMA buffers; operator recovery required", flush=True)
            while True:
                time.sleep(1)
    return 0 if result["status"] in ("GRAY8_PIXEL_VERIFIED", "GRAY8_RUNTIME_CHECKED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
