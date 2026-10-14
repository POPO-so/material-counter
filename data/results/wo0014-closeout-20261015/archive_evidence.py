"""Commit only this bounded result, excluding deploy copies, ZIP, and caches."""
import hashlib
import json
import subprocess
from pathlib import Path
OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
selected = [p for p in OUT.iterdir() if p.is_file() and p.suffix != '.zip' and p.name != 'evidence-sha256.json']
selected += [p for p in (OUT/'board-evidence').rglob('*') if p.is_file()]
selected += [p for p in (ROOT/'scripts/wo0014_hw/closeout').iterdir() if p.is_file() and p.suffix == '.py']
assert not any('__pycache__' in p.parts or 'deploy-preview' in p.parts for p in selected)
assert all(p.stat().st_size < 1_000_000 for p in selected)
protected = json.loads((OUT/'protected-before.json').read_text())
assert all(sha(ROOT/n) == h for n,h in protected.items())
paths = sorted(p.relative_to(ROOT).as_posix() for p in selected)
manifest = {n:sha(ROOT/n) for n in paths}
(OUT/'evidence-sha256.json').write_text(json.dumps(manifest,indent=2)+'\n')
paths.append((OUT/'evidence-sha256.json').relative_to(ROOT).as_posix())
paths.append('.gitattributes')
subprocess.run(['git','add','--',*paths],cwd=ROOT,check=True)
subprocess.run(['git','diff','--cached','--check'],cwd=ROOT,check=True)
actual = subprocess.check_output(['git','diff','--cached','--name-only'],cwd=ROOT,text=True).splitlines()
assert sorted(actual) == sorted(paths)
for name,digest in manifest.items():
    assert hashlib.sha256(subprocess.check_output(['git','show',':'+name],cwd=ROOT)).hexdigest() == digest, name
print(json.dumps({'staged_files':len(paths),'bytes':sum((ROOT/n).stat().st_size for n in paths),
                  'index_byte_hashes_verified':len(manifest),'protected_unchanged':True,
                  'zip_preview_caches_excluded':True},indent=2))
