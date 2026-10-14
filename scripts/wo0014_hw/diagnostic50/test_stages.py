"""Offline regressions for independently authorized diagnostic50 phases."""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import child
import core

spec = importlib.util.spec_from_file_location(
    'stage_old_tests', HERE.parent / 'test_pynq_image_once.py')
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)


BOOT = 'mock-boot'


def health(boot=BOOT):
    return {'ping': True, 'ssh': True, 'linux': True, 'boot_id': boot}


def make_rig(tmp_path, mode, *, loaded_hash='from-plan', receipt=True,
             mmio_receipt=False):
    paths = old.make_artifacts(tmp_path)
    plan = core.base.validate_inputs(*paths)
    api = old.FakeApi(plan.packet)
    # The standalone DMA path requires the same IOC evidence as the full path.
    make_mmio = api.MMIO

    def mmio(*args, **kwargs):
        device = make_mmio(*args, **kwargs)
        if device.base == plan.dma_base:
            write = device.write

            def complete(offset, value):
                write(offset, value)
                if offset == 0x28 and device.mode == 'success':
                    device.regs[4] |= 0x1000

            device.write = complete
        return device

    api.MMIO = mmio
    bit_hash = plan.bit_sha256 if loaded_hash == 'from-plan' else loaded_hash
    environment = lambda: {
        'boot_id': BOOT,
        'exclusive': True,
        'pynq': 'mock',
        'loaded_bit_sha256': bit_hash,
    }
    journal = core.Journal(tmp_path / 'guard')
    if receipt:
        (journal.root / 'overlay-receipt.json').write_text(json.dumps({
            'schema': 1,
            'mode': 'overlay-only',
            'boot_id': BOOT,
            'bit_sha256': plan.bit_sha256,
        }), encoding='utf-8')
    if mmio_receipt:
        (journal.root / 'mmio-receipt.json').write_text(json.dumps({
            'schema': 1,
            'mode': 'mmio-read-only',
            'boot_id': BOOT,
            'bit_sha256': plan.bit_sha256,
        }), encoding='utf-8')
    worker = core.Worker(plan, api, journal, environment,
                         fixed_identity=False, timeout_s=1.0, mode=mode)
    return worker, api, plan, journal


def test_overlay_only_writes_receipt_after_explicit_health_stage(tmp_path):
    worker, api, plan, journal = make_rig(tmp_path, 'overlay-only', receipt=False)

    assert worker.command('ENV')['ok']
    assert worker.command('LOAD')['ok']
    assert not (journal.root / 'overlay-receipt.json').exists()
    result = worker.command('HEALTH', health())

    assert result['ok'] and result['state'] == 'PASS'
    receipt = json.loads((journal.root / 'overlay-receipt.json').read_text())
    assert receipt == {
        'schema': 1,
        'mode': 'overlay-only',
        'boot_id': BOOT,
        'bit_sha256': plan.bit_sha256,
    }
    assert len(api.overlay_calls) == 1
    assert not api.mmios
    assert not worker.command('MIN_READ')['ok']


def test_overlay_only_failure_does_not_issue_a_receipt(tmp_path):
    worker, api, _, journal = make_rig(tmp_path, 'overlay-only', receipt=False)
    assert worker.command('ENV')['ok']
    assert worker.command('LOAD')['ok']

    assert not worker.command('HEALTH', health(boot='changed'))['ok']
    assert (journal.root / 'overlay-attempt.json').exists()
    assert not (journal.root / 'overlay-receipt.json').exists()
    assert not api.mmios


@pytest.mark.parametrize('receipt', [False, True])
def test_mmio_read_only_needs_receipt_and_never_opens_dma(tmp_path, receipt):
    worker, api, _, journal = make_rig(tmp_path, 'mmio-read-only', receipt=receipt)
    if not receipt:
        assert not worker.command('ENV')['ok']
        assert not api.mmios
        return

    assert worker.command('ENV')['ok']
    assert worker.command('HEALTH', health())['ok']
    result = worker.command('MIN_READ')

    assert result['ok'] and result['state'] == 'PASS'
    assert set(api.mmios) == {worker.plan.checker_base}
    assert not api.mmios[worker.plan.checker_base].writes
    assert (journal.root / 'mmio-read-only-consumed.json').exists()
    assert not worker.command('INIT')['ok']
    assert worker.plan.dma_base not in api.mmios


@pytest.mark.parametrize('current', [None, 'different-bit', 'different-boot'])
def test_partial_hardware_phase_rejects_stale_or_missing_identity(tmp_path, current):
    worker, api, plan, journal = make_rig(tmp_path, 'mmio-read-only')
    if current is None:
        worker.environment = lambda: {'boot_id': BOOT, 'exclusive': True}
    elif current == 'different-bit':
        worker.environment = lambda: {
            'boot_id': BOOT, 'exclusive': True, 'loaded_bit_sha256': 'other'
        }
    else:
        worker.environment = lambda: {
            'boot_id': 'other-boot', 'exclusive': True,
            'loaded_bit_sha256': plan.bit_sha256,
        }

    assert not worker.command('ENV')['ok']
    assert not api.mmios
    assert not (journal.root / 'mmio-read-only-consumed.json').exists()


def test_dma_once_rejects_consumed_send_guard_before_mmio_or_allocation(tmp_path):
    worker, api, _, journal = make_rig(tmp_path, 'dma-once', mmio_receipt=True)
    (journal.root / 'send-attempt.json').write_text('{}', encoding='utf-8')

    assert not worker.command('ENV')['ok']
    assert not api.mmios
    assert not api.buffers


