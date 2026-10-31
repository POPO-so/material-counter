set -euo pipefail
python3 - <<'PY'
import hashlib, json, tarfile
from pathlib import Path
root=Path('/tmp/material-perf-v8')
archive=Path('/tmp/v8-source-modules-e2fb595.tar.gz')
paths=['scripts/rle/protocol.py','scripts/standalone/crossing_snapshots.py']
result={'schema':'material-counter.cpu-copy-newline-normalization.v1','source_commit':'e2fb5952bfc4f36c3eacd53a8bdb6bdeec6c4fe2','files':[]}
with tarfile.open(archive,'r:gz') as bundle:
    for name in paths:
        cpu=(root/name).read_bytes()
        git=bundle.extractfile(name).read()
        normalized=cpu.replace(b'\r\n',b'\n')
        result['files'].append({'path':name,'cpu_copy_sha256':hashlib.sha256(cpu).hexdigest(),'git_blob_sha256':hashlib.sha256(git).hexdigest(),'crlf_count':cpu.count(b'\r\n'),'lf_count_including_crlf':cpu.count(b'\n'),'normalization_matches':normalized==git})
result['all_normalization_matches']=all(item['normalization_matches'] for item in result['files'])
Path('/tmp/perf-v8-crlf-normalization.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,sort_keys=True))
PY
