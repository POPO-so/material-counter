"""One authorized clean-state check and exact-byte archive; no MMIO writes."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import traceback

WO=Path('/home/xilinx/wo0015_first_board_retry3_20261015')
OUT=Path('/var/lib/wo0015-s2mm/continuations/watermark-retry2-20261015')
GUARD=Path('/var/lib/wo0015-s2mm')
HISTORY=GUARD/'history/20261015-retry1-cma-accounting'
ONCE=GUARD/'cma-watermark-recovery-consumed.json'
BOOT='822a9c29-37c0-438c-81c4-da998d57073b'
EXPECTED_DMA={0:0x10003,4:0,0x18:0,0x28:0,0x30:0x10003,0x34:0,0x48:0,0x58:0}

def sha(raw):return hashlib.sha256(raw).hexdigest()
def safe(path):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('symlink path rejected: '+str(path))
def sync_dir(path):
    fd=os.open(str(path),os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
def exclusive(path,raw,metadata=None):
    safe(path)
    fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(raw)
        if metadata:
            os.fchown(f.fileno(),metadata.st_uid,metadata.st_gid)
            os.fchmod(f.fileno(),stat.S_IMODE(metadata.st_mode))
        f.flush();os.fsync(f.fileno())
    sync_dir(path.parent)
def emit(name,row):
    exclusive(OUT/name,(json.dumps(row,indent=2,sort_keys=True)+'\n').encode())
    print(json.dumps(row),flush=True)
def check_sources():
    for path in (WO,OUT,GUARD):safe(path)
    locks_raw=(OUT/'locked-evidence.json').read_bytes()
    if sha(locks_raw)!=LOCK['locked-evidence.json']:raise ValueError('base lock differs from pinned launcher')
    locks=json.loads(locks_raw)
    for n,h in locks['deployed_sha256'].items():
        safe(WO/n)
        if sha((WO/n).read_bytes())!=h:raise ValueError('base deployment changed: '+n)
    execution_raw=(OUT/'execution-lock.json').read_bytes()
    if sha(execution_raw)!=LOCK['execution-lock.json']:raise ValueError('execution lock differs from launcher')
    execution=json.loads(execution_raw)
    for n,h in execution.items():
        if h!=LOCK[n]:raise ValueError('execution digest differs from pinned launcher')
        safe(OUT/n)
        if sha((OUT/n).read_bytes())!=h:raise ValueError('continuation source changed: '+n)
    return locks
def current_guards():
    return {p.name:sha(p.read_bytes()) for p in GUARD.iterdir() if p.is_file() and not p.is_symlink()}
def archive_originals(locks,pre,api):
    names=('dma-single-consumed.json','terminal-failure.json')
    originals={}
    if HISTORY.exists() or HISTORY.is_symlink():raise ValueError('archive target already exists')
    if ONCE.exists() or ONCE.is_symlink():raise ValueError('watermark recovery already consumed')
    if current_guards()!=locks['guard_sha256']:raise ValueError('root Guard set/bytes changed')
    for n in names:
        safe(GUARD/n);raw=(GUARD/n).read_bytes();metadata=(GUARD/n).stat()
        if not stat.S_ISREG(metadata.st_mode):raise ValueError('nonregular Guard')
        originals[n]=(raw,metadata)
    marker=json.loads(originals['terminal-failure.json'][0])
    if marker.get('stage')!='dma-single' or marker.get('reason')!='ExecutionFailure: CmaFree did not show the required allocated-page decrease before DMA':
        raise ValueError('failure is not the reviewed pre-transfer CMA accounting rejection')
    single=json.loads(originals['dma-single-consumed.json'][0])
    if single.get('stage')!='dma-single' or single.get('boot_id')!=BOOT or not single.get('consumed'):
        raise ValueError('old single consumed identity differs')
    exclusive(ONCE,(json.dumps({'stage':'cma-watermark-recovery','status':'CONSUMED',
                             'boot_id':BOOT,'base_manifest_sha256':locks['deployed_sha256']['run-manifest.json'],
                             'execution_lock_sha256':sha((OUT/'execution-lock.json').read_bytes())})+'\n').encode())
    HISTORY.mkdir(mode=0o700);safe(HISTORY);sync_dir(HISTORY.parent)
    for n,(raw,metadata) in originals.items():
        exclusive(HISTORY/n,raw,metadata)
        if sha((HISTORY/n).read_bytes())!=locks['guard_sha256'][n] or (GUARD/n).read_bytes()!=raw:
            raise ValueError('archive/source byte verification failed')
    if pre.read_boot_id()!=BOOT:raise ValueError('boot changed before marker removal')
    if {o:api.read32(pre.DMA_BASE,o) for o in EXPECTED_DMA}!=EXPECTED_DMA:
        raise ValueError('DMA state changed before marker removal')
    # Preserve the old consumed token so the legacy executor stays blocked.
    # This newly authorized attempt has its own fixed retry2 once tokens.
    (GUARD/'terminal-failure.json').unlink();sync_dir(GUARD)
    return {n:{'archive':str(HISTORY/n),'sha256':locks['guard_sha256'][n],
               'uid':m.st_uid,'gid':m.st_gid,'mode':stat.S_IMODE(m.st_mode)}
            for n,(_,m) in originals.items()}

def main():
    plan=None;run=None;api=None
    try:
        locks=check_sources()
        if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=BOOT:raise ValueError('boot differs')
        if current_guards()!=locks['guard_sha256']:raise ValueError('raw Guard set/bytes differ')
        sys.path.insert(0,str(WO))
        import board_preflight as pre
        import board_once as run
        plan=pre.validate_manifest(WO/'run-manifest.json',locks['deployed_sha256']['run-manifest.json'])
        mem=pre.read_cma_meminfo()
        if mem['CmaTotal']!=131072 or mem['CmaFree']<32768:raise ValueError('Tier 1 CMA water level rejected')
        admission=pre.read_cma_admission('/proc/device-tree/reserved-memory',meminfo=mem)
        pre.validate_runtime_cma(mem,admission.ranges,(plan.hp0_range,plan.hp1_range))
        if pre._system_ram_intervals(Path('/proc/iomem').read_text())!=((0,0x20000000),):
            raise ValueError('System RAM boundary differs')
        api=run.PynqBoardAPI(plan,stage='dma-single')
        environment=api.audit_environment(require_empty_shutdown=False,require_loaded_bit=True)
        run.require_overlay_pass_receipt(GUARD/'overlay-only-pass.json',plan)
        api.open_mmio()
        checker={f'0x{o:02x}':api.read32(pre.CHECKER_BASE,o) for o in range(0,0x64,4)}
        dma={o:api.read32(pre.DMA_BASE,o) for o in EXPECTED_DMA}
        expected_checker={f'0x{o:02x}':0 for o in range(0,0x64,4)}
        expected_checker.update({'0x00':1,'0x04':0x574f3134,'0x60':0x574f3135})
        if dma!=EXPECTED_DMA or checker!=expected_checker:raise ValueError('Run-only clean register state differs')
        if pre.read_boot_id()!=BOOT:raise ValueError('boot changed across reads')
        result={'status':'PRECHECK_PASS','boot_id':BOOT,'dma':dma,'checker':checker,
                'cma':mem,'cma_admission':admission.as_evidence(),'environment':environment,
                'mmio_writes':0,'overlay_loads':0,'cma_allocations':0,
                'base_deployed_sha256':locks['deployed_sha256'],
                'continuation_execution_sha256':sha((OUT/'continuation-execution.json').read_bytes())}
        emit('precheck-result.json',result)
        result['post_stage_audit']=run._post_stage_audit(api,plan,environment)
        result['archived']=archive_originals(locks,pre,api)
        for name in ('overlay-only-pass.json','overlay-only-consumed.json','run-only-init-consumed.json'):
            if sha((GUARD/name).read_bytes())!=locks['guard_sha256'][name]:raise ValueError('preserved Guard changed')
        if pre.read_boot_id()!=BOOT:raise ValueError('boot changed after archive')
        result['status']='PASS';result['step1']='PASS (Reused Run-only; no control writes)'
        emit('step1-acceptance.json',result)
        return 0
    except BaseException as exc:
        result={'status':'FAIL_STOPPED','error':str(exc),'traceback':traceback.format_exc(),'mmio_writes':0}
        if run is not None and plan is not None:
            try:run._record_terminal_failure('watermark-recovery-precheck',plan,result['error'])
            except BaseException as e:result['marker_error']=str(e)
        if api is not None and api._mmio:
            try:
                result['dma']={o:api.read32(pre.DMA_BASE,o) for o in EXPECTED_DMA}
                result['checker']={f'0x{o:02x}':api.read32(pre.CHECKER_BASE,o) for o in range(0,0x64,4)}
            except BaseException as e:result['read_error']=str(e)
        emit('transition-failure.json',result)
        return 2
if __name__=='__main__':raise SystemExit(main())
