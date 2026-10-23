"""Finalize only this result directory; never runs historical scripts or board code."""
import hashlib
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parents[1]

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def dump(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

required = ['verification-report.md', 'pynq-afi-and-base-hwh-audit.json',
            'fsbl-disassembly-and-dtb-audit.json', 'command-audit-and-whitelist-v2.md',
            'next-step-approval-request-v2.md', 'review-brief-v2.md', 'golden-and-cache-audit.json',
            'reviewer-findings.md']
for name in required:
    assert (OUT / name).is_file(), name
    if name.endswith('.json'):
        json.loads((OUT / name).read_bytes())
for name in ('command-audit-and-whitelist-v2.md', 'next-step-approval-request-v2.md'):
    assert 'NOT_APPROVED_DO_NOT_EXECUTE' in (OUT / name).read_text(encoding='utf-8')
integrity = json.loads((OUT / 'manager/integrity-after.json').read_bytes())
assert integrity['comparison'].startswith('PASS:')
tree = ET.parse(OUT / 'new-targeted-tests.xml').getroot()
suites = [tree] if tree.tag == 'testsuite' else list(tree.iter('testsuite'))
count = sum(int(s.attrib['tests']) for s in suites)
assert count > 0
assert all(int(s.attrib.get('errors', 0)) == int(s.attrib.get('failures', 0)) == int(s.attrib.get('skipped', 0)) == 0 for s in suites)
match = re.search(r'(\d+) passed(?:, (\d+) subtests passed)?', (OUT / 'new-targeted-tests.txt').read_text(encoding='utf-8-sig'))
assert match, 'pytest summary missing'
methods, subtests = int(match.group(1)), int(match.group(2) or 0)
assert methods + subtests == count, (methods, subtests, count)
summary = {'status': 'STOPPED_PRECONDITION_UNVERIFIED', 'audit_scope': 'PURE_OFFLINE_HOST_ONLY',
           'board_connections': 0, 'mmio_reads_or_writes': 0, 'overlay_loads': 0, 'dma_runs': 0,
           'images_mounted': 0, 'target_binary_executions': 0, 'commits_or_merges': 0,
           'restricted_raw': 'NOT_TRAVERSED_NOT_READ_NOT_COPIED',
           'new_targeted_test_methods_passed': methods, 'new_targeted_subtests_passed': subtests,
           'junit_cases_including_subtests': count,
           'historical_test_counts': '459/76/43 are separate historical suites; not added',
           'historical_indexed_files_verified': 320,
           'protected_nonrestricted_files_verified': len(integrity['protected_nonrestricted_hashes']),
           'hardware_locks': integrity['hardware_locks'],
           'candidate_boot_sha256': integrity['candidate_boot_sha256'],
           'candidate_image_sha256': json.loads((OUT / 'rootfs/image-identity.json').read_bytes())['sha256'],
           'test_collection_deviation': 'An initial recursive pytest collection entered extracted image test modules and failed with 26 import/collection errors because PYNQ was unavailable. No PYNQ driver/C extension or ARM target binary ran. Explicit four-file final suite replaced it; failed logs preserved.',
           'candidate_image_runtime_equivalence': 'UNKNOWN until separately authorized binding evidence',
           'historical_rx_pattern_and_root_cause': 'UNKNOWN',
           'b1_b2': 'NOT_APPROVED_DO_NOT_EXECUTE', 'report': str(OUT / 'verification-report.md')}
summary['posix_closeout'] = {
    'scope': 'incremental existing-artifact hardening; host mocks/static review only',
    'large_image_rehashed': False, 'image_reparsed': False, 'fsbl_21_tables_reparsed': False,
    'max_proc_read_bytes': 65536, 'max_boot_bin_bytes': 32 * 1024 * 1024,
    'b1_nonroot_gate_before_any_open': True,
    'b2_nonblocking_open': True,
    'b2_preread_regular_positive_size_same_device_gates': True,
    'b2_capture_metadata': ['boot_dev_differs_from_root'],
    'previous_audit_test_counts': {'methods': 44, 'subtests': 11},
    'counts_policy': 'current combined explicit-four-file suite; previous counts not added',
    'proposal_core_sha256': sha(OUT / 'proposals/remote_template_v2.py'),
    'awaiting_user_decision': 'remain offline or separately review/approve B1; no action inferred'
}
dump('summary.json', summary)
index = {p.relative_to(OUT).as_posix(): sha(p) for p in sorted(OUT.rglob('*'))
         if p.is_file() and '__pycache__' not in p.parts and p.name != 'evidence-sha256.json'}
dump('evidence-sha256.json', index)
assert all(sha(OUT / n) == h for n, h in index.items())
print(json.dumps({'status': summary['status'], 'new_tests_passed': methods, 'new_subtests_passed': subtests, 'history_files_unchanged': 320,
                  'index_verified_files': len(index), 'board_connections': 0}))
