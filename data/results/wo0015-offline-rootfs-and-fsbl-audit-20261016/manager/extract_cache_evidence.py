"""Extract source text/AST only. Never imports PYNQ or executes candidate code."""
import ast
import hashlib
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parents[1]
NAMES = {'allocate', '__new__', '__init__', 'flush', 'invalidate', 'sync_to_device',
         'sync_from_device', 'sync_for_cpu', 'sync_for_device', '_xrt_allocate',
         'xclSyncBO', 'xclAllocBO', 'cacheable', 'physical_address'}

def extract(path):
    raw = path.read_bytes()
    source = raw.decode('utf-8')
    tree = ast.parse(source)
    lines = source.splitlines()
    def walk(node, prefix=''):
        found = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                full = prefix + child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name in NAMES:
                    found.append({'qualified_name': full, 'line': child.lineno, 'end_line': child.end_lineno,
                                  'source': '\n'.join(lines[child.lineno-1:child.end_lineno])})
                found.extend(walk(child, full + '.'))
            else:
                found.extend(walk(child, prefix))
        return found
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'functions': walk(tree)}

if __name__ == '__main__':
    files = [Path(p) for p in sys.argv[1:]]
    assert files
    assert all(p.is_file() and 'restricted-raw' not in p.parts for p in files)
    (OUT / 'manager/cache-source-evidence.json').write_text(json.dumps([extract(p) for p in files], indent=2) + '\n', encoding='utf-8')
