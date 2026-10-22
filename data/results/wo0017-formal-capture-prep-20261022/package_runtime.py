"""Package the exact reviewed runtime into a new, isolated board deployment."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

repo = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo / 'scripts/wo0016'))
import run_stream_counter as runner

out = Path(__file__).resolve().parent
sources = runner.formal_runtime_sha256()
with zipfile.ZipFile(out / 'formal-b1-runtime.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for name, digest in sources.items():
        path = (repo / 'scripts/wo0016' / name if not name.startswith('wo0015/')
                else repo / 'scripts' / name)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        archive.write(path, 'app/' + name)
    for name in ('prepare_b1.py', 'launch_b1.py'):
        archive.write(out / name, name)
    archive.writestr('runtime-sha256.json', json.dumps(sources, indent=2) + '\n')
(out / 'runtime-sha256.json').write_text(json.dumps(sources, indent=2) + '\n', encoding='utf-8')
print('packaged', len(sources), 'runtime files; no BIT/HWH/H3 included')
