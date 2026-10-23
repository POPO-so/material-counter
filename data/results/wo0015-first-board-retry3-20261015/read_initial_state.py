"""Supplemental read-only register snapshot explicitly requested by the captain."""
import json,sys
from pathlib import Path
WO=Path('/home/xilinx/wo0015_first_board_retry3_20261015')
sys.path.insert(0,str(WO))
import board_once as run
import board_preflight as pre
MANIFEST_SHA='84c27012ec038d48267128af08deb9685eb1bb9a5fdb1a79e5da064083788312'
plan=pre.validate_manifest(WO/'run-manifest.json',MANIFEST_SHA)
assert pre.read_boot_id()==plan.boot_id
api=run.PynqBoardAPI(plan,stage='dma-single')
environment=api.audit_environment(require_empty_shutdown=False,require_loaded_bit=True)
api.open_mmio()
checker={f'0x{o:02x}':api.read32(pre.CHECKER_BASE,o) for o in range(0,0x64,4)}
dma={f'0x{o:02x}':api.read32(pre.DMA_BASE,o) for o in (0,4,0x18,0x28,0x30,0x34,0x48,0x58)}
errors=[]
if checker['0x04']!=pre.PACKET_MAGIC:errors.append('Checker signature mismatch')
if checker['0x60']!=pre.PACKER_SIGNATURE:errors.append('Packer signature mismatch')
for channel,offset in [('MM2S','0x04'),('S2MM','0x34')]:
    sr=dma[offset]
    if sr & run.DMA_ERROR_MASK:errors.append(channel+' Err != 0')
    if sr & run.DMA_HALTED:errors.append(channel+' Halted != 0')
result={'status':'INITIAL_STATE_PASS' if not errors else 'INITIAL_STATE_FAIL_STOPPED',
'boot_id':pre.read_boot_id(),'checker':checker,'dma':dma,'errors':errors,
'environment':environment,'cma':pre.read_cma_meminfo(),
'mmio_writes':0,'dma_started':False,
'version_note':'The RTL exposes Checker WO14 and Packer WO15 signatures; no separate version register exists.'}
assert result['boot_id']==plan.boot_id
(WO/'initial-state-result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2),flush=True)
if errors:
    run._record_terminal_failure('initial-state',plan,'; '.join(errors))
sys.exit(0 if not errors else 2)
