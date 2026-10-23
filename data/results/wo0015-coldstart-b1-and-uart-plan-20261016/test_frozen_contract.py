import ast
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent

def test_frozen_matches_tested_core_and_hashes():
    text=(HERE/'frozen-b1-coldstart.NOT_APPROVED.txt').read_text(encoding='utf-8')
    core=(HERE/'coldstart_b1.py').read_text(encoding='utf-8')
    assert text.split('# BEGIN TESTED CORE\n',1)[1].split('\n# END TESTED CORE',1)[0]==core
    meta=json.loads((HERE/'frozen-template-metadata.json').read_bytes())
    assert meta['core_sha256']==hashlib.sha256((HERE/'coldstart_b1.py').read_bytes()).hexdigest()
    assert meta['draft_sha256']==hashlib.sha256((HERE/'frozen-b1-coldstart.NOT_APPROVED.txt').read_bytes()).hexdigest()

def test_root_first_then_unapproved_exit_static_only():
    tree=ast.parse((HERE/'frozen-b1-coldstart.NOT_APPROVED.txt').read_text(encoding='utf-8'))
    assert ast.unparse(tree.body[0])=='import os'
    assert ast.unparse(tree.body[1].test)=='os.geteuid() == 0'
    assert ast.unparse(tree.body[1].body[0])=='raise SystemExit(77)'
    assert 'NOT_APPROVED_DO_NOT_EXECUTE' in ast.unparse(tree.body[2])
    assert ast.unparse(tree.body[3].body[0])=='raise SystemExit(78)'
    assert not any(isinstance(n,ast.Call) and 'b2' in ast.unparse(n.func).lower() for n in ast.walk(tree))

def test_capture_core_no_real_os_or_transport_import():
    tree=ast.parse((HERE/'coldstart_b1.py').read_text(encoding='utf-8'))
    imports=[alias.name for n in ast.walk(tree) if isinstance(n,ast.Import) for alias in n.names]
    assert set(imports)=={'re','stat'}
    func=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='collect_b1_coldstart')
    assert isinstance(func.body[1],ast.Try)
    assert ast.unparse(func.body[1].body[0].test)=='os.geteuid() == 0'

def test_uart_flow_control_contract_and_no_device_imports():
    import uart_readonly
    cfg=uart_readonly.CONFIG
    assert (cfg.rtscts,cfg.xonxoff,cfg.dsrdtr)==(False,False,False)
    assert (cfg.baudrate,cfg.bytesize,cfg.parity,cfg.stopbits)==(115200,8,'N',1)
    tree=ast.parse((HERE/'uart_readonly.py').read_text(encoding='utf-8'))
    imports={alias.name for n in ast.walk(tree) if isinstance(n,ast.Import) for alias in n.names}
    assert not (imports & {'serial','socket','subprocess','os'})
