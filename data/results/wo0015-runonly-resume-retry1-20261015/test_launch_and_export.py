import ast
import base64
import hashlib
import json
from pathlib import Path
import runpy
import shlex
import sys
import pytest

HERE=Path(__file__).resolve().parent

def test_guard_hash_is_computed_from_raw_bytes_on_collection(tmp_path):
    outer=ast.parse((HERE/'controller.py').read_text())
    source=next(n.value.value for n in ast.walk(outer) if isinstance(n,ast.Assign)
                and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str)
                and n.value.value.startswith('import json,os,subprocess'))
    tree=ast.parse(source)
    expr=next(v for n in ast.walk(tree) if isinstance(n,ast.Dict) for k,v in zip(n.keys,n.values)
              if isinstance(k,ast.Constant) and k.value=='guard_sha256')
    raw=b'{"status":"TERMINAL_FAILURE"}\r\n\n'
    (tmp_path/'terminal.json').write_bytes(raw)
    result=eval(compile(ast.Expression(expr),'<raw-guard-sha>','eval'),{'Path':lambda p:tmp_path,'hashlib':hashlib})
    assert result=={'terminal.json':hashlib.sha256(raw).hexdigest()}

@pytest.mark.parametrize('tampered',[False,True])
def test_actual_launch_checks_all_bytes_before_running_adapter(tmp_path,monkeypatch,tampered):
    template=json.loads((HERE/'command-templates.json').read_text())['run-only']['command']
    program=shlex.split(template)[-1]
    tree=ast.parse(program)
    encoded=next(n.args[0].value for n in ast.walk(tree) if isinstance(n,ast.Call)
                 and isinstance(n.func,ast.Attribute) and n.func.attr=='b64decode')
    source=base64.b64decode(encoded).decode()
    source=source.replace("REMOTE='/home/xilinx/wo0015_runonly_resume_retry1_20261015'",'REMOTE='+repr(str(tmp_path)),1)
    for name in json.loads((HERE/'launch-lock.json').read_text()):
        (tmp_path/name).write_bytes((HERE/name).read_bytes())
    if tampered:(tmp_path/'run_only_model.py').write_bytes(b'tampered')
    calls=[]
    monkeypatch.setattr(runpy,'run_path',lambda *a,**kw:calls.append((a,kw)))
    monkeypatch.setattr(sys,'path',sys.path.copy());monkeypatch.setattr(sys,'argv',sys.argv.copy())
    if tampered:
        with pytest.raises(AssertionError):exec(source,{})
        assert calls==[]
    else:
        exec(source,{})
        assert len(calls)==1 and calls[0][0][0]==str(tmp_path/'recovery_adapter.py')

def test_sudo_export_preserves_binary_bytes_without_user_file_permissions(tmp_path):
    # Actual collector result_files expression, excluding all board/OS operations.
    tree=ast.parse((HERE/'collect_exit.py').read_text())
    expr=next(n.value for n in ast.walk(tree) if isinstance(n,ast.Assign)
              and any(isinstance(t,ast.Subscript) and isinstance(t.slice,ast.Constant)
                      and t.slice.value=='result_files' for t in n.targets))
    raw=b'\x00\xff\r\n\n';(tmp_path/'evidence.bin').write_bytes(raw)
    exported=eval(compile(ast.Expression(expr),'<root-read-export>','eval'),
                  {'OUT':tmp_path,'sha':lambda b:hashlib.sha256(b).hexdigest(),'base64':base64})
    item=exported['evidence.bin']
    assert base64.b64decode(item['base64'])==raw and item['sha256']==hashlib.sha256(raw).hexdigest()
