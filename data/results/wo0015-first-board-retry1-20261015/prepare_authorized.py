"""Apply only the explicitly reauthorized boot binding to an isolated deployment."""
import ast, difflib, hashlib, json, subprocess, zipfile
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
OLD='cb087bbb-932d-4a6e-9600-98dd1c7e5f8d'
NEW='822a9c29-37c0-438c-81c4-da998d57073b'
ZIP=OUT.parent/'wo0015-first-board-bundle-20261015.zip'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(ZIP)=='873d245ad92926b00e9f57b2793220ab14dca0c287f49c213d7af5d36dd14879'
DEP=OUT/'authorized-deployment'
DEP.mkdir()
with zipfile.ZipFile(ZIP) as a:a.extractall(DEP)
original={p.name:sha(p) for p in DEP.iterdir() if p.is_file()}
source=DEP/'board_preflight.py'
before=source.read_bytes()
old_line=('AUTHORIZED_BOOT_ID = "'+OLD+'"').encode()
new_line=('AUTHORIZED_BOOT_ID = "'+NEW+'"').encode()
assert before.count(old_line)==1
source.write_bytes(before.replace(old_line,new_line))
assert source.read_bytes().replace(new_line,old_line)==before
(OUT/'boot-constant-only.diff').write_text(''.join(difflib.unified_diff(before.decode().splitlines(True),source.read_text().splitlines(True),fromfile='original/board_preflight.py',tofile='authorized/board_preflight.py')),encoding='utf-8')
manifest=DEP/'run-manifest.json'
m=json.loads(manifest.read_text())
assert m['boot_id']==OLD
m['boot_id']=NEW
m['executor_sha256']['board_preflight.py']=sha(source)
manifest.write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
sums=DEP/'SHA256SUMS.json'
s=json.loads(sums.read_text())
s['board_preflight.py']=sha(source)
s['run-manifest.json']=sha(manifest)
sums.write_text(json.dumps(s,indent=2)+'\n',encoding='utf-8')
after={p.name:sha(p) for p in DEP.iterdir() if p.is_file()}
changed=[n for n in original if original[n]!=after[n]]
assert set(changed)=={'board_preflight.py','run-manifest.json','SHA256SUMS.json'}
assert after['wo15.bit']=='a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420'
assert after['wo15.hwh']=='bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6'
receipt={'old_boot_id':OLD,'authorized_boot_id':NEW,'original_zip':str(ZIP),'original_zip_sha256':sha(ZIP),'old_manifest_sha256':original['run-manifest.json'],'manifest_sha256':sha(manifest),'changes':changed,'before':original,'after':after,'functional_code_changed':False}
(OUT/'authorization-rebind.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
old_controller=(OUT.parent/'wo0015-first-board-20261015/controller.py').read_text(encoding='utf-8')
controller=old_controller.replace(OLD,NEW).replace("REMOTE = '/home/xilinx/wo0015_first_board_20261015'","REMOTE = '/home/xilinx/wo0015_first_board_retry1_20261015'")
needle="            elif task['op'] == 'command':"
insert="""            elif task['op'] == 'upload-config':
                with client.open_sftp() as sftp:
                    for filename, local in task['files'].items():
                        if filename not in ('board_preflight.py','run-manifest.json','SHA256SUMS.json'):
                            raise RuntimeError('Unauthorized configuration filename')
                        sftp.put(local,REMOTE+'/'+filename)
                rc=0
"""
assert needle in controller
controller=controller.replace(needle,insert+needle)
ast.parse(controller)
(OUT/'controller.py').write_text(controller,encoding='utf-8')
request=ROOT/'docs/verification/wo-0015-first-board-request-20261015.md'
request_bytes=request.read_bytes()
(OUT/'first-board-request-before.md').write_bytes(request_bytes)
updated=request_bytes.decode('utf-8').replace(OLD,NEW)
updated=updated.replace('状态：离线准备，尚未批准或执行本申请。','状态：2026-10-15 队长已正式重授权当前 boot；旧 boot 准入熔断记录保留于 `data/results/wo0015-first-board-20261015/`。')
updated+='\n## 2026-10-15 retry1 重授权配置\n\n队长正式批准将 boot 绑定更新为 `'+NEW+'`。原 ZIP 保持原摘要，解压后仅应用隔离的 `board_preflight.py` 授权常量、`run-manifest.json` boot 字段及由此派生的摘要更新；功能代码、工件、Goldens、打包逻辑、Guard 与判定标准不变。原文以上历史命令的旧 manifest 摘要仅供追溯，本次使用以下新绑定：\n\n- 新部署目录：`/home/xilinx/wo0015_first_board_retry1_20261015`。\n- 新 manifest SHA-256：`'+sha(manifest)+'`。\n- 配置变更逐项证据：`data/results/wo0015-first-board-retry1-20261015/authorization-rebind.json`。\n- 三阶段命令、一次只读初态快照与逐步 PASS 审核均记录在本次结果目录；不得对旧命令直接使用旧 manifest 摘要执行。\n'
request.write_text(updated,encoding='utf-8')
for label,path in [('main',Path(r'C:\fpga\material-counter')),('worktree',ROOT)]:
    (OUT/(label+'-git-before.txt')).write_text(subprocess.check_output(['git','-C',str(path),'status','--porcelain=v1'],text=True),encoding='utf-8')
print(json.dumps({'manifest_sha256':sha(manifest),'changed_files':changed},indent=2))
