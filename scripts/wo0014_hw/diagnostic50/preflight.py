"""Local-only release gate: run regressions, verify sources, emit BOARD_READY."""
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from core import BUNDLE_FILES, base, validate_50

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE_OUT = ROOT / 'data/results/wo0014-diagnostic50-offline-20261014'
OUT = ROOT / 'data/results/wo0014-diagnostic50-hardening-20261014' / ('run-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
EXPORT = ROOT / 'data/results/wo0014-hw-50mhz-20261014/export'


def require(value, message):
    if not value:
        raise RuntimeError(message)


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    result = {'status': 'PRECHECK_FAIL', 'hardware_attempted': False,
              'overlay_loads': 0, 'dma_sends': 0, 'test_suites': []}
    try:
        preserved = json.loads((BASE_OUT / 'preserved-baseline.json').read_text(encoding='utf-8-sig'))
        for entry in preserved:
            require(base._sha256_file(Path(entry['Path'])) == entry['Sha256'],
                    'Original or protected file changed: ' + entry['Path'])
        protected = json.loads((ROOT / 'data/results/wo0014-offline-20261014/protected-before.json').read_text())
        for name, digest in protected.items():
            require(base._sha256_file(ROOT / name) == digest, 'Existing protection baseline differs: ' + name)
        require(base._sha256_file(HERE / 'baseline_pynq_image_once.py') ==
                base._sha256_file(HERE.parent / 'pynq_image_once.py'), 'Baseline is not byte-identical')
        plan = base.validate_inputs(EXPORT / 'wo0014_image.bit', EXPORT / 'wo0014_image.hwh',
                                    EXPORT / 'rgb_packet.bin', EXPORT / 'pair-manifest.json')
        validate_50(plan)
        warning_audit = {}
        for name in ('drc.rpt', 'methodology.rpt'):
            source = EXPORT.parent / 'reports' / name
            text = source.read_text(encoding='utf-8')
            rows = re.findall(r'^\|\s*([A-Z]+[A-Z0-9-]*)\s*\|\s*(Warning|Advisory)\s*\|[^\n]*\|\s*(\d+)\s*\|', text, re.M)
            warning_audit[name] = {rule: {'severity': severity, 'count': int(count)} for rule, severity, count in rows}
            shutil.copyfile(source, OUT / name)
        require(sum(v['count'] for v in warning_audit['drc.rpt'].values() if v['severity'] == 'Warning') == 14,
                'DRC warning audit changed')
        require(sum(v['count'] for v in warning_audit['drc.rpt'].values() if v['severity'] == 'Advisory') == 2,
                'DRC advisory audit changed')
        require(warning_audit['methodology.rpt'] == {'LUTAR-1': {'severity': 'Warning', 'count': 1}},
                'Methodology audit changed')
        for name, digest in plan.manifest['rtl_sha256'].items():
            current = ROOT / ('fpga/rtl' if name in ('axis_gray2binary.v', 'axis_rgb2gray.v')
                              else 'fpga/experimental/wo0014') / name
            snapshot = EXPORT.parent / 'rtl' / name
            require(base._sha256_file(current) == digest == base._sha256_file(snapshot),
                    'RTL protocol/snapshot changed: ' + name)
        suites = {
            'diagnostic': ['scripts/wo0014_hw/diagnostic50'],
            'legacy-image-once': ['scripts/wo0014_hw/test_pynq_image_once.py'],
            'wo0014-vectors': ['python/refmodel/experiments/wo0014/test_image_vectors.py'],
            'wo0013-ddr': ['data/results/wo0013-pagemap-policy-20261014-120205/test_ddr_validate.py'],
        }
        source_before = {p.name: base._sha256_file(p) for p in HERE.glob('*.py')}
        for name, paths in suites.items():
            fixtures = (OUT / ('fixtures-' + name)).resolve()
            require(fixtures.parent == OUT.resolve(), 'Unexpected test fixture cleanup target')
            command = [sys.executable, '-m', 'pytest', '-v', '--basetemp', str(fixtures), *paths]
            done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                  encoding='utf-8', timeout=60)
            (OUT / (name + '-stdout.log')).write_text(done.stdout, encoding='utf-8')
            (OUT / (name + '-stderr.log')).write_text(done.stderr, encoding='utf-8')
            result['test_suites'].append({'name': name, 'command': command, 'exit_code': done.returncode})
            require(done.returncode == 0, 'Regression suite failed: ' + name)
        require(source_before == {p.name: base._sha256_file(p) for p in HERE.glob('*.py')},
                'Executor/tests changed during verification')
        (OUT / 'source-sha256.json').write_text(json.dumps(source_before, indent=2) + '\n', encoding='utf-8')
        # Run local syntax checks, including the Linux-only child without import.
        import ast
        for path in HERE.glob('*.py'):
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        local_modules = {p.stem: p.name for p in HERE.glob('*.py')}
        dependencies = {}
        for name in BUNDLE_FILES:
            tree = ast.parse((HERE / name).read_text(encoding='utf-8'))
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split('.')[0])
            dependencies[name] = sorted(imports)
            for module in imports:
                if module in local_modules:
                    require(local_modules[module] in BUNDLE_FILES, 'Local dependency missing from bundle: ' + module)
                else:
                    require(module in sys.stdlib_module_names or module in ('pynq', 'numpy'),
                            'Undeclared runtime dependency: ' + module)
        (OUT / 'dependency-closure.json').write_text(json.dumps(dependencies, indent=2) + '\n', encoding='utf-8')
        bundle = {'schema': 1, 'clock_mhz': 50,
                  'files': {name: base._sha256_file(HERE / name) for name in BUNDLE_FILES}}
        (HERE / 'executor-manifest.json').write_text(json.dumps(bundle, indent=2) + '\n', encoding='utf-8')
        (OUT / 'dry-run.json').write_text(json.dumps(base._dry_run_evidence(plan), indent=2) + '\n', encoding='utf-8')
        result.update(status='OFFLINE_STAGE_READY', precheck='PASS', clock_mhz=50,
                      bit_sha256=plan.bit_sha256, hwh_sha256=plan.hwh_sha256,
                      packet_sha256=plan.packet_sha256, packet_bytes=len(plan.packet),
                      input_checksum=plan.input_byte_checksum, binary_checksum=plan.expected_binary_checksum,
                      executor_manifest_sha256=base._sha256_file(HERE / 'executor-manifest.json'),
                      protected_unchanged=True, original_script_unchanged=True,
                      warning_audit=warning_audit,
                      board_environment_verified=False, board_authorization_required=True,
                      full_execution_ready=False,
                      readiness_scope='Independent stages only; native authentication and manual external health remain live prerequisites')
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    (OUT / 'precheck.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'OFFLINE_STAGE_READY' else 2


if __name__ == '__main__':
    raise SystemExit(run())
