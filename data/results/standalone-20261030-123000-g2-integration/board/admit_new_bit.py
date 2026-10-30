import sys,json,hashlib,fcntl,time
from pathlib import Path
root=Path('/home/xilinx/standalone-g25-20261030');sys.path.insert(0,str(root))
index=json.loads((root/'staging-index.json').read_text())
for name,spec in index.items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==spec['sha256'],name
from scripts.standalone.bundle import verify_bundle,verify_runtime
from scripts.standalone.g2_readback import read_pipeline,snapshot_counters
from scripts.wo0015.board_preflight import audit_process_ownership,inspect_pynq_global_state
import scripts.standalone.g25_run, scripts.standalone.g1_run
assert 'pynq' not in sys.modules,'offline imports touched PYNQ'
assert not any('/dev/mem' in str(p.resolve()) or '/dev/video' in str(p.resolve()) for p in Path('/proc/self/fd').iterdir() if p.exists())
boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip();assert boot=='e48ff153-bd85-4dd6-98f6-438bd51da08d'
manifest=root/'export/display-manifest.json';md='2872e1598b3c3ab7ccb5d383f5cee6a8822dbd91eb0f198098eca067489a2966'
files=verify_bundle(manifest,md)
verify_runtime(json.loads(manifest.read_text())['runtime'],'/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
locks=[]
for name in ('/tmp/material-counter-desktop.lock','/run/material-standalone.lock'):
 f=open(name,'a');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(f)
ownership=audit_process_ownership()
import pynq
from pynq import MMIO,Overlay,Clocks,PL
old='2ec1f1dec5a2df2e07044e94ecacd5d20aeef19ec6787077c0728d24053a8b18'
before=inspect_pynq_global_state(pynq.__file__,require_empty_shutdown=True,expected_bit_path=Path('/home/xilinx/standalone-g2-smoke-20261030/g2-integrated.bit'),expected_bit_sha256=old,require_loaded_bit=True,allow_equivalent_bit_path=True)
old_pipeline=read_pipeline(MMIO(0x43c20000,0x1000))
dma=MMIO(0x40400000,0x1000);vdma=MMIO(0x43000000,0x1000)
old_dma={hex(i):dma.read(i) for i in (4,0x34)}
assert all(v&2 and not v&0x770 for v in old_dma.values())
assert vdma.read(4)&1 and not vdma.read(4)&0x770
overlay=Overlay(str(files['bit']),download=True)
Clocks.fclk0_mhz=50;Clocks.fclk1_mhz=100;Clocks.fclk2_mhz=142.857
new='79609f9a60c3e727b5f2f54a8d639a464a098715fb5eb28d294646a9735948e0'
assert hashlib.sha256(Path(PL.bitfile_name).read_bytes()).hexdigest()==new
after=inspect_pynq_global_state(pynq.__file__,require_empty_shutdown=True,expected_bit_path=files['bit'],expected_bit_sha256=new,require_loaded_bit=True)
control=MMIO(0x43c20000,0x1000)
report=dict(schema='material-counter.g2-monitor-admission.v1',boot_id=boot,ownership=ownership,
 offline_import_and_staging_hashes='PASS',files_checked=len(index),before=before,old_pipeline=old_pipeline,old_dma=old_dma,after=after,
 clocks_mhz=[Clocks.fclk0_mhz,Clocks.fclk1_mhz,Clocks.fclk2_mhz],pipeline=read_pipeline(control),counters=snapshot_counters(control),
 dma={hex(i):MMIO(0x40400000,0x1000).read(i) for i in (0,4,0x30,0x34)},
 vdma={hex(i):MMIO(0x43000000,0x1000).read(i) for i in (0,4)},
 afi={str(hp):{'read':MMIO(base,0x1000).read(0),'write':MMIO(base,0x1000).read(0x14)} for hp,base in ((0,0xf8008000),(1,0xf8009000),(2,0xf800a000))})
with (root/'g2-monitor-admission.json').open('x') as f:json.dump(report,f,indent=2)
print(json.dumps(report,indent=2))
