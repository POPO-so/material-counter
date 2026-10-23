"""Generate review text only by AST-reading literal wrappers; no exec or OS adapter."""
import ast
import hashlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1]
P = OUT / 'proposals'
source = (P / 'remote_template_v2.py').read_text(encoding='utf-8')
tree = ast.parse((P / 'test_proposal_contract_v2.py').read_text(encoding='utf-8'))
wrappers = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in ('FROZEN_B1_WRAPPER', 'FROZEN_B2_WRAPPER')}
index = []
for stage in ('b1', 'b2'):
    name = f'frozen-remote-{stage}.NOT_APPROVED.txt'
    raw = (source + wrappers[f'FROZEN_{stage.upper()}_WRAPPER']).encode('utf-8')
    ast.parse(raw.decode('utf-8'))
    (P / name).write_bytes(raw)
    index.append(hashlib.sha256(raw).hexdigest() + '  ' + name)
(P / 'remote-frozen-sha256.txt').write_text('\n'.join(index) + '\n', encoding='ascii')
old_sha = (P / 'remote-template-sha256.txt').read_text(encoding='ascii').strip()
new_sha = hashlib.sha256((P / 'remote_template_v2.py').read_bytes()).hexdigest()
(P / 'remote-template-sha256.txt').write_text(new_sha + '\n', encoding='ascii')
findings = (P / 'findings.md').read_text(encoding='utf-8').replace(old_sha, new_sha)
if 'Manager transport review' not in findings:
    findings += '\n## Manager transport review\n\nThe fixed candidate is `/usr/bin/python3 -I -S -B` (target identity/presence UNVERIFIED). `ControlPath=none` prevents reuse of a configured control socket; `ForwardAgent=no` and `ForwardX11=no` explicitly cover channels not disabled by ClearAllForwardings. No transport has been executed. The additional assertions were red before constants/documents were corrected; logs are in `../manager/transport-review-red.txt` and the final new-only suite.\n'
(P / 'findings.md').write_text(findings, encoding='utf-8')
print('Refreshed unapproved text payloads and hashes; no payload executed.')
