"""Regression: a transport collector must preserve raw Guard bytes."""
import ast
import os
from pathlib import Path

def test_initial_collector_preserves_terminal_bytes(tmp_path):
    path=Path(__file__).with_name(os.environ.get('WO15_COLLECTOR_TEST','controller.py'))
    outer=ast.parse(path.read_text(encoding='utf-8'))
    source=next(n.value.value for n in ast.walk(outer) if isinstance(n,ast.Assign)
                and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str)
                and n.value.value.startswith('import json,os,subprocess'))
    inner=ast.parse(source)
    guard_expr=next(v for n in ast.walk(inner) if isinstance(n,ast.Dict)
                    for k,v in zip(n.keys,n.values)
                    if isinstance(k,ast.Constant) and k.value=='guard_files')
    raw=b'{"status":"TERMINAL_FAILURE"}\n'
    guard=tmp_path/'terminal-failure.json';guard.write_bytes(raw)
    # Evaluate only the actual collector expression, with the board directory
    # redirected to a temporary fixture; no subprocess or board calls occur.
    captured=eval(compile(ast.Expression(guard_expr),'<guard-collector>','eval'),
                  {'Path':lambda ignored:tmp_path,'read':lambda p:p.read_text().strip()})
    assert captured[str(guard)].encode('utf-8')==raw
