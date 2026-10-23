"""Reuse the unchanged packager; apply the previously approved boot configuration in a new bundle."""
import ast,difflib,hashlib,json,shutil,subprocess,sys,zipfile
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
PY=Path(r'C:\fpga\material-counter\.venv\Scripts\python.exe')
BOOT='822a9c29-37c0-438c-81c4-da998d57073b'
OLD_BOOT='cb087bbb-932d-4a6e-9600-98dd1c7e5f8d'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert int((OUT/'full-offline-final-exit-code.txt').read_text())==0
original=OUT.parent/'wo0015-first-board-bundle-20261015.zip'
assert sha(original)=='873d245ad92926b00e9f57b2793220ab14dca0c287f49c213d7af5d36dd14879'
scope={}
with zipfile.ZipFile(original) as z:
    for name,allowed in [('board_preflight.py',set()),('board_once.py',{'run_frames','_runtime_preflight','PynqBoardAPI.device_address'})]:
        before=ast.parse(z.read(name).decode())
        after=ast.parse((ROOT/'scripts/wo0015'/name).read_text(encoding='utf-8'))
        def definitions(tree):
            found={}
            for n in tree.body:
                if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):found[n.name]=ast.dump(n)
                if isinstance(n,ast.ClassDef):
                    for m in n.body:
                        if isinstance(m,ast.FunctionDef):found[n.name+'.'+m.name]=ast.dump(m)
            return found
        a,b=definitions(before),definitions(after)
        modified=[n for n in a if a[n]!=b.get(n)]
        assert set(modified)<=allowed,(name,modified)
        scope[name]={'modified_existing_functions':modified,'added_functions':sorted(set(b)-set(a))}
        diff=difflib.unified_diff(z.read(name).decode().splitlines(True),
                                  (ROOT/'scripts/wo0015'/name).read_text().splitlines(True),
                                  fromfile='approved-original/'+name,tofile='cma-compat/'+name)
        (OUT/(name+'.diff')).write_text(''.join(diff),encoding='utf-8')
(OUT/'scope-audit.json').write_text(json.dumps(scope,indent=2),encoding='utf-8')
staging=OUT/'packager-output-historical-boot'
p=subprocess.run([str(PY),str(ROOT/'scripts/wo0015/prepare_board_bundle.py'),'--out',str(staging)],capture_output=True,text=True)
(OUT/'packager-stdout.txt').write_text(p.stdout,encoding='utf-8')
(OUT/'packager-stderr.txt').write_text(p.stderr,encoding='utf-8')
assert p.returncode==0,p.stderr
bundle=OUT.parent/'wo0015-retry3-two-tier-bundle-20261015'
shutil.copytree(staging,bundle)
pre=bundle/'board_preflight.py'
raw=pre.read_bytes()
old=('AUTHORIZED_BOOT_ID = "'+OLD_BOOT+'"').encode()
new=('AUTHORIZED_BOOT_ID = "'+BOOT+'"').encode()
assert raw.count(old)==1
pre.write_bytes(raw.replace(old,new))
assert pre.read_bytes().replace(new,old)==(ROOT/'scripts/wo0015/board_preflight.py').read_bytes()
manifest_path=bundle/'run-manifest.json'
manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
manifest['boot_id']=BOOT
manifest['executor_sha256']['board_preflight.py']=sha(pre)
manifest_path.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
sums={p.name:sha(p) for p in sorted(bundle.iterdir()) if p.is_file() and p.name!='SHA256SUMS.json'}
(bundle/'SHA256SUMS.json').write_text(json.dumps(sums,indent=2)+'\n',encoding='utf-8')
assert sha(bundle/'wo15.bit')=='a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420'
assert sha(bundle/'wo15.hwh')=='bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6'
assert sha(bundle/'board_once.py')==sha(ROOT/'scripts/wo0015/board_once.py')
for mode in ('offline','mock'):
    command=[str(PY),'-B',str(bundle/'board_once.py'),'--mode',mode,'--manifest',str(manifest_path),'--manifest-sha256',sha(manifest_path),'--evidence-json',str(OUT/f'new-bundle-{mode}-result.json')]
    p=subprocess.run(command,capture_output=True,text=True)
    (OUT/f'new-bundle-{mode}-stdout.txt').write_text(p.stdout,encoding='utf-8')
    (OUT/f'new-bundle-{mode}-stderr.txt').write_text(p.stderr,encoding='utf-8')
    assert p.returncode==0,p.stderr+p.stdout
    r=json.loads((OUT/f'new-bundle-{mode}-result.json').read_text())
    assert not r['board_accessed'] and not r['pynq_imported']
archive=bundle.with_suffix('.zip')
with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
    for p in sorted(bundle.iterdir()):
        if p.is_file():z.write(p,p.name)
receipt={'status':'OFFLINE_PACKAGE_PASS_AUTHORIZED_RETRY3','board_accessed':False,'retry3_executed':False,
'bundle':str(bundle),'archive':str(archive),'archive_sha256':sha(archive),'manifest_sha256':sha(manifest_path),
'packaged_boot_id':BOOT,'board_once_sha256':sha(bundle/'board_once.py'),'board_preflight_sha256':sha(pre),
'source_board_preflight_sha256':sha(ROOT/'scripts/wo0015/board_preflight.py'),
'bit_sha256':sha(bundle/'wo15.bit'),'hwh_sha256':sha(bundle/'wo15.hwh'),
'runtime_admission_approved':True,'limitation':'Captain authorizes MEMINFO_IOMEM_VERIFIED and one ordered Retry3 sequence; fresh board admission and post-allocation gates remain mandatory.'}
(OUT/'updated-bundle-receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print(json.dumps(receipt,indent=2))
