"""Start only the preflight standby server; browser authentication starts hardware."""
import json
import os
from pathlib import Path
import sys

root = Path('/home/xilinx/wo0017/formal-b1-20261022-01')
assert os.geteuid() == 0
marker = root / 'standby-launch-once.json'
fd = os.open(str(marker), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write('{"standby_launch_claimed":true,"hardware_started":false}\n')
    stream.flush()
    os.fsync(stream.fileno())
argv = json.loads((root / 'runner-argv.json').read_text())
os.execv(sys.executable, [sys.executable, '-u', str(root / 'app/operator_gate_launcher.py'),
         '--attempt-dir', str(root), '--host', '127.0.0.1', '--control-port', '8081',
         '--handoff-timeout-s', '3600', '--standby-before-preflight', '--'] + argv)
