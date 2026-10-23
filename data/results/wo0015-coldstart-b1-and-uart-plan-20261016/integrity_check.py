"""Explicit-manifest host-only integrity checks. Never walks historical trees."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
SEALED = ROOT / 'data/results/wo0015-offline-rootfs-and-fsbl-audit-20261016'

def sha(p):
    p = Path(p)
    if any(x.lower() == 'restricted-raw' or x.upper().startswith('REVISION') for x in p.parts):
        raise ValueError('restricted path denied')
    if p.is_symlink():
        raise ValueError('symlink denied')
    with p.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def member(base, name):
    p = base / name
    if p.is_absolute() and not p.resolve().is_relative_to(base.resolve()):
        raise ValueError('outside manifest root')
    return p

def snapshot():
    previous = json.loads((SEALED / 'manager/integrity-after.json').read_bytes())
    sealed_index = json.loads((SEALED / 'evidence-sha256.json').read_bytes())
    assert len(sealed_index) == 364
    for n, h in sealed_index.items():
        assert sha(member(SEALED, n)) == h, n
    indexed = {}
    for name, record in previous['indexed'].items():
        base = ROOT / 'data/results' / name
        assert sha(base / 'evidence-sha256.json') == record['manifest_sha256']
        index = json.loads((base / 'evidence-sha256.json').read_bytes())
        assert len(index) == record['files']
        for n, h in index.items():
            assert sha(member(base, n)) == h, (name, n)
        indexed[name] = record
    assert sum(r['files'] for r in indexed.values()) == 320
    protected = previous['protected_nonrestricted_hashes']
    assert len(protected) == 389
    for n, h in protected.items():
        assert sha(member(ROOT, n)) == h, n
    for n, h in previous['hardware_locks'].items():
        assert sha(ROOT / 'data/results/wo0015-hw-50mhz-20261015/export' / n) == h
    git = {}
    for name, path in [('main', Path('C:/fpga/material-counter')), ('worktree', ROOT)]:
        git[name] = {label: hashlib.sha256(subprocess.check_output(
            ['git', '-C', str(path), *args])).hexdigest()
            for label, args in [('head', ['rev-parse', 'HEAD']),
                                ('tracked_diff', ['diff', '--binary', '--no-ext-diff']),
                                ('staged_diff', ['diff', '--cached', '--binary', '--no-ext-diff'])]}
    return {'sealed_files_verified':364, 'sealed_manifest_sha256':sha(SEALED / 'evidence-sha256.json'),
            'indexed_files_verified':320, 'protected_files_verified':389,
            'hardware_locks':previous['hardware_locks'], 'git':git,
            'restricted_raw':'NOT_TRAVERSED_NOT_READ_NOT_COPIED', 'revision':'NOT_READ',
            'large_image_read':False, 'fsbl_reparsed':False}

if __name__ == '__main__':
    mode = sys.argv[1]
    assert mode in ('before', 'after')
    result = snapshot()
    if mode == 'after':
        assert result == json.loads((OUT / 'integrity-before.json').read_bytes())
    (OUT / ('integrity-' + mode + '.json')).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'mode':mode, 'result':'PASS', **result}))
