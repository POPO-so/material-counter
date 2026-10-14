"""Run the exact packaged entry points offline before native dispatch."""
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
OUT = Path(__file__).resolve().parent
cfg = json.loads((OUT/'package-config.json').read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(OUT/'wo0014-closeout-20261015.zip') == cfg['zip_sha256']
preview = OUT/'deploy-preview'
preview.mkdir()
with zipfile.ZipFile(OUT/'wo0014-closeout-20261015.zip') as archive:
    assert set(archive.namelist()) == set(cfg['package_files'])
    for name,digest in cfg['package_files'].items():
        p = preview/name
        assert p.resolve().is_relative_to(preview.resolve())
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(archive.read(name))
        assert sha(p) == digest
results = []
for mode in ('multi50','overlay100','single100','multi100'):
    done = subprocess.run([sys.executable,str(preview/'closeout/closeout_session.py'),
                           '--mode',mode,'--manifest-sha256',cfg['manifest_sha256']],
                          capture_output=True,text=True,timeout=20)
    results.append({'mode':mode,'exit_code':done.returncode,'stdout':done.stdout,'stderr':done.stderr})
    assert done.returncode == 0, results[-1]
    row = json.loads(done.stdout)
    assert row['hardware_attempted'] is False and row['status'] == 'OFFLINE_READY'
(OUT/'packaged-offline-validation.json').write_text(json.dumps(results,indent=2)+'\n')
print('EXACT_PACKAGE_OFFLINE_ENTRIES_PASS=4; hardware_attempted=false')
