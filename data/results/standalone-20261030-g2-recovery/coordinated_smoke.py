"""Pinned G2 evidence runner; one owner, no overlay reload, no retry.

Invoke through PYNQ Python as root with XILINX_XRT=/usr. This is a bounded
integration experiment, not the G3/G4 production counting service.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

sys.path[:0] = ['/home/xilinx/standalone-g2-smoke-20261030/goal-python',
                '/home/xilinx/standalone-g2-smoke-20261030/python-modules',
                '/home/xilinx/standalone-20261029-g1']

import cv2
import numpy as np
from pynq import MMIO, allocate
from scripts.wo0015.board_preflight import audit_process_ownership
from scripts.standalone.bundle import verify_bundle, verify_runtime, sha256
from scripts.standalone.driver_owner import DriverOwner
from scripts.standalone.g1_run import setup_hardware, snapshot
from scripts.standalone.compositor import GrayComposer
from scripts.rle.protocol import TX_BYTES, RX_CAPACITY, reconstruct
from scripts.rle.run_counter import synthetic_frames, golden_mask
from scripts.rle.transport import RleDmaTransport
from scripts.rle.counter import RleCounter

ROOT = Path('/home/xilinx/standalone-g2-smoke-20261030')
OUTPUT = ROOT / 'g2-coordinated-smoke-01.json'
BOOT = 'e48ff153-bd85-4dd6-98f6-438bd51da08d'
DIGEST = '2ec1f1dec5a2df2e07044e94ecacd5d20aeef19ec6787077c0728d24053a8b18'
MANIFEST_DIGEST = 'c115950a342535961f3de32b3b3e038d892e4bd8ef7491871a1c49291f47c4d9'
FONT = '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'
LATIN = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def main():
    assert os.geteuid() == 0 and os.environ['XILINX_XRT'] == '/usr'
    assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == BOOT
    assert not OUTPUT.exists(), 'never overwrite run evidence'
    locks = []
    for name in ('/tmp/material-counter-desktop.lock', '/run/material-standalone.lock'):
        stream = open(name, 'a'); fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.append(stream)
    report = dict(schema='material-counter.g2-coordinated-smoke.v1', boot_id=BOOT,
                  bit_sha256=DIGEST, ownership=audit_process_ownership(), frames=[],
                  session_id=str(uuid.uuid4()), state='STARTING',
                  scope='single hardware owner; synthetic smoke; no camera/accuracy/G3/G4 acceptance')
    files = verify_bundle(ROOT/'g2-display-smoke.manifest.json', MANIFEST_DIGEST)
    verify_runtime(json.loads((ROOT/'g2-display-smoke.manifest.json').read_text())['runtime'], FONT, LATIN)
    from pynq import PL
    assert sha256(PL.bitfile_name) == DIGEST, 'require same live bit; no automatic download'
    files['digest'] = DIGEST
    lib = ROOT/'rle-g2-adapter/board/rle_components_v2.so'
    assert lib.is_file(), 'prebuilt library required; never compile at runtime'
    counter = RleCounter(library=lib)
    composer = GrayComposer(FONT, latin_font_path=LATIN, gray_interpolation=cv2.INTER_LINEAR)
    owner = DriverOwner()
    hw = transport = frame = None
    try:
        hw = owner.call(setup_hardware, files, owner, timeout=60)
        report['clocks_mhz'] = hw.clocks
        report['hp2_read32'] = hw.hp2_read32
        dma = owner.retain(MMIO(0x40400000, 0x1000))
        control = owner.retain(MMIO(0x43c20000, 0x1000))
        tx = owner.retain(allocate(shape=(TX_BYTES,), dtype='u1'))
        rx = owner.retain(allocate(shape=(RX_CAPACITY,), dtype='u1'))
        transport = RleDmaTransport(hw.overlay.axi_dma_0.sendchannel,
                    hw.overlay.axi_dma_0.recvchannel, dma, control, tx, rx)
        # A preceding completed run leaves done_seen set; this new, explicitly
        # admitted session consumes that history and starts a new frame sequence.
        assert control.read(8) & 5 == 0
        transport._completed_frames = 1
        report['before_monitor'] = owner.call(snapshot, hw.monitor)
        session = uuid.UUID(report['session_id'])
        for index, gray in enumerate(synthetic_frames(12, 'moving')):
            started = time.monotonic()
            packet = owner.call(transport.transfer, gray, index, timeout=6)
            assert packet.valid
            binary = reconstruct(packet)
            assert np.array_equal(binary, golden_mask(gray))
            result = counter.update_rle(packet, capture_monotonic_ns=time.monotonic_ns())
            canvas = composer.render(gray, binary, session_id=session, frame_id=index,
                        status_lines=('G2 集成冒烟', '累计 {}'.format(counter.total_count)))
            frame = owner.call(hw.hdmi.newframe)
            np.copyto(frame, canvas)
            owner.call(hw.hdmi.writeframe, frame, timeout=2); frame = None
            report['frames'].append(dict(frame=index, runs=packet.run_count,
                    bytes=packet.actual_bytes, pixel_equal=True, count=counter.total_count,
                    elapsed_ms=1000*(time.monotonic()-started), dma=transport.snapshot()))
        time.sleep(0.1)
        report['after_monitor'] = owner.call(snapshot, hw.monitor)
        report['dma_after'] = {hex(i): owner.call(dma.read, i) for i in (0,4,0x30,0x34)}
        assert all(report['dma_after'][hex(i)] & 2 and not report['dma_after'][hex(i)] & 0x770 for i in (4,0x34))
        owner.call(transport.close)
        owner.call(hw.hdmi.close, timeout=3)
        report['vdma_after'] = {hex(i): owner.call(hw.vdma.read, i) for i in (0,4)}
        assert report['vdma_after']['0x4'] & 1
        assert not report['vdma_after']['0x4'] & 0x770
        owner.close()
        report.update(state='STOPPED', buffers_released=True, camera_opened=False,
                      accuracy='NOT_MEASURED', count=counter.total_count)
        OUTPUT.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    except BaseException as error:
        report.update(state='FAIL_HOLD', error=repr(error), diagnostics=owner.diagnostics())
        OUTPUT.write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
        while True:
            time.sleep(1)


if __name__ == '__main__':
    main()
