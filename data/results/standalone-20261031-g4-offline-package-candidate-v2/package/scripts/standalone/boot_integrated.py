"""Offline boot entry for the pinned standalone package; no network dependency."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import struct
import sys
import uuid

from .boot_policy import initialize_once,build_argv
from .bundle import verify_bundle,verify_runtime,sha256
from .delivery import verify_package
from .evidence import write_evidence
from .runtime_environment import verify_runtime_environment


class BoardBackend:
    """Initial configuration only; allocates no counting or display CMA frames."""
    def __init__(self,root,files,manifest):
        self.root=root;self.files=files;self.manifest=manifest;self.overlay=None

    def audit(self):
        from scripts.wo0015.board_preflight import audit_process_ownership
        return audit_process_ownership()

    def configure(self):
        # Only the once-per-new-boot policy reaches this path. There are no PL
        # register probes through stale metadata before the explicit download.
        from pynq import Overlay,Clocks
        self.overlay=Overlay(str(self.files['bit']),download=True)
        Clocks.fclk0_mhz=50;Clocks.fclk1_mhz=100;Clocks.fclk2_mhz=142.857

    def verify_loaded(self):
        import pynq
        from pynq import MMIO,Clocks
        from scripts.wo0015.board_preflight import inspect_pynq_global_state
        from .g2_readback import read_pipeline,snapshot_counters
        state=inspect_pynq_global_state(pynq.__file__,require_empty_shutdown=True,
            expected_bit_path=self.files['bit'],expected_bit_sha256=sha256(self.files['bit']),
            require_loaded_bit=True,allow_equivalent_bit_path=True)
        clocks=[Clocks.fclk0_mhz,Clocks.fclk1_mhz,Clocks.fclk2_mhz]
        if any(abs(actual-wanted)>.15 for actual,wanted in zip(clocks,(50,100,142.857))):
            raise RuntimeError('boot clocks do not match the admitted G2 build')
        pipeline=read_pipeline(MMIO(0x43c20000,0x1000))
        counters=snapshot_counters(MMIO(0x43c20000,0x1000))
        for base,offsets in ((0x40400000,(4,0x34)),(0x43000000,(4,))):
            mmio=MMIO(base,0x1000)
            for offset in offsets:
                value=mmio.read(offset)
                if value&0x770 or not value&3:raise RuntimeError('boot DMA is not idle/halted')
        return dict(global_state=state,clocks_mhz=clocks,pipeline=pipeline,counters=counters)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--index-sha256',required=True)
    parser.add_argument('--reuse-loaded',action='store_true',help='explicit late acceptance launch; never reconfigure')
    args=parser.parse_args(argv)
    root=args.root.resolve(strict=True)
    if root!=Path(__file__).resolve().parents[2]:raise RuntimeError('entrypoint and package root differ')
    if os.geteuid()!=0 or sys.platform!='linux' or sys.version_info[:2]!=(3,10) or struct.calcsize('P')!=4:
        raise RuntimeError('requires the admitted root PYNQ CPython 3.10 ARM32 runtime')
    if os.environ.get('XILINX_XRT')!='/usr':raise RuntimeError('requires XILINX_XRT=/usr')
    integrity=verify_package(root,args.index_sha256)
    runtime_compatibility=verify_runtime_environment(root/'board-runtime.json')
    config=json.loads((root/'boot-config.json').read_text())
    boot=str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
    run_root=Path('/var/lib/material-counter')/boot
    run_root.mkdir(parents=True,exist_ok=True)
    output=run_root/('session-'+str(uuid.uuid4())+'.json')
    manifest_path=root/'export/display-manifest.json';manifest=json.loads(manifest_path.read_text())
    manifest_digest=sha256(manifest_path)
    command=build_argv(root,config,boot,output,manifest_digest,sys.executable)
    command+=['--startup-record',str(output.with_suffix('.startup.json'))]
    files=verify_bundle(manifest_path,manifest_digest)
    verify_runtime(manifest['runtime'],root/'fonts/DroidSansFallbackFull.ttf',root/'fonts/DejaVuSans.ttf')
    locks=[]
    for name in ('/tmp/material-counter-desktop.lock','/run/material-standalone.lock'):
        stream=open(name,'a');fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(stream)
    lock_fds=[stream.fileno() for stream in locks]
    command+=['--inherited-lock-fds',*(str(fd) for fd in lock_fds)]
    backend=BoardBackend(root,files,manifest)
    uptime=float(Path('/proc/uptime').read_text().split()[0])
    marker=Path('/run/material-counter')/(boot+'.json')
    marker.parent.mkdir(parents=True,exist_ok=True)
    admission=initialize_once(backend,marker,boot,uptime,reuse_loaded=args.reuse_loaded)
    record=dict(schema='material-counter.integrated-boot-admission.v1',boot_id=boot,
        package_index_sha256=args.index_sha256,manifest_sha256=manifest_digest,
        bit_sha256=sha256(files['bit']),native_sha256=sha256(root/'native/rle_components_v2.so'),
        integrity=integrity,runtime_compatibility=runtime_compatibility,
        admission=admission,command=command,scope='initialization; not G4 acceptance')
    write_evidence(output.with_suffix('.admission.json'),record)
    print(json.dumps(record,separators=(',',':')),flush=True)
    os.chdir(root)
    for fd in lock_fds:os.set_inheritable(fd,True)
    os.execv(sys.executable,command)


if __name__=='__main__':main()
