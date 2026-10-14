"""Freeze tested source identity and the exact old no-LENGTH failure evidence."""
import ast
import base64
import difflib
import hashlib
import json
import subprocess
import sys
import zlib
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
CODE=ROOT/'scripts/wo0014_hw/diagnostic50'
OLD=ROOT/'data/results/wo0014-diagnostic50-recovery-20261015'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
tests=json.loads((OUT/'preflight.log').read_text())
assert tests['status']=='OFFLINE_STAGE_READY'
assert all(row['exit_code']==0 for row in tests['test_suites'])
old=json.loads((OLD/'deployment-config.json').read_text())['new_config']
old['remote_evidence_directory']='/home/xilinx/wo0014_recovery50_dma_once_20261015'
new=json.loads(json.dumps(old))
new['executor_directory']='/home/xilinx/wo0014_diagnostic50_pg021_20261015'
new['remote_evidence_directory']='/home/xilinx/wo0014_pg021_pre_20261015'
new['manifest_sha']=sha(CODE/'executor-manifest.json')
changed={}
for name in ('core.py','executor-manifest.json'):
    path=CODE/name
    changed[name]=base64.b64encode(zlib.compress(path.read_bytes())).decode()
    new['expected_files'][name]={'bytes':path.stat().st_size,'sha256':sha(path)}
deployment={'old_config':old,'new_config':new,'changed_files':changed,'changed_encoding':'zlib-base64'}
(OUT/'deployment-config.json').write_text(json.dumps(deployment,indent=2))
previous=json.loads((OLD/'dma-failure-snapshot.json').read_text())
assert previous['executor_status']['child_result']['send_attempts']==0
(OUT/'previous-failure.json').write_text(json.dumps(previous,indent=2))
reference={'guard':{name:{'sha256':entry['sha256']} for name,entry in previous['guard'].items()},
           'hardware_process_records':{'6311':{k:previous['hardware_process_records']['6311'][k] for k in ('stat','cmdline')}}}
(OUT/'previous-failure-reference.json').write_text(json.dumps(reference,indent=2))
diff=''.join(difflib.unified_diff((OUT/'core-before.py').read_text().splitlines(True),
    (CODE/'core.py').read_text().splitlines(True),fromfile='core-before.py',tofile='core.py'))
(OUT/'core.diff').write_text(diff)
source_before={p.name:sha(p) for p in CODE.glob('*.py')}
reviewed={'tests_pass':True,'precheck':tests,'source_sha256':source_before,
          'manifest_sha256':new['manifest_sha']}
(OUT/'reviewed-local.json').write_text(json.dumps(reviewed,indent=2))
for p in OUT.glob('*.py'):ast.parse(p.read_text(encoding='utf-8'),filename=str(p))
baseline={k:previous[k] for k in ('boot_id','kernel_lines')};baseline['kernel_lines']=baseline['kernel_lines'][-8:]
program='deployment='+repr(deployment)+'\nprevious_os='+repr(baseline)+'\nsnapshot_source='+repr((OUT/'snapshot_source.py').read_text())+'\nprevious_failure='+repr(reference)+'\n'+(OUT/'remote_support.py').read_text()+'\n'+(OUT/'remote_cleanup.py').read_text()
encoded=base64.b64encode(zlib.compress(program.encode())).decode()
assert len(encoded)<28000,'Native command too long'
print(json.dumps({'tests_pass':True,'manifest_sha':new['manifest_sha'],'cleanup_b64_chars':len(encoded)}))
