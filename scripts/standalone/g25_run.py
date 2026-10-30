"""Bounded G2.5 synthetic RLE/HDMI pressure experiment with one hardware owner.

This runner does not implement G3 camera recovery or G4 autonomous startup.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import uuid

import cv2
import numpy as np

from .bundle import sha256, verify_bundle, verify_runtime
from .compositor import GrayComposer
from .driver_owner import DriverOwner
from .evidence import write_evidence,fail_hold
from .g1_run import setup_hardware, snapshot, _memory, _parse_proc_stat, _cpu_usage_percent
from .g2_readback import read_pipeline, snapshot_counters
from .pressure import make_input, counter_delta, validate_transfers
from scripts.rle.protocol import TX_BYTES, RX_CAPACITY, reconstruct
from scripts.rle.run_counter import golden_mask
from scripts.rle.transport import RleDmaTransport
from scripts.rle.counter import RleCounter
from scripts.wo0015.board_preflight import audit_process_ownership, inspect_pynq_global_state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--boot-id', required=True)
    parser.add_argument('--native-lib', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pattern', choices=('moving', 'high_runs'), required=True)
    parser.add_argument('--display', choices=('off', 'scan', 'dynamic'), required=True)
    parser.add_argument('--duration', type=float, default=35)
    parser.add_argument('--verify-pixels', action='store_true')
    parser.add_argument('--font', default='/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf')
    parser.add_argument('--latin-font', default='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    args = parser.parse_args(argv)
    if not args.execute or not 1 <= args.duration <= 600:
        parser.error('explicit execute and duration 1..600 required')
    if args.output.exists() or not args.native_lib.is_file():
        parser.error('new output and prebuilt native library required')
    files = verify_bundle(args.manifest, args.manifest_sha256)
    manifest = json.loads(args.manifest.read_text())
    if manifest.get('rle_monitor') != {'abi': 1, 'clock_hz': 50_000_000}:
        raise ValueError('requires reviewed new BIT manifest with RLE monitor ABI 1')
    verify_runtime(manifest['runtime'], args.font, args.latin_font)
    if os.geteuid() != 0 or os.environ.get('XILINX_XRT') != '/usr':
        raise RuntimeError('root and XILINX_XRT=/usr required')
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if boot != args.boot_id:
        raise RuntimeError('boot changed')
    locks = []
    import fcntl
    for name in ('/tmp/material-counter-desktop.lock', '/run/material-standalone.lock'):
        stream = open(name, 'a'); fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.append(stream)
    import pynq
    from pynq import MMIO, allocate
    files['digest'] = sha256(files['bit'])
    identity = inspect_pynq_global_state(pynq.__file__, require_empty_shutdown=True,
                expected_bit_path=files['bit'], expected_bit_sha256=files['digest'],
                require_loaded_bit=True, allow_equivalent_bit_path=True)
    report = dict(schema='material-counter.g25-pressure.v1', boot_id=boot,
            session_id=str(uuid.uuid4()), loaded=identity,
            bit_sha256=files['digest'], hwh_sha256=sha256(files['hwh']),
            manifest_sha256=args.manifest_sha256, native_sha256=sha256(args.native_lib),
            ownership=audit_process_ownership(), before_memory=_memory(),
            pattern=args.pattern, display=args.display, pixel_verification=args.verify_pixels,
            scope='synthetic concurrency; no real material accuracy or G3/G4 acceptance',
            state='STARTING', samples=[], cpu_samples=[], frames=[])
    owner = DriverOwner()
    transport = hw = frame = None
    try:
        before_dma = MMIO(0x40400000, 0x1000)
        if any(before_dma.read(i) & 0x770 or not before_dma.read(i) & 3 for i in (4, 0x34)):
            raise RuntimeError('RLE DMA not idle/halted before setup')
        hw = owner.call(setup_hardware, files, owner, timeout=60)
        report['clocks_mhz'] = hw.clocks
        dma = owner.retain(MMIO(0x40400000, 0x1000))
        control = owner.retain(MMIO(0x43c20000, 0x1000))
        report['pipeline_before'] = owner.call(read_pipeline, control)
        tx = owner.retain(allocate(shape=(TX_BYTES,), dtype='u1'))
        rx = owner.retain(allocate(shape=(RX_CAPACITY,), dtype='u1'))
        report['rle_buffers'] = {'tx_address': int(tx.physical_address),
                                'rx_address': int(rx.physical_address),
                                'tx_bytes': TX_BYTES, 'rx_bytes': RX_CAPACITY,
                                'allocations': 2}
        transport = RleDmaTransport(hw.overlay.axi_dma_0.sendchannel,
            hw.overlay.axi_dma_0.recvchannel, dma, control, tx, rx)
        if report['pipeline_before']['state']['done_seen']:
            transport._completed_frames = 1  # explicit admitted previous completion
        counter = RleCounter(library=args.native_lib)
        composer = GrayComposer(args.font, latin_font_path=args.latin_font,
                                gray_interpolation=cv2.INTER_LINEAR)
        cv2.setNumThreads(2)
        session = uuid.UUID(report['session_id'])

        def display(gray, binary, frame_id):
            nonlocal frame
            canvas = composer.render(gray, binary, session, frame_id,
                        status_lines=('G2.5 并发压力', args.pattern, '累计 {}'.format(counter.total_count)),line_y=200)
            frame = owner.call(hw.hdmi.newframe)
            np.copyto(frame, canvas)
            owner.call(hw.hdmi.writeframe, frame, timeout=2)
            frame = None

        if args.display == 'off':
            owner.call(hw.hdmi.close, timeout=3)
        else:
            warm = make_input(args.pattern, 0)
            display(warm, golden_mask(warm), 0)
            deadline = time.monotonic() + 2
            while True:
                observed = owner.call(snapshot, hw.monitor)
                if observed['session'] == session.int and observed['unique']:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError('no valid HDMI identity before pressure interval')
                time.sleep(.005)
        report['monitor_before'] = owner.call(snapshot, hw.monitor)
        report['counters_before'] = owner.call(snapshot_counters, control)
        start = last_sample = time.monotonic()
        previous_cpu = _parse_proc_stat(Path('/proc/stat').read_text())
        index = 0
        rx_lengths, transfers, totals = [], [], []
        while time.monotonic() - start < args.duration:
            frame_started = time.monotonic()
            gray = make_input(args.pattern, index)
            packet = owner.call(transport.transfer, gray, index, timeout=6)
            transfer_end = time.monotonic()
            if not packet.valid:
                raise RuntimeError('unexpected invalid RLE packet in valid pressure pattern')
            if args.verify_pixels:
                binary = reconstruct(packet)
                if not np.array_equal(binary, golden_mask(gray)):
                    raise RuntimeError('RLE full pixel mismatch')
            result = counter.update_rle(packet, capture_monotonic_ns=time.monotonic_ns())
            if args.display == 'dynamic':
                display(gray, reconstruct(packet), index + 1)
            ended = time.monotonic()
            rx_lengths.append(packet.actual_bytes)
            transfers.append(1000*(transfer_end-frame_started))
            totals.append(1000*(ended-frame_started))
            report['frames'].append(dict(frame=index, monotonic=ended, runs=packet.run_count,
                rx_bytes=packet.actual_bytes, foreground=packet.total_fg,
                transfer_ms=transfers[-1], total_ms=totals[-1], count=counter.total_count,
                dma=transport.snapshot()))
            index += 1
            if ended-last_sample >= 2:
                now_cpu = _parse_proc_stat(Path('/proc/stat').read_text())
                report['cpu_samples'].append(dict(monotonic=ended,
                    per_core_percent=_cpu_usage_percent(previous_cpu, now_cpu), memory=_memory()))
                previous_cpu = now_cpu
                dma_addresses = {hex(offset): owner.call(dma.read, offset) for offset in (0x18,0x48)}
                if dma_addresses['0x18'] != int(tx.physical_address) or dma_addresses['0x48'] != int(rx.physical_address):
                    raise RuntimeError('RLE DMA buffer address changed during pressure run')
                report['samples'].append(dict(monotonic=ended, processed=index,
                    monitor=owner.call(snapshot, hw.monitor),
                    vdma_status=owner.call(hw.vdma.read, 4),
                    dma_addresses=dma_addresses,
                    process_memory={line.split(':',1)[0]:line.split(':',1)[1].strip()
                        for line in Path('/proc/self/status').read_text().splitlines()
                        if line.startswith(('VmRSS:','VmSize:','RssAnon:','RssFile:','RssShmem:'))},
                    counters=owner.call(snapshot_counters, control)))
                last_sample = ended
        report['duration_s'] = time.monotonic()-start
        report['processed_frames'] = index
        report['processing_fps'] = index/report['duration_s']
        report['counters_after'] = owner.call(snapshot_counters, control)
        delta = counter_delta(report['counters_before'], report['counters_after'])
        report['counter_delta'] = delta
        report['transfer_consistency'] = validate_transfers(delta, index, rx_lengths)
        report['monitor_after'] = owner.call(snapshot, hw.monitor)
        report['latency_ms'] = {name: {f'p{p}': float(np.percentile(values,p)) for p in (50,95,99)}
                            for name, values in (('transfer_parse',transfers),('total',totals))}
        report['dma_after'] = {hex(i): owner.call(dma.read,i) for i in (0,4,0x30,0x34)}
        if any(report['dma_after'][hex(i)] & 0x770 or not report['dma_after'][hex(i)] & 2 for i in (4,0x34)):
            raise RuntimeError('RLE DMA not safely idle at stop')
        owner.call(transport.close)
        if args.display != 'off':
            owner.call(hw.hdmi.close, timeout=3)
        report['vdma_after'] = {hex(i): owner.call(hw.vdma.read,i) for i in (0,4)}
        if not report['vdma_after']['0x4'] & 1 or report['vdma_after']['0x4'] & 0x770:
            raise RuntimeError('VDMA did not safely halt')
        report['pipeline_after'] = owner.call(read_pipeline, control)
        owner.close()
        report.update(state='STOPPED', buffers_released=True, after_memory=_memory(),
                      total_count=counter.total_count, invalid_frames=counter.invalid_frames,
                      material_accuracy='NOT_MEASURED')
    except BaseException as error:
        report.update(state='FAIL_HOLD', error=repr(error), diagnostics=owner.diagnostics())
        fail_hold(lambda:write_evidence(args.output,report),'G25_FAIL_HOLD '+repr(error))
    write_evidence(args.output,report)
    print(json.dumps({key: report[key] for key in ('state','processed_frames','duration_s','processing_fps','counter_delta')}),flush=True)


if __name__ == '__main__':
    main()
