set -euo pipefail
python3 - <<'PY'
import hashlib, json, subprocess
from pathlib import Path
base = Path('/opt/material-counter/releases/standalone-20261031-v7-count-5272587')
target = Path('/opt/material-counter/releases/standalone-20261031-v8-perf-e2fb595')
def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()
if not base.is_dir():
    raise SystemExit('old v7 package path is absent')
index_path = base / 'deployment-index.json'
source_path = base / 'package-source.json'
index_sha = sha(index_path)
index = json.loads(index_path.read_text(encoding='utf-8'))
source = json.loads(source_path.read_text(encoding='utf-8'))
modules = source.get('module_files', [])
static_names = ['native/rle_components_v2.so', 'export/g2-integrated.bit', 'export/g2-integrated.hwh', 'fonts/DroidSansFallbackFull.ttf', 'fonts/DejaVuSans.ttf']
static = {name: sha(base/name) for name in static_names}
runtime = sorted((name, meta['sha256']) for name, meta in index['files'].items() if name.startswith('runtime/'))
preflight = {
  'old_package': str(base), 'target': str(target), 'target_exists': target.exists() or target.is_symlink(),
  'deployment_index_sha256': index_sha, 'indexed_file_count': len(index.get('files', {})),
  'package_source': {k: source.get(k) for k in ('schema','status','source_commit','source_branch')},
  'module_count': len(modules), 'module_files': modules,
  'static_file_sha256': static, 'runtime_files': runtime,
  'env_candidate_files': [],
}
for root in (base, base.parent, base.parent.parent):
    for path in sorted(root.glob('package.env.candidate')):
        if path.is_file():
            raw = path.read_text(encoding='utf-8')
            values = {line.split('=',1)[0]:line.split('=',1)[1] for line in raw.splitlines() if '=' in line and not line.lstrip().startswith('#')}
            preflight['env_candidate_files'].append({'path':str(path),'sha256':sha(path),'keys':sorted(values),'values':values})
check = subprocess.run(['/usr/local/share/pynq-venv/bin/python3','-B','-m','scripts.standalone.delivery','--root',str(base),'--index-sha256',index_sha],cwd=base,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
preflight['integrity_check'] = json.loads(check.stdout)
Path('/tmp/perf-v7-package-preflight.json').write_text(json.dumps(preflight,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(preflight,ensure_ascii=False,sort_keys=True))
PY
