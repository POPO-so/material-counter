"""Explicitly authorized current-boot Stage 1; no DMA transfers or buffer allocation.

Operational adapter using unchanged deployed WO-0015 helpers. Cold FPGA manager
state 'unknown' is admitted only before this one overlay load; post-audit still
requires 'operating', exact signatures and exact AFI1 readback.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

DEPLOY = Path('/home/xilinx/wo0017/live-monitor-20261024-ee55267e')
sys.path.insert(0, str(DEPLOY/'scripts/wo0016'))
import run_stream_counter as runtime
pf, board = runtime.preflight, runtime.board_once


def admit_manager(state, flags):
    if state not in ('unknown', 'operating') or flags != '0':
        raise RuntimeError('Stage 1 requires cold unknown or operating manager, flags=0')
    return {'state': state, 'flags': flags}


def execute(args):
    if not args.execute or not args.approve_once or args.authorize_stage != 'overlay-only':
        raise RuntimeError('explicit --execute --authorize-stage overlay-only --approve-once required')
    if os.geteuid() != 0 or os.environ.get('XILINX_XRT') != '/usr':
        raise RuntimeError('root and XILINX_XRT=/usr required')
    if pf.read_boot_id() != args.expected_boot_id:
        raise RuntimeError('live Boot differs from explicit approval')
    plan = pf.validate_manifest(args.manifest, args.manifest_sha256, approved_boot_id=args.expected_boot_id)
    if not plan.ready_for_board or plan.manifest['clock_mhz'] != 50:
        raise RuntimeError('manifest not board-ready at 50MHz')
    board._assert_no_terminal_failure()
    out = Path(args.output_dir)
    expected = Path('/var/lib/material-counter-live') / args.expected_boot_id / 'stage1'
    if out != expected or out.is_symlink() or out.exists():
        raise RuntimeError('new isolated current-Boot stage1 directory required')
    mem = pf.read_cma_meminfo()
    if mem['CmaFree'] <= 32768:
        raise RuntimeError('CmaFree must be strictly above 32768KiB')
    admission = pf.read_cma_admission(meminfo=mem)
    pf.validate_runtime_cma(mem, admission.ranges, (plan.hp0_range, plan.hp1_range))
    ownership = pf.audit_process_ownership()
    runtime._require_camera_device_unowned('/dev/video0')
    manager = admit_manager(Path('/sys/class/fpga_manager/fpga0/state').read_text().strip(),
                            Path('/sys/class/fpga_manager/fpga0/flags').read_text().strip())
    spec = importlib.util.find_spec('pynq')
    if spec is None or not spec.origin:
        raise RuntimeError('PYNQ package unavailable')
    global_state = pf.inspect_pynq_global_state(spec.origin, require_empty_shutdown=True, require_loaded_bit=False)
    before = {'boot_id': plan.boot_id, 'process_ownership': ownership, 'fpga_manager': manager,
              'pynq_state': global_state, 'kernel_log': pf.read_kernel_log()}
    index_raw = (DEPLOY/'deployment-index.json').read_bytes()
    if hashlib.sha256(index_raw).hexdigest() != args.deployment_index_sha256:
        raise RuntimeError('deployment index differs from independently pinned SHA-256')
    index = json.loads(index_raw)
    for name, wanted in index['files'].items():
        if hashlib.sha256((DEPLOY/name).read_bytes()).hexdigest() != wanted:
            raise RuntimeError('deployed source hash changed: '+name)
    if pf.read_boot_id() != plan.boot_id or pf.read_cma_meminfo()['CmaFree'] <= 32768:
        raise RuntimeError('Boot/CMA changed before one-shot claim')
    out.mkdir(parents=True, mode=0o700)
    for parent in (out, out.parent):
        os.chmod(parent, 0o700)
    identity = {'schema':1,'stage':'overlay-only','boot_id':plan.boot_id,
                'manifest_sha256':plan.manifest_sha256,'clock_mhz':50,
                'executor_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    runtime.claim_once_guard(out/'consumed.json', identity)
    evidence = {'status':'STAGE1_FAIL','boot_id':plan.boot_id,'manifest_sha256':plan.manifest_sha256,
                'cma_before_kib':mem, 'manager_before':manager, 'dma_started':False,
                'buffers_allocated':False,'guard':str(out/'consumed.json'),'executor_sha256':identity['executor_sha256']}
    try:
        api = board.PynqBoardAPI(plan, stage='overlay-only', cma_ranges=admission.ranges)
        print('STAGE1_ONE_SHOT_CLAIMED', flush=True)
        api.load_overlay()
        evidence['overlay_download_returned'] = True
        evidence['afi_sync'] = board.sync_afi1_hp1_32bit(api,plan,stage='overlay-only')
        evidence['afi_snapshot'] = board.read_afi1_snapshot(api,'stage1-live-monitor')
        runtime.verify_afi1_readback(evidence['afi_snapshot'])
        api.open_mmio()
        if (api.read32(pf.CHECKER_BASE,0x04) != pf.PACKET_MAGIC or
                api.read32(pf.CHECKER_BASE,board.PACKER_SIGNATURE) != pf.PACKER_SIGNATURE):
            raise RuntimeError('Checker/Packer signature mismatch')
        # The overlay-only API maps Checker only; read the two allowlisted
        # DMASR addresses through its existing read-only physical accessor.
        statuses = {'mm2s':board.read_phys32_readonly(pf.DMA_BASE+0x04),
                    's2mm':board.read_phys32_readonly(pf.DMA_BASE+0x34)}
        evidence['dma_status'] = {k:hex(v) for k,v in statuses.items()}
        if any(v != board.DMA_HALTED for v in statuses.values()):
            raise RuntimeError('DMA initial state must be exactly halted=0x1')
        post = board._post_stage_audit(api,plan,before)
        after = pf.read_cma_meminfo()
        if after['CmaFree'] <= 32768 or pf.read_boot_id() != plan.boot_id:
            raise RuntimeError('post-load Boot/CMA failed')
        receipt = board.write_overlay_pass_receipt(out/'overlay-pass.json',plan,post)
        board.require_overlay_pass_receipt(out/'overlay-pass.json',plan)
        evidence.update(status='STAGE1_PASS',receipt=str(out/'overlay-pass.json'),
            receipt_sha256=hashlib.sha256((out/'overlay-pass.json').read_bytes()).hexdigest(),
            cma_after_kib=after,post_audit=post)
        print(json.dumps(evidence),flush=True)
    except BaseException as exc:
        evidence['error'] = type(exc).__name__+': '+str(exc)
        raise
    finally:
        runtime._write_json_exclusive(out/'evidence.json',evidence)


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    for key in ['manifest','manifest-sha256','expected-boot-id','output-dir','authorize-stage','deployment-index-sha256']:
        p.add_argument('--'+key,required=True)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--approve-once',action='store_true')
    execute(p.parse_args())
