"""Offline checks for the CLI's identity, environment and transport gates."""
import hashlib
import json
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core
import child
import supervisor


def real_plan():
    root = HERE.parents[2]
    export = root / 'data/results/wo0014-hw-50mhz-20261014/export'
    return core.base.validate_inputs(export / 'wo0014_image.bit', export / 'wo0014_image.hwh',
                                     export / 'rgb_packet.bin', export / 'pair-manifest.json')


def test_real_50_identity_and_offline_cli_never_start_network(monkeypatch, capsys):
    plan = real_plan(); core.validate_50(plan)
    monkeypatch.setattr(supervisor, 'verify_bundle', lambda _: 'offline-test-bundle')
    def forbidden(*a, **kw):
        raise AssertionError('Offline CLI attempted subprocess/network')
    monkeypatch.setattr(supervisor.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(supervisor.subprocess, 'run', forbidden)
    args = ['--bit', str(plan.bit_path), '--hwh', str(plan.hwh_path),
            '--packet', str(plan.packet_path), '--manifest', str(plan.manifest_path)]
    assert supervisor.main(args) == 0
    assert json.loads(capsys.readouterr().out)['hardware_attempted'] is False


@pytest.mark.parametrize('mutation', ['code', 'manifest', 'ok'])
def test_reviewed_bundle_hash_gate(tmp_path, mutation):
    records = {}
    for name in core.BUNDLE_FILES:
        data = ('fixture-' + name).encode()
        (tmp_path / name).write_bytes(data)
        records[name] = hashlib.sha256(data).hexdigest()
    path = tmp_path / 'executor-manifest.json'
    path.write_text(json.dumps({'schema': 1, 'files': records}))
    digest = core.base._sha256_file(path)
    if mutation == 'code': (tmp_path / 'core.py').write_text('changed')
    if mutation == 'manifest': path.write_text('{}')
    if mutation == 'ok':
        assert core.verify_bundle(tmp_path, digest) == digest
    else:
        with pytest.raises(core.base.PreflightError):
            core.verify_bundle(tmp_path, digest)


@pytest.mark.parametrize('bad', [False, True])
def test_remote_transfer_hash_gate_is_read_only_and_precedes_child(monkeypatch, tmp_path, bad):
    plan = real_plan()
    files = {name: '1' * 64 for name in core.BUNDLE_FILES}
    path = tmp_path / 'executor-manifest.json'
    path.write_text(json.dumps({'files': files}))
    files.update({'executor-manifest.json': core.base._sha256_file(path),
                  'wo0014_image.bit': plan.bit_sha256, 'wo0014_image.hwh': plan.hwh_sha256,
                  'rgb_packet.bin': plan.packet_sha256, 'pair-manifest.json': plan.manifest_sha256})
    def remote(argv, **kwargs):
        assert argv[0] == 'ssh' and 'sha256sum --' in argv[-1]
        assert 'child.py --execute' not in argv[-1]
        text = ''.join(digest + '  /home/xilinx/isolated/' + name + '\n' for name, digest in files.items())
        if bad: text = text.replace(plan.bit_sha256, '0' * 64)
        return SimpleNamespace(returncode=0, stdout=text, stderr='')
    monkeypatch.setattr(supervisor.subprocess, 'run', remote)
    if bad:
        with pytest.raises(RuntimeError, match='hashes differ'):
            supervisor.verify_remote_files('xilinx@192.168.2.99', '/home/xilinx/isolated', plan, tmp_path, runner=remote)
    else:
        assert supervisor.verify_remote_files('xilinx@192.168.2.99', '/home/xilinx/isolated', plan, tmp_path, runner=remote)['hashes']


def test_signal_guard_marks_transport_lost_without_mmio(monkeypatch):
    handlers = {}
    monkeypatch.setattr(child.signal, 'signal', lambda signum, handler: handlers.update({signum: handler}))
    child.transport_lost.clear()
    child.install_signal_guard()
    for signum, handler in handlers.items():
        child.transport_lost.clear(); handler(signum, None)
        assert child.transport_lost.is_set()
    child.transport_lost.clear()


@pytest.mark.parametrize('kind', ['notebook', 'mapping', 'fd', 'clean'])
def test_process_guard_is_fail_closed(tmp_path, monkeypatch, kind):
    proc = tmp_path / '999998'; proc.mkdir()
    (proc / 'fd').mkdir()
    (proc / 'cmdline').write_bytes(b'ipykernel_launcher' if kind == 'notebook' else b'other')
    (proc / 'maps').write_text('/dev/mem' if kind == 'mapping' else '')
    if kind == 'fd':
        (proc / 'fd/1').write_text('placeholder')
        monkeypatch.setattr(child.os, 'readlink', lambda _: '/dev/mem')
    original = Path
    monkeypatch.setattr(child, 'Path', lambda path: tmp_path if path == '/proc' else original(path))
    if kind == 'clean':
        assert child.process_audit() == {'findings': [], 'denied': []}
    else:
        with pytest.raises(core.base.PreflightError, match='ownership scan failed'):
            child.process_audit()
