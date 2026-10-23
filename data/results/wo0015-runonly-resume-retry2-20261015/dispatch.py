"""Submit one pinned operation; no automatic stage advancement or retry."""
import hashlib
import json
from pathlib import Path
import sys

OUT=Path(__file__).resolve().parent
OP=sys.argv[1]
ORDER={'deploy':1,'transition':2,'dma-single':3,'dma-three':4,'exit-pass':5}
state=json.loads((OUT/'status.json').read_bytes())
assert state['status'] in ('READONLY_BOOT_GATE_PASS_AWAITING_MANAGER','REQUEST_DONE_AWAITING_MANAGER'),state
assert int((OUT/'full-offline-final-exit-code.txt').read_text())==0
assert int((OUT/'offline-continuation-exit-code.txt').read_text())==0
for n,h in json.loads((OUT/'launch-lock.json').read_bytes()).items():
    assert hashlib.sha256((OUT/n).read_bytes()).hexdigest()==h
if OP!='deploy':
    assert json.loads((OUT/f'request-{ORDER[OP]-1:03d}-done.json').read_bytes())['exit_code']==0
for name in {'dma-single':['step1-acceptance.json'],'dma-three':['step2-acceptance.json'],
             'exit-pass':['step3-acceptance.json']}.get(OP,[]):
    assert json.loads((OUT/name).read_bytes())['status']=='PASS'
template=json.loads((OUT/'command-templates.json').read_bytes())[OP]
with (OUT/f'request-{ORDER[OP]:03d}.json').open('x',encoding='utf-8') as f:json.dump(template,f)
print('DISPATCHED_ONCE',OP)
