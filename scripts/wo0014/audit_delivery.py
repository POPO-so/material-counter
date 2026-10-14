"""Offline delivery audit; never connects to hardware or changes Git state."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / 'data/results/wo0014-offline-20261014'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

before = json.loads((RESULT / 'protected-before.json').read_text(encoding='utf-8-sig'))
after = {name: sha(ROOT / name) for name in before}
assert after == before, 'Protected document or production RTL changed'
(RESULT / 'protected-after.json').write_text(json.dumps(after, indent=2), encoding='utf-8')
groups = ['fpga/experimental/wo0014', 'fpga/sim/wo0014',
          'python/refmodel/experiments/wo0014', 'scripts/wo0014']
sources = [p for group in groups for p in (ROOT / group).rglob('*')
           if p.is_file() and '__pycache__' not in p.parts]
sources += [ROOT / 'docs/work-orders/wo-0014-offline-image-integration.md',
            ROOT / 'docs/verification/wo-0014-offline-integration-20261014.md']
hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(sources)}
(RESULT / 'source-hashes.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
(RESULT / 'changed-files.txt').write_text('\n'.join(hashes) + '\n', encoding='utf-8')
for args, name in [(['status', '--short'], 'git-final.txt'),
                   (['diff', '--check'], 'git-diff-check-final.txt'),
                   (['diff', '--cached', '--name-only'], 'git-staged-final.txt')]:
    run = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, check=True)
    (RESULT / name).write_bytes(run.stdout + run.stderr)
    if args[0] == 'diff':
        assert not run.stdout, 'Unexpected diff/check or staged content'
commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
assert commit == '47103338d2f59177af0c6570242aff953baf07b7'
assert subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT).decode().strip() == 'feat/pl-stream-skeleton'
artifact_hashes = {}
for name, expected in {
    'wo0013_phase_a.bit': '47e6cd65c552244236f2564c11cba3e81a9a0c6aced0c9974c04de78256da3f7',
    'wo0013_phase_a.hwh': 'a31d71f8e7004de710fa4da07078b80e2a0a8b40ff52ac523d322f21362df1fb',
}.items():
    path = 'data/results/wo0013-resetfix-20261014/export/' + name
    blob = subprocess.check_output(['git', 'show', f'HEAD:{path}'], cwd=ROOT)
    artifact_hashes[name] = hashlib.sha256(blob).hexdigest()
    assert artifact_hashes[name] == expected == sha(ROOT / path)
bad_whitespace = []
for p in sources:
    for line_number, line in enumerate(p.read_text(encoding='utf-8-sig').splitlines(), 1):
        if line.rstrip(' \t') != line:
            bad_whitespace.append(f'{p.relative_to(ROOT)}:{line_number}')
assert not bad_whitespace, bad_whitespace
summary = {'protected_and_production_unchanged': True, 'wo0013_commit': commit,
           'wo0014_source_files': len(sources), 'staged_files': 0,
           'diff_check': 'PASS', 'new_text_whitespace': 'PASS',
           'committed_bit_hwh_raw_sha256': artifact_hashes}
(RESULT / 'delivery-audit.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2))
