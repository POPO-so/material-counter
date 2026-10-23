import os,pwd,json,hashlib,subprocess
from pathlib import Path
p=Path('/home/xilinx/wo0015_first_board_retry1_20261015')
assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()=='822a9c29-37c0-438c-81c4-da998d57073b'
assert not p.is_symlink() and p.resolve()==p and p.is_dir()
entries=[p]+list(p.rglob('*'))
assert all(not q.is_symlink() and (q==p or p in q.resolve().parents) for q in entries)
assert not list(Path('/var/lib/wo0015-s2mm').glob('*'))
expected={'wo15.bit':'a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420','wo15.hwh':'bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6'}
def hashes():return {n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in expected}
assert hashes()==expected
before={str(q.relative_to(p)):{'uid':q.stat().st_uid,'gid':q.stat().st_gid,'mode':oct(q.stat().st_mode&0o777)} for q in entries}
account=pwd.getpwnam('xilinx')
subprocess.run(['chown','-R','--no-dereference','xilinx:xilinx',str(p)],check=True)
assert all(q.stat().st_uid==account.pw_uid and q.stat().st_gid==account.pw_gid for q in entries)
assert all((q.stat().st_mode&0o600)==0o600 for q in entries if q.is_file())
assert hashes()==expected
result={'status':'PERMISSION_REPAIR_PASS','path':str(p),'uid':account.pw_uid,'gid':account.pw_gid,'before':before,'sha256_after':hashes(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
print(json.dumps(result,indent=2),flush=True)
