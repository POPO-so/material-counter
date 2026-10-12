"""Run the bounded RGB-to-gray work package via the existing Ross stdio MCP.

No configuration/install changes. Raw MCP requests/responses are preserved.
Usage: .venv/Scripts/python.exe fpga/scripts/run_rgb2gray_mcp.py --phase red|verify|synth
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

REPO = Path(__file__).resolve().parents[2]

class Ross:
    def __init__(self, root):
        import tomllib
        cfg = tomllib.loads((REPO / '.codex/config.toml').read_text(encoding='utf-8'))['mcp_servers']['vivado-mcp']
        env = dict(os.environ, **cfg.get('env', {}))
        self.log = (root / 'mcp-transcript.jsonl').open('w', encoding='utf-8')
        self.stderr = (root / 'mcp-stderr.log').open('w', encoding='utf-8')
        # Own isolated state; never attach to or stop another user's session.
        self.proc = subprocess.Popen([cfg['command'], '--stdio', '--state-dir=' + str(root / 'state')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            text=True, encoding='utf-8', env=env, cwd=root)
        self.q = queue.Queue()
        self.ident = 0
        threading.Thread(target=self.reader, daemon=True).start()
        self.call('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
            'clientInfo': {'name': 'material-counter-rgb2gray', 'version': '1.0'}})
        self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        schema = self.call('tools/list', {})
        (root / 'mcp-tools.json').write_text(json.dumps(schema, indent=2), encoding='utf-8')
        self.schemas = {t['name']: t['inputSchema'] for t in schema['tools']}

    def reader(self):
        for line in self.proc.stdout:
            try:
                self.q.put(json.loads(line))
            except json.JSONDecodeError:
                self.q.put({'unparsed': line})

    def send(self, msg):
        self.log.write(json.dumps({'direction': 'request', 'message': msg}) + '\n')
        self.log.flush()
        self.proc.stdin.write(json.dumps(msg) + '\n')
        self.proc.stdin.flush()

    def call(self, method, params):
        self.ident += 1
        ident = self.ident
        self.send({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params})
        while True:
            reply = self.q.get(timeout=360)
            self.log.write(json.dumps({'direction': 'response', 'message': reply}) + '\n')
            self.log.flush()
            if reply.get('id') == ident:
                if 'error' in reply:
                    raise RuntimeError(reply['error'])
                return reply['result']

    def tool(self, name, **args):
        if name not in self.schemas:
            raise RuntimeError('Tool unavailable: ' + name)
        required = self.schemas[name].get('required', [])
        if any(k not in args for k in required):
            raise RuntimeError('Missing required arguments: ' + name)
        result = self.call('tools/call', {'name': name, 'arguments': args})
        parsed = result.get('structuredContent')
        if parsed is None:
            for block in result.get('content', []):
                if block.get('type') == 'text':
                    try:
                        parsed = json.loads(block['text'])
                        break
                    except json.JSONDecodeError:
                        pass
        parsed = parsed or result
        print(name + ': ' + json.dumps(parsed, ensure_ascii=False)[:1600], flush=True)
        return parsed

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        self.log.close()
        self.stderr.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--phase', choices=['red', 'verify', 'synth'], default='verify')
    ap.add_argument('--vectors', type=Path)
    args = ap.parse_args()
    root = REPO / 'data/results' / ('rgb2gray-' + args.phase + '-' + time.strftime('%Y%m%d-%H%M%S'))
    root.mkdir(parents=True)
    print('EVIDENCE_DIR=' + str(root), flush=True)
    client = Ross(root)
    sid = None
    try:
        started = client.tool('vivado_start', working_dir=str(root), session_type='general', gui_mode=False)
        sid = started.get('session_id')
        if not sid:
            raise RuntimeError('No session_id: ' + str(started))
        def execute(cmd):
            result = client.tool('vivado_execute', session_id=sid, command=cmd, verbosity='full')
            if result.get('status') == 'running' or result.get('error_type') == 'STILL_RUNNING_DETACHED':
                # Do not resubmit an in-flight command. Respect the server handoff.
                for _ in range(20):
                    time.sleep(30)
                    status = client.tool('vivado_status', session_id=sid, action='session')
                    if status.get('is_command_running') is False:
                        if status.get('last_completed_code') != 0:
                            raise RuntimeError('Detached command failed: ' + str(status))
                        return status
                raise RuntimeError('Vivado handoff did not complete within 10 minutes')
            if result.get('exit_code', 0) != 0 or result.get('isError'):
                raise RuntimeError('Vivado command failed: ' + str(result))
            if 'SKIPPED (duplicate)' in result.get('output', ''):
                raise RuntimeError('MCP duplicate protection skipped the command')
            return result
        execute('version -short')
        r = REPO.as_posix()
        out = root.as_posix()
        execute(f'create_project gray_pkg {{{out}/project}} -part xc7z020clg400-1')
        if args.phase == 'synth':
            execute(f'add_files {{{r}/fpga/rtl/axis_rgb2gray.v}}; set_property top axis_rgb2gray [get_filesets sources_1]; update_compile_order -fileset sources_1')
            execute('synth_design -top axis_rgb2gray -part xc7z020clg400-1 -mode out_of_context')
            execute('create_clock -name aclk -period 10 [get_ports aclk]')
            execute(f'report_utilization -file {{{out}/utilization.rpt}}; report_timing_summary -file {{{out}/timing-summary.rpt}}')
            client.tool('vivado_log_messages', session_id=sid, filter='all', max_messages=500)
            (root / 'source-hashes.json').write_text(json.dumps({str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [REPO/'fpga/rtl/axis_rgb2gray.v', REPO/'fpga/sim/tb_axis_rgb2gray.sv']}, indent=2), encoding='utf-8')
            print('MCP_SYNTH_CHECKS_FINISHED', flush=True)
            return
        execute(f'set_property target_simulator XSim [current_project]; add_files {{{r}/fpga/rtl/axis_stream_skeleton.v}}; add_files -fileset sim_1 {{{r}/fpga/sim/tb_axis_stream_skeleton.sv}}; set_property top tb_axis_stream_skeleton [get_filesets sim_1]; set_property xsim.simulate.runtime 10us [get_filesets sim_1]; update_compile_order -fileset sources_1; update_compile_order -fileset sim_1')
        execute('launch_simulation -simset sim_1 -mode behavioral')
        import shutil
        baseline = root / 'sim-skeleton'
        baseline.mkdir()
        baseline_logs = list((root / 'project/gray_pkg.sim/sim_1/behav/xsim').glob('*.log'))
        for log in baseline_logs:
            shutil.copy2(log, baseline / log.name)
        baseline_text = '\n'.join(p.read_text(encoding='utf-8', errors='replace') for p in baseline_logs)
        if 'PASS: in=8 out=8' not in baseline_text or 'Fatal:' in baseline_text or 'ERROR:' in baseline_text:
            raise RuntimeError('Skeleton regression failed')
        execute('close_sim -force')
        execute(f'add_files -fileset sim_1 {{{r}/fpga/sim/tb_axis_rgb2gray.sv}}; set_property top tb_axis_rgb2gray [get_filesets sim_1]; update_compile_order -fileset sim_1')
        if args.phase == 'red':
            # Expected failing elaboration: module does not exist yet.
            result = client.tool('vivado_execute', session_id=sid, command='launch_simulation -simset sim_1 -mode behavioral; puts {RGB2GRAY_RED}', verbosity='full')
            (root / 'red-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            return
        if not args.vectors:
            raise RuntimeError('--vectors is required for verify')
        vectors = args.vectors.resolve()
        manifest = json.loads((vectors / 'manifest.json').read_text(encoding='utf-8'))
        n = manifest['beats']
        execute(f'add_files {{{r}/fpga/rtl/axis_rgb2gray.v}}; set_property top axis_rgb2gray [get_filesets sources_1]; update_compile_order -fileset sources_1')
        for mode in (0, 1):
            v = vectors.as_posix()
            # Leading whitespace keeps set_property from parsing the value as an option.
            execute(f'set_property xsim.simulate.xsim.more_options {{ -testplusarg N={n} -testplusarg READY_MODE={mode} -testplusarg RGB={v}/vectors_rgb.hex -testplusarg EXPECTED={v}/expected_gray.hex -testplusarg OUTPUT={out}/output-mode-{mode}.hex}} [get_filesets sim_1]; set_property xsim.simulate.runtime 10ms [get_filesets sim_1]')
            execute(f'launch_simulation -simset sim_1 -mode behavioral; puts {{RGB2GRAY_MODE_{mode}}}')
            simdir = root / 'project/gray_pkg.sim/sim_1/behav/xsim'
            logs = list(simdir.glob('*.log'))
            saved = root / ('sim-mode-' + str(mode))
            saved.mkdir()
            import shutil
            for log in logs:
                shutil.copy2(log, saved / log.name)
            text = '\n'.join(log.read_text(encoding='utf-8', errors='replace') for log in logs)
            if 'TEST_PASS' not in text or 'Fatal:' in text or 'ERROR:' in text:
                raise RuntimeError('Simulation did not satisfy terminal PASS gate')
            execute(f'close_sim -force; puts {{CLOSED_GRAY_MODE_{mode}}}')
        subprocess.run([sys.executable, str(REPO/'python/refmodel/check_rgb2gray_output.py'), '--vectors', str(vectors), '--run', str(root)], check=True)
        execute(f'synth_design -top axis_rgb2gray -part xc7z020clg400-1 -lint -file {{{out}/lint.rpt}}')
        subprocess.run([sys.executable, str(REPO/'.agents/skills/vivado-rtl-lint/parse_lint_report.py'), str(root/'lint.rpt'), str(root/'lint-parsed.csv')], check=True)
        execute('synth_design -top axis_rgb2gray -part xc7z020clg400-1 -mode out_of_context')
        execute('create_clock -name aclk -period 10 [get_ports aclk]')
        execute(f'report_utilization -file {{{out}/utilization.rpt}}; report_timing_summary -file {{{out}/timing-summary.rpt}}')
        client.tool('vivado_log_messages', session_id=sid, filter='all', max_messages=500)
        hashes = {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [REPO/'fpga/rtl/axis_rgb2gray.v', REPO/'fpga/sim/tb_axis_rgb2gray.sv']}
        (root / 'source-hashes.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
        print('MCP_WORK_PACKAGE_CHECKS_FINISHED', flush=True)
    finally:
        if sid:
            client.tool('vivado_stop', session_id=sid)
        client.close()

if __name__ == '__main__':
    main()
