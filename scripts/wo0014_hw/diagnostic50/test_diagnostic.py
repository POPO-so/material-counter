"""Safety regressions use a fake device; no PYNQ imports or board connections."""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('old_tests', HERE.parent / 'test_pynq_image_once.py')
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)


def core_module():
    import core
    return core


@pytest.fixture
def rig(tmp_path):
    c = core_module()
    paths = old.make_artifacts(tmp_path)
    plan = old.wo14.validate_inputs(*paths)
    api = old.FakeApi(plan.packet)
    create = api.MMIO
    def mmio(*a, **kw):
        m = create(*a, **kw)
        if m.base == plan.dma_base:
            write = m.write
            def completed(offset, value):
                write(offset, value)
                if offset == 0x28 and m.mode == 'success':
                    m.regs[4] |= 0x1000
            m.write = completed
        return m
    api.MMIO = mmio
    env = lambda: {'boot_id': 'mock-boot', 'exclusive': True, 'pynq': 'mock'}
    worker = c.Worker(plan, api, c.Journal(tmp_path / 'guard'), env,
                      fixed_identity=False, timeout_s=1.0)
    return c, worker, api, plan


def health():
    return {'ping': True, 'ssh': True, 'linux': True, 'boot_id': 'mock-boot'}


def advance(worker, stop='INIT'):
    for stage in ['ENV', 'LOAD', 'HEALTH', 'MIN_READ', 'INIT']:
        result = worker.command(stage, health() if stage == 'HEALTH' else None)
        assert result['ok'], result
        if stage == stop:
            break


def calls(api):
    return sum(len(m.writes) for m in api.mmios.values())


def test_missing_executor_is_a_failing_safety_regression():
    assert (HERE / 'core.py').is_file(), 'new fail-closed executor is not implemented'


def test_normal_single_frame(rig):
    c, w, api, plan = rig
    advance(w)
    assert w.command('SEND')['ok']
    assert w.command('VERIFY')['ok']
    assert w.command('RELEASE')['ok']
    assert w.command('POST', health())['ok']
    assert w.state == 'PASS'
    assert api.buffers[0].free_count == 1
    assert len([x for x in api.mmios[plan.dma_base].writes if x[0] == 0x28]) == 1
    assert (w.journal.root / 'send-attempt.json').is_file()
    before = calls(api)
    assert not w.command('SEND')['ok']
    assert calls(api) == before


@pytest.mark.parametrize('failed', ['ping', 'ssh', 'linux'])
def test_post_load_health_failure_prevents_all_mmio(rig, failed):
    _, w, api, _ = rig
    advance(w, 'LOAD')
    report = health(); report[failed] = False
    assert not w.command('HEALTH', report)['ok']
    assert not api.mmios
    assert not w.command('MIN_READ')['ok']
    assert not api.mmios


def test_overlay_failure_stops_before_mmio(rig):
    _, w, api, _ = rig
    api.Overlay = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('overlay fault'))
    assert w.command('ENV')['ok']
    assert not w.command('LOAD')['ok']
    assert not w.command('MIN_READ')['ok']
    assert not api.mmios


@pytest.mark.parametrize('kind', ['create', 'read'])
def test_mmio_exception_stops_all_followup_access(rig, kind):
    _, w, api, _ = rig
    advance(w, 'LOAD'); assert w.command('HEALTH', health())['ok']
    accesses = []
    def fail(*args, **kwargs):
        accesses.append(kind)
        raise OSError('MMIO fault')
    if kind == 'create':
        api.MMIO = fail
    else:
        original = api.MMIO
        def create(*args, **kwargs):
            m = original(*args, **kwargs); m.read = fail; return m
        api.MMIO = create
    assert not w.command('MIN_READ')['ok']
    assert len(accesses) == 1
    for stage in ['INIT', 'SEND', 'VERIFY', 'RELEASE']:
        assert not w.command(stage)['ok']
    assert len(accesses) == 1
    assert calls(api) == 0


def test_dma_initial_failure_never_sends(rig):
    _, w, api, _ = rig
    api.initial_dma_status = 0
    advance(w, 'MIN_READ')
    assert not w.command('INIT')['ok']
    assert not w.command('SEND')['ok']
    assert calls(api) == 0


@pytest.mark.parametrize('fault', ['poll_exception', 'timeout', 'checker'])
def test_after_length_failure_never_retries_or_reads_for_cleanup(rig, fault):
    _, w, api, plan = rig
    advance(w)
    api.mode = 'timeout' if fault == 'timeout' else 'success'
    dma = api.mmios[plan.dma_base]
    if fault == 'timeout':
        dma.mode = 'timeout'
        w.timeout_s = 0.15
    if fault == 'poll_exception':
        read = dma.read
        def fail(offset):
            if dma.regs.get(0x28):
                raise OSError('poll fault')
            return read(offset)
        dma.read = fail
    if fault == 'checker':
        write = dma.write
        def corrupt(offset, value):
            write(offset, value)
            if offset == 0x28:
                api.checker.regs[0x40] ^= 255
        dma.write = corrupt
    result = w.command('SEND')
    if fault == 'checker':
        assert result['ok']
        assert not w.command('VERIFY')['ok']
    else:
        assert not result['ok']
    assert api.buffers[0].free_count == 0
    before = calls(api)
    dma.read = lambda _: (_ for _ in ()).throw(AssertionError('unsafe extra read'))
    for stage in ['SEND', 'VERIFY', 'RELEASE']:
        assert not w.command(stage)['ok']
    assert calls(api) == before
    assert len([x for x in dma.writes if x[0] == 0x28]) == 1


