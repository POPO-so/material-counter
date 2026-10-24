#!/usr/bin/env python3
"""Append benchmark evidence, then tag its committed ledger (Python stdlib only).

record -> review/commit ledger -> tag [--push]. No implicit commits or board access.
"""
import argparse
from datetime import date, datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

LEDGER = 'docs/benchmarks/optimization_ledger.md'
COLUMNS = ('日期', '阶段/工单 (WO)', 'Git Commit / Tag', '验证环境 (Offline/PYNQ-Z2)',
           '吞吐帧率 (FPS)', '计数准确率', 'CMA Free (KiB)', 'Pytest 通过数', '关键改动与工程收益摘要')
HEADER = '| ' + ' | '.join(COLUMNS) + ' |'
INITIAL = '# Optimization Benchmark Ledger\n\n历史行只追加，不覆盖；N/A 表示未测或不适用。\n\n' + HEADER + '\n|' + '|'.join(['---'] * 9) + '|\n'


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                            text=True, encoding='utf8')
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Git command failed')
    return result.stdout.strip()


def digest(path):
    with path.open('rb') as handle:
        value = hashlib.file_digest(handle, 'sha256').hexdigest()
    return value


def cell(value):
    return ' '.join(str(value).split()).replace('|', '&#124;').replace('<', '&lt;').replace('>', '&gt;')


