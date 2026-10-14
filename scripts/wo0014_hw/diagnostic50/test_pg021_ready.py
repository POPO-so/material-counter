"""PG021 direct-mode first-transfer readiness regressions; no board access."""
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core

spec = importlib.util.spec_from_file_location(
    'pg021_old_tests', HERE.parent / 'test_pynq_image_once.py')
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)


def make_rig(tmp_path, *, run_dma_status=0x2, completion_status=None):
    paths = old.make_artifacts(tmp_path)
    plan = core.base.validate_inputs(*paths)
    api = old.FakeApi(plan.packet, run_dma_status=run_dma_status)
    create_mmio = api.MMIO

    def mmio(base, length):
        device = create_mmio(base, length)
        if base == plan.dma_base:
            write = device.write

            def complete(offset, value):
                write(offset, value)
                if offset == 0x28:
                    if completion_status is not None:
                        device.regs[4] = completion_status
                    elif device.mode == 'success':
                        device.regs[4] |= 0x1000

            device.write = complete
        return device

    api.MMIO = mmio
    environment = lambda: {
        'boot_id': 'mock-boot', 'exclusive': True, 'pynq': 'mock',
    }
    worker = core.Worker(
        plan, api, core.Journal(tmp_path / 'guard'), environment,
        fixed_identity=False, timeout_s=0.5)
    return worker, api, plan


def health():
    return {'ping': True, 'ssh': True, 'linux': True, 'boot_id': 'mock-boot'}


def advance_to_init(worker):
    for stage in ('ENV', 'LOAD', 'HEALTH', 'MIN_READ', 'INIT'):
        result = worker.command(stage, health() if stage == 'HEALTH' else None)
        assert result['ok'], result


def writes_at(dma, offset):
    return [value for register, value in dma.writes if register == offset]


def test_reset_first_frame_accepts_dmasr_zero_and_verifies_idle_ioc(tmp_path):
    worker, api, plan = make_rig(
        tmp_path, run_dma_status=0, completion_status=0x1002)
    advance_to_init(worker)

    sent = worker.command('SEND')

    assert sent['ok'], sent
    dma = api.mmios[plan.dma_base]
    assert writes_at(dma, 0x18) == [api.address]
    assert writes_at(dma, 0x28) == [230432]
    assert dma.regs[4] == 0x1002
    assert worker.command('VERIFY')['ok']


@pytest.mark.parametrize('run_status', [0x1, 0x10])
def test_halted_or_error_after_runstop_never_programs_sa_or_length(tmp_path, run_status):
    worker, api, plan = make_rig(tmp_path, run_dma_status=run_status)
    advance_to_init(worker)
    worker.timeout_s = 0.08

    result = worker.command('SEND')

    assert not result['ok']
    dma = api.mmios[plan.dma_base]
    assert writes_at(dma, 0x18) == []
    assert writes_at(dma, 0x28) == []


@pytest.mark.parametrize('completion_status', [0x0, 0x3, 0x12])
def test_post_length_requires_idle_without_halted_or_error(tmp_path, completion_status):
    worker, api, plan = make_rig(
        tmp_path, run_dma_status=0x2, completion_status=completion_status)
    advance_to_init(worker)
    worker.timeout_s = 0.08

    result = worker.command('SEND')

    assert not result['ok']
    assert worker.state == 'FAIL'
    dma = api.mmios[plan.dma_base]
    assert writes_at(dma, 0x28) == [230432]
    assert dma.regs[4] == completion_status
