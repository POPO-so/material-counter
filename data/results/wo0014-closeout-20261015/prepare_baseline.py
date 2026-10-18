"""Explicit, reviewed baseline staging; no broad add or cleanup."""
import hashlib
import json
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
milestone = ROOT / 'data/results/wo0014-pg021-ready-20261015'
expected = json.loads((milestone / 'evidence-sha256.json').read_text())
for name, digest in expected.items():
    assert sha(milestone / name) == digest, name
protected = json.loads((milestone / 'protected-after.json').read_text())
for name, digest in protected.items():
    assert sha(ROOT / name) == digest, name
paths = [p for p in (ROOT / 'scripts/wo0014_hw/diagnostic50').iterdir() if p.is_file()]
paths += [ROOT / 'scripts/wo0014_hw' / n for n in ('pynq_image_once.py','test_pynq_image_once.py')]
paths += [p for p in milestone.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')]
fresh = ROOT / 'data/results/wo0014-diagnostic50-hardening-20261014/run-20261015-031611-696569'
assert json.loads((fresh / 'precheck.json').read_text())['status'] == 'OFFLINE_STAGE_READY'
paths += [p for p in fresh.iterdir() if p.is_file()]
assert all(p.stat().st_size < 2_000_000 for p in paths)
relative = sorted({p.relative_to(ROOT).as_posix() for p in paths})
assert not subprocess.check_output(['git','diff','--cached','--name-only'],cwd=ROOT).strip()
(OUT / 'baseline-staged-files.json').write_text(json.dumps({n:sha(ROOT/n) for n in relative},indent=2)+'\n')
(OUT / 'protected-before.json').write_text(json.dumps(protected,indent=2)+'\n')
subprocess.run(['git','add','--',*relative],cwd=ROOT,check=True)
subprocess.run(['git','diff','--cached','--check'],cwd=ROOT,check=True)
actual = subprocess.check_output(['git','diff','--cached','--name-only'],cwd=ROOT,text=True).splitlines()
assert sorted(actual) == relative
print(json.dumps({'staged_files':len(relative),'bytes':sum((ROOT/n).stat().st_size for n in relative),'evidence_hashes_verified':len(expected),'protected_verified':True},indent=2))