def test_persistent_trial_refuses_restart(rig):
    c, w, api, plan = rig
    advance(w, 'LOAD')
    second = c.Worker(plan, api, c.Journal(w.journal.root), w.environment,
                      fixed_identity=False)
    assert not second.command('ENV')['ok']
    assert len(api.overlay_calls) == 1


def test_skipping_gate_is_terminal(rig):
    _, w, api, _ = rig
    assert not w.command('SEND')['ok']
    assert not w.command('ENV')['ok']
    assert not api.overlay_calls


def test_send_claim_before_length_fault(rig):
    _, w, api, plan = rig
    advance(w)
    dma = api.mmios[plan.dma_base]
    write = dma.write
    def fail(offset, value):
        if offset == 0x28:
            assert (w.journal.root / 'send-attempt.json').is_file()
            raise OSError('uncertain LENGTH write')
        return write(offset, value)
    dma.write = fail
    assert not w.command('SEND')['ok']
    assert w.send_attempts == 1
    assert api.buffers[0].free_count == 0


def test_post_health_failure_is_not_pass(rig):
    _, w, api, _ = rig
    advance(w)
    for stage in ['SEND', 'VERIFY', 'RELEASE']:
        assert w.command(stage)['ok']
    report = health(); report['boot_id'] = 'changed'
    assert not w.command('POST', report)['ok']
    assert w.state == 'FAIL'


def test_real_identity_rejects_mock_artifacts(rig):
    c, w, api, plan = rig
    with pytest.raises(c.base.PreflightError):
        c.validate_50(plan)


def test_late_read_cannot_advance_to_dma(rig):
    _, w, api, _ = rig
    advance(w, 'LOAD'); assert w.command('HEALTH', health())['ok']
    create = api.MMIO
    def late(*a, **kw):
        m = create(*a, **kw)
        def read(offset):
            time.sleep(0.08)
            return 0x574f3134
        m.read = read
        return m
    api.MMIO = late
    w.timeout_s = 0.04
    assert not w.command('MIN_READ')['ok']
    assert not w.command('INIT')['ok']
    assert 0x40400000 not in api.mmios


def test_transport_loss_prevents_next_hardware_access(rig):
    _, w, api, _ = rig
    advance(w, 'MIN_READ')
    w.may_progress = lambda: False
    assert not w.command('INIT')['ok']
    assert 0x40400000 not in api.mmios


def test_verify_requires_ioc_and_raw_dma_readback(rig):
    _, w, api, plan = rig
    advance(w)
    assert w.command('SEND')['ok']
    api.mmios[plan.dma_base].regs[4] = 2  # IDLE alone is not IOC evidence.
    assert not w.command('VERIFY')['ok']
    assert api.buffers[0].free_count == 0


def test_logging_failure_before_send_claim_never_writes_length(rig):
    _, w, api, plan = rig
    advance(w)
    claim = w.journal.claim
    def fail(name, values):
        if name == 'send-attempt':
            raise OSError('disk unavailable')
        return claim(name, values)
    w.journal.claim = fail
    assert not w.command('SEND')['ok']
    assert not [x for x in api.mmios[plan.dma_base].writes if x[0] == 0x28]
    assert api.buffers[0].free_count == 0


@pytest.mark.parametrize('fault', ['alignment', 'range', 'sync'])
def test_buffer_failure_prevents_programming(rig, fault):
    _, w, api, plan = rig
    advance(w)
    if fault == 'alignment':
        api.address = 0x10000001
    if fault == 'range':
        api.address = 0x1ffff000
    if fault == 'sync':
        alloc = api.allocate
        def allocation(**kw):
            b = alloc(**kw)
            def failed():
                raise OSError('cache sync failed')
            b.sync_to_device = failed
            return b
        api.allocate = allocation
    assert not w.command('SEND')['ok']
    assert calls(api) == 0


@pytest.mark.parametrize('changed', ['ownership', 'boot', 'overlay'])
def test_pre_send_environment_change_prevents_dma(rig, changed):
    _, w, api, _ = rig
    advance(w)
    report = {'boot_id': 'mock-boot', 'exclusive': True, 'loaded_bit_sha256': w.plan.bit_sha256}
    if changed == 'ownership': report['exclusive'] = False
    if changed == 'boot': report['boot_id'] = 'other-boot'
    if changed == 'overlay': report['loaded_bit_sha256'] = 'other-overlay'
    w.environment = lambda: report
    assert not w.command('SEND')['ok']
    assert not api.buffers
    assert calls(api) == 0


def test_partial_persistent_marker_is_also_consumed(rig):
    _, w, api, _ = rig
    (w.journal.root / 'trial-consumed.json').write_text('')
    assert not w.command('ENV')['ok']
    assert not w.command('LOAD')['ok']
    assert not api.overlay_calls


def test_final_mmio_exception_never_releases_buffer_or_rereads(rig):
    _, w, api, plan = rig
    advance(w); assert w.command('SEND')['ok']
    reads = []
    def failed(offset):
        reads.append(offset)
        raise OSError('Final read fault')
    api.mmios[plan.dma_base].read = failed
    assert not w.command('VERIFY')['ok']
    assert reads == [0]
    assert not w.command('RELEASE')['ok']
    assert reads == [0]
    assert api.buffers[0].free_count == 0
