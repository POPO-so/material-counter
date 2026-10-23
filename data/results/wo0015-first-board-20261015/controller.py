"""Transport only: native hidden credentials, strict SSH pin, manual one-shot requests."""
import base64
import getpass
import hashlib
import json
from pathlib import Path
import shlex
import sys
import time
import traceback

OUT = Path(__file__).resolve().parent
sys.path.insert(0, r'C:\fpga\material-counter\data\results\wo0013-level2-20261014-020118\ssh-runtime')
import paramiko

KNOWN = Path.home()/'.ssh/known_hosts'
KNOWN_SHA = '367d661467a1e161f6af95d83631e09cd16d9e9e3286d486298e2b48282034ea'
FINGERPRINT = 'SHA256:TB0FfGjfMzgZUv/reJgTpeq/AM68sMCpGC5xm6Pit7o'
BOOT = 'cb087bbb-932d-4a6e-9600-98dd1c7e5f8d'
REMOTE = '/home/xilinx/wo0015_first_board_20261015'
PY = '/usr/local/share/pynq-venv/bin/python'

def event(status, **fields):
    row = dict(status=status, time_ns=time.time_ns(), **fields)
    (OUT/'status.json').write_text(json.dumps(row, indent=2), encoding='utf-8')
    with (OUT/'events.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(row)+'\n')
    print(status, flush=True)

def execute(client, password, command, name, timeout=45):
    with (OUT/(name+'-dispatch.json')).open('x', encoding='utf-8') as f:
        json.dump({'command': command, 'single_attempt': True}, f, indent=2)
    event('DISPATCH', name=name)
    stdin, stdout, stderr = client.exec_command(command, timeout=10)
    if command.startswith('sudo '):
        stdin.write(password+'\n'); stdin.flush()
    stdin.close()
    channel = stdout.channel
    start = time.monotonic()
    chunks = bytearray()
    with (OUT/(name+'-stdout.txt')).open('xb', buffering=0) as out, (OUT/(name+'-stderr.txt')).open('xb', buffering=0) as err:
        while True:
            if channel.recv_ready():
                data = channel.recv(65536); out.write(data); chunks.extend(data)
            if channel.recv_stderr_ready():
                err.write(channel.recv_stderr(65536))
            if b'FAIL_HOLD' in chunks or time.monotonic()-start > timeout:
                event('FAIL_HOLD_TRANSPORT_RETAINED', name=name, elapsed_s=time.monotonic()-start)
                # Never terminate a possibly DMA-owning remote process or its SSH transport.
                while True: time.sleep(1)
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            time.sleep(.03)
    rc = channel.recv_exit_status()
    result = {'exit_code': rc, 'elapsed_s': time.monotonic()-start}
    (OUT/(name+'-transport.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    event('COMMAND_EXIT', name=name, **result)
    return rc, bytes(chunks)

def sudo_python(source):
    encoded = base64.b64encode(source.encode()).decode()
    program = 'import base64;exec(compile(base64.b64decode('+repr(encoded)+'),"wo0015-readonly-or-unpack","exec"))'
    return 'sudo -S -p "" '+PY+' -I -S -B -c '+shlex.quote(program)

def main():
    with (OUT/'controller-attempt.json').open('x') as f:
        json.dump({'single_connection_attempt': True, 'boot_id': BOOT}, f)
    if hashlib.sha256(KNOWN.read_bytes()).hexdigest() != KNOWN_SHA:
        raise RuntimeError('known_hosts file changed')
    event('AWAITING_NATIVE_HIDDEN_CREDENTIALS')
    password = getpass.getpass('SSH password for xilinx@192.168.2.99 (hidden): ')
    sudo_password = getpass.getpass('sudo password (hidden): ')
    client = paramiko.SSHClient()
    client.load_host_keys(str(KNOWN))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    event('CONNECT_BEGIN')
    client.connect('192.168.2.99', username='xilinx', password=password,
                   allow_agent=False, look_for_keys=False, timeout=5,
                   banner_timeout=10, auth_timeout=15,
                   disabled_algorithms={'keys': ['ssh-rsa', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521']})
    del password
    transport = client.get_transport()
    actual = 'SHA256:'+base64.b64encode(hashlib.sha256(transport.get_remote_server_key().asbytes()).digest()).decode().rstrip('=')
    if actual != FINGERPRINT: raise RuntimeError('strict host fingerprint mismatch')
    event('STRICT_SSH_AUTHENTICATED', fingerprint=actual)
    source = '''import json,os,subprocess
from pathlib import Path
def read(p):
    try:return Path(p).read_text().strip()
    except OSError as e:return str(e)
mem={k:int(v.split()[0]) for k,v in (l.split(':',1) for l in read('/proc/meminfo').splitlines()) if k.startswith('Cma')}
r={'boot_id':read('/proc/sys/kernel/random/boot_id'),'meminfo':mem,'uname':os.uname()[:],
'manager_state':read('/sys/class/fpga_manager/fpga0/state'),'manager_flags':read('/sys/class/fpga_manager/fpga0/flags'),
'python_exists':Path('/usr/local/share/pynq-venv/bin/python').is_file(),
'processes':subprocess.run(['ps','-eo','pid,ppid,stat,comm,args'],capture_output=True,text=True).stdout,
'dmesg':subprocess.run(['dmesg','--color=never'],capture_output=True,text=True).stdout,
'guard_files':{str(p):read(p) for p in Path('/var/lib/wo0015-s2mm').glob('*') if p.is_file()}}
print(json.dumps(r),flush=True)
'''
    rc, data = execute(client, sudo_password, sudo_python(source), 'initial-readonly')
    if rc: raise RuntimeError('read-only prerequisite command failed')
    initial = json.loads(data)
    (OUT/'initial-snapshot.json').write_text(json.dumps(initial, indent=2), encoding='utf-8')
    if initial['boot_id'] != BOOT: raise RuntimeError('boot_id mismatch; no hardware access')
    if not initial['python_exists']: raise RuntimeError('approved interpreter missing')
    if initial['guard_files']: raise RuntimeError('existing WO0015 guard state; no retry')
    event('READONLY_BOOT_GATE_PASS_AWAITING_MANAGER')
    seen = set()
    while True:
        for path in sorted(OUT.glob('request-[0-9][0-9][0-9].json')):
            if path.name in seen: continue
            seen.add(path.name)
            task = json.loads(path.read_text(encoding='utf-8-sig'))
            name = path.stem
            if task['op'] == 'close':
                client.close(); event('CLOSED_NO_HARDWARE_HANDLES'); return
            if task['op'] == 'upload':
                local = Path(task['path'])
                if hashlib.sha256(local.read_bytes()).hexdigest() != task['sha256']:
                    raise RuntimeError('local ZIP mismatch')
                with client.open_sftp() as sftp:
                    sftp.mkdir(REMOTE)
                    sftp.put(str(local), REMOTE+'/bundle.zip')
                source = 'from pathlib import Path\nimport hashlib,zipfile,json\np=Path('+repr(REMOTE)+')\nz=p/"bundle.zip"\nassert hashlib.sha256(z.read_bytes()).hexdigest()=='+repr(task['sha256'])+'\nwith zipfile.ZipFile(z) as a:\n assert all((p/n).resolve().is_relative_to(p.resolve()) for n in a.namelist())\n a.extractall(p)\nprint(json.dumps({n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in ("run-manifest.json","wo15.bit","wo15.hwh")}),flush=True)\n'
                rc, _ = execute(client, sudo_password, sudo_python(source), name)
            elif task['op'] == 'command':
                rc, _ = execute(client, sudo_password, task['command'], name, task.get('timeout',45))
            elif task['op'] == 'download':
                with client.open_sftp() as sftp:
                    for remote, filename in task['files'].items():
                        sftp.get(remote, str(OUT/filename))
                rc = 0
            else: raise RuntimeError('unsupported transport request')
            (OUT/(name+'-done.json')).write_text(json.dumps({'exit_code':rc}), encoding='utf-8')
            if rc:
                event('FAIL_STOPPED_NO_MORE_STAGE_COMMANDS', name=name)
                # Permit evidence downloads and a read-only OS snapshot after failure.
                while True:
                    recovery = OUT/'failure-evidence-request.json'
                    if recovery.exists():
                        task=json.loads(recovery.read_text(encoding='utf-8-sig'))
                        execute(client,sudo_password,task['command'],'failure-readonly')
                        with client.open_sftp() as sftp:
                            for remote,filename in task.get('files',{}).items():
                                sftp.get(remote,str(OUT/filename))
                        client.close();event('FAIL_STOPPED_EVIDENCE_SAVED');return
                    time.sleep(.2)
            event('REQUEST_DONE_AWAITING_MANAGER', name=name)
        time.sleep(.2)

if __name__ == '__main__':
    try: main()
    except BaseException as exc:
        (OUT/'controller-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        event('FAIL_STOPPED', error=type(exc).__name__+': '+str(exc))
    input('Evidence console finished. Press Enter to close: ')
