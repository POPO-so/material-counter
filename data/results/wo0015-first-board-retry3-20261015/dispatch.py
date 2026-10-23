"""Submit only the manager-selected operation, once; no automatic stage advancement."""
import json,sys
from pathlib import Path
OUT=Path(__file__).resolve().parent
number=int(sys.argv[1]);op=sys.argv[2]
assert json.loads((OUT/'initial-snapshot.json').read_text())['boot_id']=='822a9c29-37c0-438c-81c4-da998d57073b'
assert int((OUT/'full-offline-final-exit-code.txt').read_text())==0
templates=json.loads((OUT/'command-templates.json').read_text())
if op=='dma-single':assert json.loads((OUT/'step1-acceptance.json').read_text())['status']=='PASS'
if op=='dma-three':assert json.loads((OUT/'step2-acceptance.json').read_text())['status']=='PASS'
with (OUT/f'request-{number:03d}.json').open('x',encoding='utf-8') as f:json.dump(templates[op],f)
print('SUBMITTED_ONCE',number,op)