def test_dma_once_runs_only_its_explicit_stages_without_overlay(tmp_path):
    worker, api, plan, journal = make_rig(tmp_path, 'dma-once', mmio_receipt=True)

    assert worker.command('ENV')['ok']
    assert worker.command('HEALTH', health())['ok']
    assert worker.command('MIN_READ')['ok']
    assert worker.command('INIT')['ok']
    assert worker.command('SEND')['ok']
    assert worker.command('VERIFY')['ok']
    assert worker.command('RELEASE')['ok']
    assert worker.command('POST', health())['ok']

    assert worker.state == 'PASS'
    assert not api.overlay_calls
    assert len([entry for entry in api.mmios[plan.dma_base].writes if entry[0] == 0x28]) == 1
    assert (journal.root / 'dma-once-consumed.json').exists()
    assert (journal.root / 'send-attempt.json').exists()


def test_dma_once_requires_prior_successful_mmio_phase(tmp_path):
    worker, api, _, _ = make_rig(tmp_path, 'dma-once', mmio_receipt=False)

    assert not worker.command('ENV')['ok']
    assert not api.mmios
    assert not api.buffers


@pytest.mark.parametrize('mode', ['overlay-only', 'mmio-read-only', 'dma-once'])
def test_partial_phase_cannot_bypass_consumed_full_trial(tmp_path, mode):
    worker, api, _, journal = make_rig(
        tmp_path, mode, mmio_receipt=(mode == 'dma-once'))
    journal.claim('trial-consumed', {'boot_id': BOOT, 'bit_sha256': worker.plan.bit_sha256})

    assert not worker.command('ENV')['ok']
    assert not api.overlay_calls
    assert not api.mmios
    assert not api.buffers


@pytest.mark.parametrize('receipt_name', ['overlay-receipt', 'mmio-receipt'])
def test_dma_once_rejects_mismatched_stage_receipt(tmp_path, receipt_name):
    worker, api, _, journal = make_rig(
        tmp_path, 'dma-once', mmio_receipt=True)
    path = journal.root / (receipt_name + '.json')
    receipt = json.loads(path.read_text(encoding='utf-8'))
    receipt['bit_sha256'] = '0' * 64
    path.write_text(json.dumps(receipt), encoding='utf-8')

    assert not worker.command('ENV')['ok']
    assert not api.mmios
    assert not api.buffers


def test_terminal_failure_marker_blocks_later_process_phase(tmp_path):
    first, api, _, journal = make_rig(
        tmp_path, 'overlay-only', receipt=False, mmio_receipt=False)
    assert first.command('ENV')['ok']
    assert first.command('LOAD')['ok']
    assert not first.command('HEALTH', health(boot='changed'))['ok']

    second, _, _, _ = make_rig(
        tmp_path, 'mmio-read-only', receipt=True, mmio_receipt=True)
    assert not second.command('ENV')['ok']
    assert not api.mmios


@pytest.mark.parametrize('mode', ['overlay-only', 'mmio-read-only', 'dma-once'])
def test_child_rejects_missing_mode_authorization_before_bundle_or_board_api(
        monkeypatch, mode):
    def forbidden(*args, **kwargs):
        raise AssertionError('authorization failure must precede bundle and board setup')

    monkeypatch.setattr(child, 'verify_bundle', forbidden)
    monkeypatch.setattr(child, 'BoardAPI', forbidden)
    with pytest.raises(SystemExit):
        child.main(['--execute', '--approve-once', '--mode', mode,
                    '--executor-manifest-sha', '0' * 64])


@pytest.mark.parametrize('mode', ['overlay-only', 'mmio-read-only', 'dma-once'])
def test_partial_mode_requires_exact_authorization_token(mode):
    args = ['--execute', '--approve-once', '--mode', mode,
            '--authorize-stage', 'overlay-only' if mode != 'overlay-only' else 'mmio-read-only',
            '--executor-manifest-sha', '0' * 64]

    with pytest.raises(SystemExit):
        child.parse_args(args)


def test_partial_mode_accepts_matching_authorization_token():
    for mode in ('overlay-only', 'mmio-read-only', 'dma-once'):
        args = ['--execute', '--approve-once', '--mode', mode,
                '--authorize-stage', mode, '--executor-manifest-sha', '0' * 64]
        assert child.parse_args(args).mode == mode


def test_expired_external_deadline_blocks_stage_before_hardware_access(tmp_path):
    worker, api, _, _ = make_rig(tmp_path, 'mmio-read-only')
    assert worker.command('ENV')['ok']
    assert worker.command('HEALTH', health())['ok']

    result = worker.command('MIN_READ', deadline_monotonic_s=time.monotonic() - 1)

    assert not result['ok']
    assert not api.mmios


def test_child_requires_supervision_claim_and_rejects_incomplete_other_phase(tmp_path):
    journal = core.Journal(tmp_path)
    with pytest.raises(core.base.PreflightError, match='supervisor authorization'):
        child.check_start_guards('overlay-only', journal, 'reviewed')
    journal.claim('supervision-overlay-only-consumed', {'mode':'overlay-only','manifest_sha256':'reviewed'})
    child.check_start_guards('overlay-only', journal, 'reviewed')
    with pytest.raises(core.base.PreflightError, match='incomplete supervision'):
        child.check_start_guards('mmio-read-only', journal)


def test_partial_supervision_pass_is_not_success(tmp_path):
    journal = core.Journal(tmp_path)
    journal.claim('supervision-overlay-only-consumed', {'mode':'overlay-only','manifest_sha256':'reviewed'})
    (tmp_path/'supervision-overlay-only-pass.json').write_text('{')
    with pytest.raises(core.base.PreflightError):
        child.check_start_guards('mmio-read-only', journal)
