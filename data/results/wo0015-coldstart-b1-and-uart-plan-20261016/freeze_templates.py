"""Freeze review-only text from tested core. Does not execute the draft."""
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

def render(core):
    return '''# NOT_APPROVED_DO_NOT_EXECUTE -- source-review text only, no SSH launcher.
import os
if os.geteuid() == 0:
    raise SystemExit(77)
EXECUTION_STATUS = "NOT_APPROVED_DO_NOT_EXECUTE"
if EXECUTION_STATUS != "SEPARATELY_REVIEWED_AND_APPROVED":
    raise SystemExit(78)
# BEGIN TESTED CORE
''' + core + '''
# END TESTED CORE
import json
result = collect_b1_coldstart(os, mode="coldstart_register_candidate",
                              retired_boot_ids=RETIRED_BOOT_IDS)
print(json.dumps(result, sort_keys=True))
raise SystemExit(0 if result["valid"] else 1)
'''

if __name__ == '__main__':
    core = (HERE / 'coldstart_b1.py').read_text(encoding='utf-8')
    draft = render(core)
    ast.parse(draft)
    (HERE / 'frozen-b1-coldstart.NOT_APPROVED.txt').write_text(draft, encoding='utf-8')
    (HERE / 'frozen-template-metadata.json').write_text(json.dumps({
        'status':'NOT_APPROVED_DO_NOT_EXECUTE', 'draft_executed':False,
        'core_sha256':hashlib.sha256((HERE/'coldstart_b1.py').read_bytes()).hexdigest(),
        'draft_sha256':hashlib.sha256((HERE/'frozen-b1-coldstart.NOT_APPROVED.txt').read_bytes()).hexdigest(),
        'baseline_promotion':False, 'b2_chaining':False},indent=2)+'\n',encoding='utf-8')
