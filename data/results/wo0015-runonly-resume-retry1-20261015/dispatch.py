"""Manager submits one fixed operation after inspecting the previous PASS."""
import json
from pathlib import Path
import sys

OUT=Path(__file__).resolve().parent
OP=sys.argv[1]
ORDER={'deploy':1,'run-only':2,'archive':3,'dma-single':4,'dma-three':5,'exit-pass':6}
state=json.loads((OUT/'status.json').read_bytes())
assert state['status'] in ('READONLY_BOOT_GATE_PASS_AWAITING_MANAGER','REQUEST_DONE_AWAITING_MANAGER'),state
assert json.loads((OUT/'initial-snapshot.json').read_bytes())['boot_id']=='822a9c29-37c0-438c-81c4-da998d57073b'
if OP!='deploy':
    previous=ORDER[OP]-1
    assert json.loads((OUT/f'request-{previous:03d}-done.json').read_bytes())['exit_code']==0
for required in {'archive':['run-only-acceptance.json'],'dma-single':['step1-acceptance.json'],
                 'dma-three':['step2-acceptance.json'],'exit-pass':['step3-acceptance.json']}.get(OP,[]):
    assert json.loads((OUT/required).read_bytes())['status']=='PASS'
template=json.loads((OUT/'command-templates.json').read_bytes())[OP]
with (OUT/f'request-{ORDER[OP]:03d}.json').open('x',encoding='utf-8') as f:
    json.dump(template,f)
print('DISPATCHED_ONCE',OP)
