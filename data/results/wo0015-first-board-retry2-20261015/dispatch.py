"""Submit exactly one manager-selected transport request; never advance stages."""
import json,sys
from pathlib import Path
OUT=Path(__file__).resolve().parent
number=int(sys.argv[1])
operation=sys.argv[2]
templates=json.loads((OUT/'command-templates.json').read_text())
task=templates[operation]
assert json.loads((OUT/'initial-snapshot.json').read_text())['boot_id']=='822a9c29-37c0-438c-81c4-da998d57073b'
if operation=='dma-single':
    assert json.loads((OUT/'step1-acceptance.json').read_text())['status']=='PASS'
if operation=='dma-three':
    assert json.loads((OUT/'step2-acceptance.json').read_text())['status']=='PASS'
with (OUT/f'request-{number:03d}.json').open('x',encoding='utf-8') as f:json.dump(task,f)
print(f'SUBMITTED_ONCE {number:03d} {operation}')
