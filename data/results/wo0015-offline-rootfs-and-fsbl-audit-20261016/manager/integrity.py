"""Read-only integrity snapshot; never traverses or opens restricted evidence."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

OUT = Path(__file__).resolve().parents[1]
ROOT = OUT.parents[2]
HISTORY = ['wo0015-afi1-preconditions-20261015', 'wo0015-afi1-readonly-20261015',
           'wo0015-step2-mismatch-audit-20261015', 'wo0015-runonly-resume-retry2-20261015',
           'wo0015-first-board-retry3-20261015']
PROTECTED = HISTORY + ['wo0015-boot-provenance-20261016']

def sha(path):
    if 'restricted-raw' in path.parts or path.name.upper().startswith('REVISION'):
        raise ValueError('restricted path denied')
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def safe_files(folder):
    # Explicit descent prevents even traversing restricted-raw.
    for child in sorted(folder.iterdir()):
        if child.name in ('restricted-raw', '__pycache__') or child.name.upper().startswith('REVISION'):
            continue
        if child.is_symlink():
            raise ValueError(f'Unexpected symlink: {child}')
        if child.is_dir():
            yield from safe_files(child)
        elif child.is_file():
            yield child

def snapshot():
    indexed = {}
    for name in HISTORY:
        folder = ROOT / 'data/results' / name
        index = json.loads((folder / 'evidence-sha256.json').read_bytes())
        bad = [n for n, h in index.items() if sha(folder / n) != h]
        assert not bad, (name, bad)
        indexed[name] = {'files': len(index), 'manifest_sha256': sha(folder / 'evidence-sha256.json')}
    assert sum(x['files'] for x in indexed.values()) == 320
    protected = {str(p.relative_to(ROOT)): sha(p) for name in PROTECTED
                 for p in safe_files(ROOT / 'data/results' / name)}
    locks = {'wo15.bit': 'a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420',
             'wo15.hwh': 'bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6'}
    for n, h in locks.items():
        assert sha(ROOT / 'data/results/wo0015-hw-50mhz-20261015/export' / n) == h
    candidate_boot = sha(ROOT / 'data/results/wo0015-boot-provenance-20261016/extracted/BOOT.BIN')
    assert candidate_boot == '3bf9c24025a489926fd76872754977c5954e2ab25c91957373386e1f20915af6'
    git = {}
    for name, path in [('main', Path('C:/fpga/material-counter')), ('worktree', ROOT)]:
        git[name] = {label: subprocess.check_output(['git', '-C', str(path), *args]).decode('utf-8')
                     for label, args in [('head', ['rev-parse', 'HEAD']),
                                         ('status', ['status', '--porcelain=v1']),
                                         ('tracked_diff', ['diff', '--binary']),
                                         ('staged_diff', ['diff', '--cached', '--binary'])]}
    return {'indexed': indexed, 'protected_nonrestricted_hashes': protected,
            'hardware_locks': locks, 'candidate_boot_sha256': candidate_boot, 'git': git,
            'restricted_raw': 'NOT_TRAVERSED_NOT_READ_NOT_COPIED'}

if __name__ == '__main__':
    mode = sys.argv[1]
    result = snapshot()
    if mode == 'after':
        before = json.loads((OUT / 'manager/integrity-before.json').read_bytes())
        assert result['indexed'] == before['indexed']
        assert result['protected_nonrestricted_hashes'] == before['protected_nonrestricted_hashes']
        assert result['hardware_locks'] == before['hardware_locks']
        assert result['git']['main'] == before['git']['main']
        for key in ('head', 'tracked_diff', 'staged_diff'):
            assert result['git']['worktree'][key] == before['git']['worktree'][key]
        result['comparison'] = 'PASS: indexed320, protected nonrestricted files, locked artifacts, main Git state and worktree HEAD/tracked/staged diffs unchanged'
    (OUT / f'manager/integrity-{mode}.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'mode': mode, 'indexed_files': 320, 'protected_files': len(result['protected_nonrestricted_hashes']), 'comparison': result.get('comparison', 'baseline recorded')}))
