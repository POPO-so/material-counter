"""Exercise the milestone CLI against real, disposable Git repositories."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).with_name('record_milestone.py')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.email', 'test@example.invalid')
    git(root, 'config', 'user.name', 'Test')
    (root / 'code.py').write_text('value = 1\n')
    (root / 'evidence.json').write_text('{"frames":300}\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'source')
    return root


def cli(repo, *args):
    assert SCRIPT.exists(), 'milestone CLI is not implemented'
    return subprocess.run([sys.executable, '-B', str(SCRIPT), *args], cwd=repo,
                          capture_output=True, text=True, encoding='utf8',
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})


def record(repo, *extra):
    return cli(repo, 'record', '--stage', 'WO-test', '--environment', 'Offline',
               '--summary', 'Test | metric\nsecond line', '--evidence', 'evidence.json',
               '--pytest-passed', '10', '--pytest-failed', '0', '--pytest-errors', '0', '--test-scope', 'unit tests',
               '--verification', 'passed', *extra)


def commit_ledger(repo):
    git(repo, 'add', 'docs/benchmarks/optimization_ledger.md')
    git(repo, 'commit', '-qm', 'record benchmark')


def test_dry_run_does_not_write_or_tag(repo):
    result = record(repo, '--dry-run')
    assert result.returncode == 0, result.stderr
    assert not (repo / 'docs').exists()
    assert git(repo, 'status', '--porcelain') == ''
    assert git(repo, 'tag') == ''


def test_dirty_record_rejected_without_changing_files(repo):
    (repo / 'unrelated.txt').write_text('keep')
    result = record(repo)
    assert result.returncode != 0 and 'clean' in result.stderr.lower()
    assert not (repo / 'docs').exists()
    assert (repo / 'unrelated.txt').read_text() == 'keep'


def test_append_only_and_annotated_tag_with_metrics(repo):
    assert record(repo, '--fps', '22.132', '--accuracy', '4/5 (80%)').returncode == 0
    ledger = repo / 'docs/benchmarks/optimization_ledger.md'
    old = ledger.read_bytes()
    assert b'&#124;' in old
    commit_ledger(repo)
    result = cli(repo, 'tag', '--tag', 'v1.1-fps-opt', '--dry-run')
    assert result.returncode == 0, result.stderr
    assert git(repo, 'tag') == ''
    result = cli(repo, 'tag', '--tag', 'v1.1-fps-opt')
    assert result.returncode == 0, result.stderr
    assert git(repo, 'cat-file', '-t', 'v1.1-fps-opt') == 'tag'
    msg = git(repo, 'for-each-ref', '--format=%(contents)', 'refs/tags/v1.1-fps-opt')
    assert '22.132' in msg and '4/5 (80%)' in msg and '10 passed' in msg
    assert record(repo).returncode == 0
    assert ledger.read_bytes().startswith(old)


@pytest.mark.parametrize('change', ['dirty', 'source', 'evidence', 'failed', 'unknown'])
def test_tag_fail_closed(repo, change):
    flags = ['--verification', 'failed' if change == 'failed' else 'not-verified'] if change in ('failed','unknown') else []
    assert record(repo, *flags).returncode == 0
    commit_ledger(repo)
    if change in ('source', 'evidence', 'dirty'):
        path = repo / ('evidence.json' if change == 'evidence' else 'code.py')
        path.write_text('changed\n')
        if change != 'dirty':
            git(repo, 'add', str(path))
            git(repo, 'commit', '-qm', 'changed after validation')
    result = cli(repo, 'tag', '--tag', 'v1.0-hmi-ready')
    assert result.returncode != 0
    assert git(repo, 'tag') == ''


def test_duplicate_tag_not_overwritten(repo):
    assert record(repo).returncode == 0
    commit_ledger(repo)
    assert cli(repo, 'tag', '--tag', 'v1.0-hmi-ready').returncode == 0
    old = git(repo, 'rev-parse', 'v1.0-hmi-ready')
    assert cli(repo, 'tag', '--tag', 'v1.0-hmi-ready').returncode != 0
    assert git(repo, 'rev-parse', 'v1.0-hmi-ready') == old


def test_tag_rejects_history_rewrite_in_ledger_commit(repo):
    assert record(repo).returncode == 0
    commit_ledger(repo)
    assert record(repo).returncode == 0
    ledger = repo / 'docs/benchmarks/optimization_ledger.md'
    text = ledger.read_text(encoding='utf8')
    ledger.write_text(text.replace('Test &#124; metric', 'rewritten history', 1), encoding='utf8')
    commit_ledger(repo)
    result = cli(repo, 'tag', '--tag', 'v1.0-history')
    assert result.returncode != 0
    assert git(repo, 'tag') == ''


def test_junit_counts_testcases_not_inflated_subtest_suite_total(repo, tmp_path):
    junit = tmp_path / 'pytest.xml'
    junit.write_text('<testsuites><testsuite tests="8" failures="0" errors="0" skipped="1">'
                     '<testcase name="a"/><testcase name="b"/><testcase name="c"><skipped/></testcase>'
                     '</testsuite></testsuites>')
    result = cli(repo, 'record', '--stage', 'JUnit', '--environment', 'Offline',
                 '--summary', 'summary', '--evidence', 'evidence.json', '--pytest-junit', str(junit),
                 '--test-scope', 'unit', '--verification', 'passed')
    assert result.returncode == 0, result.stderr
    assert '2 passed; 1 skipped' in (repo / 'docs/benchmarks/optimization_ledger.md').read_text(encoding='utf8')


def test_failing_junit_cannot_unlock_tag(repo, tmp_path):
    junit = tmp_path / 'failed.xml'
    junit.write_text('<testsuites><testsuite tests="1" errors="1"><testcase name="bad"><error/>'
                     '</testcase></testsuite></testsuites>')
    result = cli(repo, 'record', '--stage', 'failed', '--environment', 'Offline', '--summary', 'bad',
                 '--evidence', 'evidence.json', '--pytest-junit', str(junit), '--test-scope', 'unit',
                 '--verification', 'passed')
    assert result.returncode == 0, result.stderr
    commit_ledger(repo)
    assert cli(repo, 'tag', '--tag', 'v1.0-test').returncode != 0


def test_missing_evidence_or_invalid_tag_no_mutation(repo):
    assert record(repo, '--evidence', 'missing.json').returncode != 0
    assert not (repo / 'docs').exists()
    assert record(repo).returncode == 0
    commit_ledger(repo)
    assert cli(repo, 'tag', '--tag', '--force').returncode != 0
    assert git(repo, 'tag') == ''


def test_push_is_opt_in_and_exact_tag_only(repo, tmp_path):
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
    git(repo, 'remote', 'add', 'origin', str(remote))
    assert record(repo).returncode == 0
    commit_ledger(repo)
    assert cli(repo, 'tag', '--tag', 'v1.0-local').returncode == 0
    assert git(repo, 'ls-remote', '--tags', 'origin') == ''
    assert cli(repo, 'tag', '--tag', 'v1.1-pushed', '--push').returncode == 0
    refs = git(repo, 'ls-remote', 'origin')
    assert 'refs/tags/v1.1-pushed' in refs and 'refs/heads/' not in refs
    assert 'v1.0-local' not in refs


def test_relative_junit_resolves_same_file_as_evidence_from_subdirectory(repo):
    (repo / 'reports').mkdir()
    (repo / 'reports' / 'results.xml').write_text('<testsuites><testsuite errors="1"><testcase><error/></testcase></testsuite></testsuites>')
    sub = repo / 'sub'
    (sub / 'reports').mkdir(parents=True)
    (sub / 'reports' / 'results.xml').write_text('<testsuites><testsuite><testcase/><testcase/></testsuite></testsuites>')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'two reports')
    result = cli(sub, 'record', '--stage', 'paths', '--environment', 'Offline', '--summary', 'path test',
                 '--evidence', 'evidence.json', '--pytest-junit', 'reports/results.xml', '--test-scope', 'unit',
                 '--verification', 'passed')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.split('Appended.')[0])['record']['pytest']['errors'] == 1


def test_manual_counts_require_explicit_failures_and_gate_on_them(repo):
    result = cli(repo, 'record', '--stage', 'manual', '--environment', 'Offline', '--summary', 'test',
                 '--evidence', 'evidence.json', '--pytest-passed', '10', '--test-scope', 'unit',
                 '--verification', 'passed')
    assert result.returncode != 0
    assert record(repo, '--pytest-failed', '1').returncode == 0
    commit_ledger(repo)
    assert cli(repo, 'tag', '--tag', 'v1.0-failed').returncode != 0


def test_record_explicit_measurement_date(repo):
    result = record(repo, '--date', '2026-10-21', '--dry-run')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['record']['date'] == '2026-10-21'
    assert record(repo, '--date', '2026-02-30', '--dry-run').returncode != 0
