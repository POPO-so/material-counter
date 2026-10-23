"""Authorized recovery adapter. No Overlay load, buffer allocation, or frame transfer."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import traceback

import run_only_model as model

WO = Path('/home/xilinx/wo0015_first_board_retry3_20261015')
OUT = Path('/home/xilinx/wo0015_runonly_resume_retry1_20261015')
GUARD = Path('/var/lib/wo0015-s2mm')
ARCHIVE = GUARD/'history/20261015-retry3-initial-state-terminal-failure.json'
ONCE = GUARD/'run-only-init-consumed.json'

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def safe_path(path):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise model.ProposalRejected('symlink path rejected: '+str(path))

def exclusive_bytes(path, data, *, mode=0o600, owner=None):
    safe_path(path)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        if owner is not None:
            os.fchown(stream.fileno(), *owner)
        os.fchmod(stream.fileno(), mode)
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(path.parent)

def sync_dir(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)

def emit(name, payload):
    exclusive_bytes(OUT/name, (json.dumps(payload, indent=2, sort_keys=True)+'\n').encode())
    print(json.dumps(payload, sort_keys=True), flush=True)

def locked_files():
    safe_path(OUT); safe_path(WO); safe_path(GUARD)
    locks = json.loads((OUT/'locked-evidence.json').read_text())
    execution=json.loads((OUT/'execution-lock.json').read_text())
    for name,expected in execution.items():
        safe_path(OUT/name)
        if sha((OUT/name).read_bytes())!=expected:
            raise model.ProposalRejected('recovery source digest differs: '+name)
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != model.BOOT:
        raise model.ProposalRejected('boot differs before imports/MMIO')
    for name, expected in locks['deployed_sha256'].items():
        safe_path(WO/name)
        if sha((WO/name).read_bytes()) != expected:
            raise model.ProposalRejected('deployed hash changed: '+name)
    return locks

def guard_bytes(locks, *, require_original=True):
    for name in ('overlay-only-consumed.json','overlay-only-pass.json'):
        safe_path(GUARD/name)
        if sha((GUARD/name).read_bytes()) != locks['guard_sha256'][name]:
            raise model.ProposalRejected('Overlay guard/receipt changed')
    for name in ('dma-single-consumed.json','dma-three-consumed.json','dma-single-pass.json'):
        if (GUARD/name).exists() or (GUARD/name).is_symlink():
            raise model.ProposalRejected('frame stage already consumed')
    if require_original:
        safe_path(GUARD/'terminal-failure.json')
        raw = (GUARD/'terminal-failure.json').read_bytes()
        if sha(raw) != locks['guard_sha256']['terminal-failure.json']:
            raise model.ProposalRejected('historical terminal bytes changed')
        return raw

class LiveRunOnly:
    def __init__(self, api, pre, plan, locks):
        self.api=api; self.pre=pre; self.plan=plan; self.locks=locks
        self.writes=[]; self.claimed=False; self.opened=False
    def snapshot(self):
        locked_files()
        environment=self.api.audit_environment(require_empty_shutdown=False,require_loaded_bit=True)
        mem=self.pre.read_cma_meminfo()
        admission=self.pre.read_cma_admission('/proc/device-tree/reserved-memory',meminfo=mem)
        self.pre.validate_runtime_cma(mem,admission.ranges,(self.plan.hp0_range,self.plan.hp1_range))
        if not self.opened:
            self.api.open_mmio(); self.opened=True
        checker={f'0x{o:02x}':self.api.read32(self.pre.CHECKER_BASE,o) for o in range(0,0x64,4)}
        dma={o:self.api.read32(self.pre.DMA_BASE,o) for o in model.REG_OFFSETS}
        result={'identity':dict(model.IDENTITY),'checker_signature':checker['0x04'],
                'packer_signature':checker['0x60'],
                'counters':{k:v for k,v in checker.items() if k not in ('0x00','0x04','0x60')},
                'dma':dma,'CmaTotal':mem['CmaTotal'],'CmaFree':mem['CmaFree'],
                'system_ram':[0,0x20000000], 'ownership_clean':True,
                'checker_raw':checker,'environment':environment,'cma_admission':admission.as_evidence()}
        # The envelope source must independently prove the exact 512 MiB RAM range.
        ram=self.pre._system_ram_intervals(Path('/proc/iomem').read_text())
        if ram != ((0,0x20000000),):
            raise model.ProposalRejected('exact System RAM boundary changed')
        if self.boot_id()!=model.BOOT:raise model.ProposalRejected('boot changed across snapshot')
        return result
    def boot_id(self):return self.pre.read_boot_id()
    def claim_once(self):
        guard_bytes(self.locks)
        if ARCHIVE.exists() or ONCE.exists():raise model.ProposalRejected('recovery already consumed')
        exclusive_bytes(ONCE,(json.dumps({'status':'CONSUMED','stage':'run-only-init',**model.IDENTITY})+'\n').encode())
        self.claimed=True
    def write32(self, offset, value):
        expected=(0x30,0)[len(self.writes)] if len(self.writes)<2 else None
        if not self.claimed or offset!=expected or value!=0x10003:
            raise model.ProposalRejected('Run-only write whitelist/order violation')
        if self.boot_id()!=model.BOOT:raise model.ProposalRejected('boot changed before control write')
        if self.api.read32(self.pre.DMA_BASE,offset)!=0x10002:
            raise model.ProposalRejected('CR changed immediately before write')
        # Durable intent precedes each write. Never retry if the write raises.
        intent={'offset':offset,'address':model.DMA+offset,'value':value,'attempt':len(self.writes)+1}
        exclusive_bytes(OUT/f'run-write-{len(self.writes)+1}-intent.json',(json.dumps(intent)+'\n').encode())
        self.writes.append(intent)
        self.api.write32(self.pre.DMA_BASE,offset,value)
    def read32(self,offset):
        if offset not in (4,0x34):raise model.ProposalRejected('poll read outside whitelist')
        if self.boot_id()!=model.BOOT:raise model.ProposalRejected('boot changed while polling')
        return self.api.read32(self.pre.DMA_BASE,offset)
    def monotonic(self):return time.monotonic()
    def sleep(self,seconds):time.sleep(seconds)

def restore_int_keys(result):
    for name in ('before','after'):
        result[name]['dma']={int(k):v for k,v in result[name]['dma'].items()}
    return result

def archive_terminal(raw, expected_sha):
    source=GUARD/'terminal-failure.json'
    safe_path(source); safe_path(ARCHIVE)
    if sha(source.read_bytes())!=expected_sha or sha(raw)!=expected_sha:
        raise model.ProposalRejected('terminal changed before archive')
    metadata=source.stat()
    if not stat.S_ISREG(metadata.st_mode):raise model.ProposalRejected('terminal is not regular')
    ARCHIVE.parent.mkdir(exist_ok=True)
    safe_path(ARCHIVE)
    exclusive_bytes(ARCHIVE,raw,mode=stat.S_IMODE(metadata.st_mode),owner=(metadata.st_uid,metadata.st_gid))
    if sha(ARCHIVE.read_bytes())!=expected_sha or source.read_bytes()!=raw:
        raise model.ProposalRejected('archive copy/source verification failed; canonical retained')
    # This is the single explicitly authorized removal, after durable exact-byte copy.
    source.unlink()
    sync_dir(GUARD)
    return {'source':str(source),'archive':str(ARCHIVE),'sha256':expected_sha,
            'original_mode':stat.S_IMODE(metadata.st_mode),'original_uid':metadata.st_uid,'original_gid':metadata.st_gid}

def main(mode):
    live=None; run=None; plan=None
    try:
        locks=locked_files()
        sys.path.insert(0,str(WO))
        import board_preflight as pre
        import board_once as run
        plan=pre.validate_manifest(WO/'run-manifest.json',model.MANIFEST)
        raw=guard_bytes(locks)
        api=run.PynqBoardAPI(plan,stage='dma-single')
        live=LiveRunOnly(api,pre,plan,locks)
        if mode=='run-only':
            if ONCE.exists() or ARCHIVE.exists():raise model.ProposalRejected('recovery already consumed')
            result=model.exercise_proposed_sequence(live,timeout_s=1.0)
            result['actual_write_intents']=live.writes
            if result['status']!='RUN_ONLY_PASS':raise model.ProposalRejected(json.dumps(result))
            result['post_stage_audit']=run._post_stage_audit(api,plan,result['before']['environment'])
            guard_bytes(locks)
            emit('run-only-result.json',result)
        elif mode=='archive':
            result=restore_int_keys(json.loads((OUT/'run-only-result.json').read_text()))
            archive_plan=model.terminal_archive_plan(raw,locks['guard_sha256']['terminal-failure.json'],result)
            once=json.loads(ONCE.read_text()); model.require_identity(once)
            if once.get('stage')!='run-only-init' or once.get('status')!='CONSUMED':
                raise model.ProposalRejected('Run-only once receipt differs')
            fresh=live.snapshot()
            # Ignore global CmaFree drift; require a fresh complete Run-only poststate.
            checked=dict(result,after=fresh)
            model.terminal_archive_plan(raw,locks['guard_sha256']['terminal-failure.json'],checked)
            audit=run._post_stage_audit(api,plan,result['after']['environment'])
            receipt={'status':'PASS','step1':'PASS (Resumed via Run-only)',**model.IDENTITY,
                     'run_result_sha256':sha((OUT/'run-only-result.json').read_bytes()),
                     'postflight':fresh,'post_stage_audit':audit,'archive_plan':archive_plan,
                     'old_overlay_pass_sha256':locks['guard_sha256']['overlay-only-pass.json']}
            # Write prepared receipt first. Any later error re-establishes terminal.
            emit('archive-prepared.json',receipt)
            receipt['archive']=archive_terminal(raw,locks['guard_sha256']['terminal-failure.json'])
            guard_bytes(locks,require_original=False)
            if pre.read_boot_id()!=model.BOOT:raise model.ProposalRejected('boot changed after archive')
            emit('step1-acceptance.json',receipt)
        else:raise model.ProposalRejected('unsupported recovery mode')
        return 0
    except BaseException as exc:
        failure={'status':'FAIL_STOPPED','stage':mode,'error':type(exc).__name__+': '+str(exc),
                 'traceback':traceback.format_exc(),'write_intents':live.writes if live else []}
        if run is not None and plan is not None:
            try:run._record_terminal_failure('recovery-'+mode,plan,failure['error'])
            except BaseException as e:failure['marker_error']=str(e)
        # Read-only diagnostics are best effort and can never authorize recovery.
        if live is not None and live.opened:
            try:failure['final_snapshot']=live.snapshot()
            except BaseException as e:failure['snapshot_error']=str(e)
        try:
            import subprocess
            failure['dmesg']=subprocess.run(['dmesg','--color=never'],capture_output=True,text=True).stdout
            failure['meminfo']=Path('/proc/meminfo').read_text()
        except BaseException as e:failure['os_evidence_error']=str(e)
        emit(mode+'-failure.json',failure)
        return 2

if __name__=='__main__':
    raise SystemExit(main(sys.argv[1]))
