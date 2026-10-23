"""Offline proposal core. No PYNQ, SSH, filesystem mutation, allocation, or board adapter."""
from __future__ import annotations
import hashlib
import json
import math

BOOT = '822a9c29-37c0-438c-81c4-da998d57073b'
MANIFEST = '84c27012ec038d48267128af08deb9685eb1bb9a5fdb1a79e5da064083788312'
BIT = 'a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420'
HWH = 'bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6'
DMA = 0x40400000
ERROR_MASK = 0x4070
REG_OFFSETS = (0x00, 0x04, 0x18, 0x28, 0x30, 0x34, 0x48, 0x58)
IDENTITY = {'boot_id': BOOT, 'manifest_sha256': MANIFEST, 'bit_sha256': BIT, 'hwh_sha256': HWH}

class ProposalRejected(ValueError):
    pass

def require_identity(identity):
    if any(identity.get(k) != v for k, v in IDENTITY.items()):
        raise ProposalRejected('boot or locked artifact identity differs')

def validate_stopped_baseline(snapshot):
    require_identity(snapshot['identity'])
    if snapshot['checker_signature'] != 0x574f3134 or snapshot['packer_signature'] != 0x574f3135:
        raise ProposalRejected('signature differs')
    if any(snapshot['counters'].values()):
        raise ProposalRejected('Checker/Packer baseline is not untouched')
    regs = snapshot['dma']
    expected = {0x00: 0x10002, 0x04: 1, 0x18: 0, 0x28: 0,
                0x30: 0x10002, 0x34: 1, 0x48: 0, 0x58: 0}
    if regs != expected:
        raise ProposalRejected('DMA state differs from the reviewed stopped/no-length baseline')
    if snapshot['CmaTotal'] != 131072 or snapshot['CmaFree'] < 32768:
        raise ProposalRejected('Tier 1 memory accounting no longer passes')
    if snapshot['system_ram'] != [0, 0x20000000] or not snapshot['ownership_clean']:
        raise ProposalRejected('RAM/ownership gate no longer passes')
    return [
        {'address': DMA + 0x30, 'offset': 0x30, 'before': regs[0x30], 'value': regs[0x30] | 1},
        {'address': DMA + 0x00, 'offset': 0x00, 'before': regs[0x00], 'value': regs[0x00] | 1},
    ]

def exercise_proposed_sequence(api, *, timeout_s=1.0):
    """Exercise against an injected offline model; a future live adapter needs separate approval."""
    before = api.snapshot()
    writes = validate_stopped_baseline(before)
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ProposalRejected('invalid bounded poll timeout')
    api.claim_once()
    evidence = {'identity': dict(IDENTITY), 'before': before, 'writes': [], 'status': 'RUN_ONLY_RUNNING'}
    for write, status_offset in zip(writes, (0x34, 0x04)):
        if api.boot_id() != BOOT:
            evidence.update(status='FAIL_STOPPED', reason='boot changed before write')
            return evidence
        api.write32(write['offset'], write['value'])
        evidence['writes'].append(write)
        deadline = api.monotonic() + timeout_s
        while True:
            sr = api.read32(status_offset)
            if sr & ERROR_MASK:
                evidence.update(status='FAIL_STOPPED', reason='DMA error after Run-only write', status_word=sr)
                return evidence
            if not (sr & 1):
                break
            if api.monotonic() >= deadline:
                evidence.update(status='FAIL_STOPPED', reason='Halted did not clear', status_word=sr)
                return evidence
            api.sleep(.001)
    after = api.snapshot()
    evidence['after'] = after
    if after['identity'] != before['identity'] or after['counters'] != before['counters']:
        evidence.update(status='FAIL_STOPPED', reason='identity or counters changed after Run-only')
        return evidence
    if (after['checker_signature'] != before['checker_signature'] or
            after['packer_signature'] != before['packer_signature'] or
            after['CmaTotal'] != 131072 or after['CmaFree'] < 32768 or
            after['system_ram'] != [0, 0x20000000] or not after['ownership_clean']):
        evidence.update(status='FAIL_STOPPED', reason='post-Run signature or resource gate changed')
        return evidence
    expected_after = dict(before['dma'])
    expected_after.update({0x30: 0x10003, 0x00: 0x10003, 0x34: 0, 0x04: 0})
    if after['dma'] != expected_after:
        evidence.update(status='FAIL_STOPPED', reason='unexpected post-Run DMA state')
        return evidence
    evidence.update(status='RUN_ONLY_PASS', after=after, length_writes=0,
                    dma_transfer_started=False, cma_allocations=0, overlay_loads=0)
    return evidence

def terminal_archive_plan(raw_terminal: bytes, expected_sha: str, recovery_result):
    """Produce a reviewable plan only; never rename or remove any live Guard."""
    if recovery_result.get('status') != 'RUN_ONLY_PASS':
        raise ProposalRejected('Run-only has not passed; terminal marker must remain')
    require_identity(recovery_result['identity'])
    try:
        planned = validate_stopped_baseline(recovery_result['before'])
        after = recovery_result['after']
        expected_dma = dict(recovery_result['before']['dma'])
        expected_dma.update({0x30: 0x10003, 0: 0x10003, 0x34: 0, 4: 0})
        require_identity(after['identity'])
        valid = (recovery_result['writes'] == planned and after['dma'] == expected_dma and
                 after['counters'] == recovery_result['before']['counters'] and
                 after['checker_signature'] == 0x574f3134 and after['packer_signature'] == 0x574f3135 and
                 after['CmaTotal'] == 131072 and after['CmaFree'] >= 32768 and
                 after['system_ram'] == [0, 0x20000000] and after['ownership_clean'] is True and
                 recovery_result['length_writes'] == 0 and recovery_result['cma_allocations'] == 0 and
                 recovery_result['overlay_loads'] == 0 and recovery_result['dma_transfer_started'] is False)
    except (KeyError, TypeError):
        raise ProposalRejected('incomplete Run-only evidence') from None
    if not valid:
        raise ProposalRejected('inconsistent Run-only evidence')
    if hashlib.sha256(raw_terminal).hexdigest() != expected_sha:
        raise ProposalRejected('terminal marker bytes differ')
    marker = json.loads(raw_terminal)
    # Historical terminal schema omitted HWH; the locked manifest and complete
    # independently verified recovery identity must still bind that artifact.
    if any(marker.get(k) != v for k, v in IDENTITY.items() if k != 'hwh_sha256'):
        raise ProposalRejected('historical terminal identity differs')
    if 'hwh_sha256' in marker and marker['hwh_sha256'] != HWH:
        raise ProposalRejected('historical terminal HWH differs')
    if (marker.get('status') != 'TERMINAL_FAILURE' or marker.get('stage') != 'initial-state' or
            marker.get('reason') != 'MM2S Halted != 0; S2MM Halted != 0'):
        raise ProposalRejected('terminal marker is not the reviewed Halted-only failure')
    return {'status': 'ARCHIVE_PROPOSED_NOT_EXECUTED',
            'source': '/var/lib/wo0015-s2mm/terminal-failure.json',
            'destination': '/var/lib/wo0015-s2mm/history/20261015-retry3-initial-state-terminal-failure.json',
            'preserved_sha256': expected_sha, 'must_not_overwrite_destination': True,
            'overlay_consumed_guard_unchanged': True, 'overlay_pass_receipt_unchanged': True,
            'fresh_postflight_and_explicit_archive_approval_required': True}