def check_clean(root):
    if git(root, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise ValueError('A clean working tree is required; preserve and review existing changes first.')


def junit_counts(path):
    tree = ET.parse(path).getroot()
    cases = list(tree.iter('testcase'))
    # pytest subtests can inflate testsuite.tests; count actual testcase outcomes.
    skipped = sum(c.find('skipped') is not None for c in cases)
    failed = sum(c.find('failure') is not None for c in cases)
    errors = sum(c.find('error') is not None for c in cases)
    suites = [s for s in tree.iter('testsuite') if not s.findall('testsuite')]
    failed = max(failed, sum(int(s.get('failures', '0')) for s in suites))
    errors = max(errors, sum(int(s.get('errors', '0')) for s in suites))
    return {'passed': max(0, len(cases) - skipped - failed - errors),
            'skipped': skipped, 'failed': failed, 'errors': errors}


def evidence(root, inputs):
    result = []
    for item in inputs:
        path = Path(item)
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_file():
            raise ValueError(f'Evidence is missing: {item}')
        try:
            name = path.relative_to(root).as_posix()
        except ValueError:
            name = str(path)
        if name not in [x['path'] for x in result]:
            result.append({'path': name, 'sha256': digest(path)})
    return result


def make_record(root, args):
    head = git(root, 'rev-parse', 'HEAD')
    tested = git(root, 'rev-parse', '--verify', (args.tested_commit or head) + '^{commit}')
    if tested != head:
        raise ValueError('The tested commit must equal current HEAD; do not reuse stale results.')
    junit = (root / args.pytest_junit).resolve() if args.pytest_junit else None
    if args.pytest_passed is not None and (args.pytest_failed is None or args.pytest_errors is None):
        raise ValueError('Manual counts require explicit --pytest-failed and --pytest-errors plus log evidence.')
    counts = (junit_counts(junit) if junit else
              {'passed': args.pytest_passed, 'skipped': args.pytest_skipped,
               'failed': args.pytest_failed or 0, 'errors': args.pytest_errors or 0})
    verification = args.verification
    if counts['failed'] or counts['errors']:
        verification = 'failed'
    sources = list(args.evidence)
    if junit:
        sources.append(str(junit))
    measurement_date = args.date or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    if date.fromisoformat(measurement_date).isoformat() != measurement_date:
        raise ValueError('Date must be YYYY-MM-DD.')
    return {'schema': 1, 'date': measurement_date,
            'source_commit': head, 'stage': args.stage, 'environment': args.environment,
            'fps': args.fps, 'accuracy': args.accuracy, 'cma_free_kib': args.cma_free_kib,
            'latency_ms': args.latency_ms, 'summary': args.summary, 'verification': verification,
            'pytest': counts, 'test_scope': args.test_scope, 'evidence': evidence(root, sources)}


def pytest_text(record):
    p = record['pytest']
    return f"{p['passed'] if p['passed'] is not None else 'N/A'} passed; {p['skipped']} skipped; {p['failed']} failed; {p['errors']} errors"


def render(record):
    refs = '; '.join(f"{x['path']} (SHA256 {x['sha256'][:12]})" for x in record['evidence'])
    summary = (f"{record['summary']}; verification={record['verification']}; "
               f"scope={record['test_scope']}; latency_ms={record['latency_ms']}; evidence: {refs}")
    values = [record['date'], record['stage'], record['source_commit'][:12], record['environment'],
              record['fps'] if record['fps'] is not None else 'N/A', record['accuracy'],
              record['cma_free_kib'], pytest_text(record), summary]
    # Inline metadata preserves the Markdown table and enables fail-closed tag checks.
    metadata = json.dumps(record, ensure_ascii=True, separators=(',', ':'))
    metadata = metadata.replace('|', '\\u007c').replace('<', '\\u003c').replace('>', '\\u003e')
    return '| ' + ' | '.join(cell(v) for v in values) + f' <!-- milestone:{metadata} --> |\n'


def latest_record(ledger):
    lines = [line for line in ledger.read_text(encoding='utf8').splitlines()
             if line.startswith('| ') and line != HEADER and not re.match(r'^\|\s*-', line)]
    if not lines:
        raise ValueError('No recorded milestone; run record and commit the ledger first.')
    match = re.search(r'<!-- milestone:(.+) -->', lines[-1])
    if not match:
        raise ValueError('Latest row has no machine provenance; record a new validated milestone.')
    return json.loads(match.group(1))


def tag_plan(root, ledger, args):
    check_clean(root)
    if not re.fullmatch(r'v\d+\.\d+(?:\.\d+)?-[a-z0-9]+(?:-[a-z0-9]+)*', args.tag):
        raise ValueError('Tag must use vMAJOR.MINOR[.PATCH]-description, e.g. v1.1-fps-opt.')
    git(root, 'check-ref-format', 'refs/tags/' + args.tag)
    if git(root, 'tag', '--list', args.tag):
        raise ValueError('Tag already exists; never overwrite a milestone.')
    record = latest_record(ledger)
    counts = record['pytest']
    if record['verification'] != 'passed' or not counts['passed'] or counts['failed'] or counts['errors']:
        raise ValueError('Passing scoped validation and nonzero passing tests are required for tagging.')
    source = record['source_commit']
    git(root, 'merge-base', '--is-ancestor', source, 'HEAD')
    changed = git(root, 'diff', '--name-only', source, 'HEAD').splitlines()
    if any(path != LEDGER for path in changed):
        raise ValueError('Source changed after validation; run fresh tests and record a new milestone.')
    if git(root, 'ls-tree', source, '--', LEDGER):
        previous = subprocess.check_output(['git', '-C', str(root), 'show', f'{source}:{LEDGER}'])
        if not ledger.read_bytes().replace(b'\r\n', b'\n').startswith(previous.replace(b'\r\n', b'\n')):
            raise ValueError('Historical ledger content changed; only appending is allowed.')
    for item in record['evidence']:
        if digest(root / item['path']) != item['sha256']:
            raise ValueError('Evidence hash changed: ' + item['path'])
    head = git(root, 'rev-parse', 'HEAD')
    summary = (f"{record['stage']}: {record['summary']}\n\nTagged commit: {head}\n"
               f"Tested source commit: {source}\nEnvironment: {record['environment']}\n"
               f"FPS: {record['fps'] if record['fps'] is not None else 'N/A'}\nAccuracy: {record['accuracy']}\n"
               f"CMA Free (KiB): {record['cma_free_kib']}\nLatency (ms): {record['latency_ms']}\n"
               f"Pytest: {pytest_text(record)}\nScope: {record['test_scope']}\n"
               f"Ledger SHA256: {digest(ledger)}\nEvidence: {json.dumps(record['evidence'])}\n"
               'Offline results are not new board acceptance. Skips retain their documented limits.')
    commands = [['git', 'tag', '-a', args.tag, head, '-m', summary]]
    if args.push:
        git(root, 'remote', 'get-url', args.remote)
        commands.append(['git', 'push', args.remote, f'refs/tags/{args.tag}:refs/tags/{args.tag}'])
    return head, commands


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    rec = actions.add_parser('record', help='append metrics; commit the ledger separately afterwards')
    rec.add_argument('--stage', required=True)
    rec.add_argument('--environment', choices=['Offline', 'PYNQ-Z2'], required=True)
    rec.add_argument('--summary', required=True)
    rec.add_argument('--date', help='measurement/validation date YYYY-MM-DD; default today Asia/Shanghai')
    rec.add_argument('--evidence', action='append', required=True, help='report path; repeatable')
    rec.add_argument('--fps', type=float)
    rec.add_argument('--accuracy', default='N/A')
    rec.add_argument('--cma-free-kib', default='N/A', help='include before/allocated/released labels')
    rec.add_argument('--latency-ms', default='N/A', help='named stage/timing statistic')
    group = rec.add_mutually_exclusive_group()
    group.add_argument('--pytest-junit')
    group.add_argument('--pytest-passed', type=int)
    rec.add_argument('--pytest-skipped', type=int, default=0)
    rec.add_argument('--pytest-failed', type=int, help='explicit count required with --pytest-passed')
    rec.add_argument('--pytest-errors', type=int, help='explicit count required with --pytest-passed')
    rec.add_argument('--test-scope', required=True, help='test commands, skips and evidence limits')
    rec.add_argument('--tested-commit', help='must resolve to HEAD; caller binds fresh results to source')
    rec.add_argument('--verification', choices=['passed', 'failed', 'not-verified'], default='not-verified')
    tag = actions.add_parser('tag', help='annotated tag after committed ledger; never commits files')
    tag.add_argument('--tag', required=True)
    tag.add_argument('--push', action='store_true', help='explicitly push this tag only')
    tag.add_argument('--remote', default='origin')
    for action in (rec, tag):
        action.add_argument('--dry-run', action='store_true', help='preview without writes or push')
    args = parser.parse_args(argv)
    try:
        root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel')).resolve()
        ledger = root / LEDGER
        if not ledger.resolve().is_relative_to(root):
            raise ValueError('Ledger must remain within the repository.')
        if args.action == 'record':
            if args.fps is not None and (not math.isfinite(args.fps) or args.fps < 0):
                raise ValueError('FPS must be finite and nonnegative.')
            if any(v is not None and v < 0 for v in
                   (args.pytest_skipped, args.pytest_passed, args.pytest_failed, args.pytest_errors)):
                raise ValueError('Pytest counts cannot be negative.')
            if not args.dry_run:
                check_clean(root)
            record = make_record(root, args)
            row = render(record)
            old = ledger.read_bytes() if ledger.exists() else b''
            if old and HEADER not in old.decode('utf8'):
                raise ValueError('Unexpected ledger schema; preserve historical content and review manually.')
            print(json.dumps({'dry_run': args.dry_run, 'clean': not bool(git(root, 'status', '--porcelain=v1', '-uall')),
                              'record': record, 'row': row}, ensure_ascii=False, indent=2))
            if not args.dry_run:
                check_clean(root)
                if git(root, 'rev-parse', 'HEAD') != record['source_commit']:
                    raise ValueError('HEAD changed during preparation.')
                ledger.parent.mkdir(parents=True, exist_ok=True)
                with ledger.open('ab') as handle:
                    if not old:
                        handle.write(INITIAL.encode('utf8'))
                    elif not old.endswith(b'\n'):
                        handle.write(b'\n')
                    handle.write(row.encode('utf8'))
                print('Appended. Review and commit the ledger, then run tag --tag NAME --dry-run.')
        else:
            head, commands = tag_plan(root, ledger, args)
            print(json.dumps({'dry_run': args.dry_run, 'commands': commands}, ensure_ascii=False, indent=2))
            if not args.dry_run:
                check_clean(root)
                if git(root, 'rev-parse', 'HEAD') != head:
                    raise ValueError('HEAD changed before tagging.')
                for command in commands:
                    git(root, *command[1:])
    except (ValueError, OSError, ET.ParseError, KeyError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
