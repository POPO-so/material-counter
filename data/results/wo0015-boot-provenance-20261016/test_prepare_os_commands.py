import ast
import base64
import hashlib
import importlib.util
from pathlib import Path
import shlex

import pytest

spec = importlib.util.spec_from_file_location("prepare_os_commands", Path(__file__).with_name("prepare_os_commands.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.mark.parametrize("stage", ["os-metadata", "boot-bin"])
def test_generated_command_pins_source_no_connection_or_execution(stage):
    cmd = m.make_command(stage, "b" * 32)
    assert shlex.split(cmd["remote_shell_command"]) == cmd["argv"]
    assert cmd["source_sha256"] == hashlib.sha256(m.SOURCE.read_bytes()).hexdigest()
    assert all(flag in cmd["argv"] for flag in ("-I", "-S", "-B", "-u"))
    assert cmd["argv"][0:3] == ["/usr/bin/sudo", "-n", "--"]
    assert cmd["source_sha256"] in cmd["argv"][-1]
    tree = ast.parse(cmd["argv"][-1])
    data_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "b64decode"]
    assert len(data_calls) == 1
    assert base64.b64decode(ast.literal_eval(data_calls[0].args[0]), validate=True) == m.SOURCE.read_bytes()
    assert "socket" not in cmd["argv"][-1] and "paramiko" not in cmd["argv"][-1]


@pytest.mark.parametrize("stage,nonce", [("dma", "a" * 32), ("boot-bin", "';shutdown now;#"), ("os-metadata", "a" * 31)])
def test_reject_command_scope_or_injection(stage, nonce):
    with pytest.raises(ValueError): m.make_command(stage, nonce)
