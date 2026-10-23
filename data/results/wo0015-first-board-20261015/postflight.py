"""One read-only exit audit after boot gate rejection; no stage retry."""
import ast
import base64
import getpass
import hashlib
import json
from pathlib import Path
import subprocess
import traceback
import controller as t

OUT = Path(__file__).resolve().parent

def main():
    with (OUT/'postflight-attempt.json').open('x') as f:
        json.dump({'read_only':True,'stage_retry':False},f)
    if hashlib.sha256(t.KNOWN.read_bytes()).hexdigest()!=t.KNOWN_SHA:
        raise RuntimeError('known_hosts changed')
    password=getpass.getpass('EXIT AUDIT SSH password (hidden; no hardware action): ')
    sudo_password=getpass.getpass('EXIT AUDIT sudo password (hidden): ')
    client=t.paramiko.SSHClient()
    client.load_host_keys(str(t.KNOWN))
    client.set_missing_host_key_policy(t.paramiko.RejectPolicy())
    client.connect('192.168.2.99',username='xilinx',password=password,
                   allow_agent=False,look_for_keys=False,timeout=5,banner_timeout=10,auth_timeout=15,
                   disabled_algorithms={'keys':['ssh-rsa','ecdsa-sha2-nistp256','ecdsa-sha2-nistp384','ecdsa-sha2-nistp521']})
    del password
    key=client.get_transport().get_remote_server_key()
    fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
    if fingerprint!=t.FINGERPRINT:raise RuntimeError('host fingerprint mismatch')
    source_path=OUT.parents[2]/'scripts/wo0015/board_preflight.py'
    original=source_path.read_text(encoding='utf-8')
    tree=ast.parse(original)
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='audit_process_ownership')
    audit=ast.get_source_segment(original,function)
    source='from __future__ import annotations\nimport os,json,subprocess\nfrom pathlib import Path\nclass PreflightError(RuntimeError):pass\n'+audit+'\n'+'''
def read(p):
    try:return Path(p).read_text().strip()
    except OSError as e:return str(e)
try:ownership=audit_process_ownership()
except Exception as e:ownership={'error':str(e)}
mem={k:int(v.split()[0]) for k,v in (l.split(':',1) for l in read('/proc/meminfo').splitlines()) if k.startswith('Cma')}
r={'boot_id':read('/proc/sys/kernel/random/boot_id'),'meminfo':mem,'ownership':ownership,
'manager_state':read('/sys/class/fpga_manager/fpga0/state'),'manager_flags':read('/sys/class/fpga_manager/fpga0/flags'),
'remote_bundle_directory_exists':Path('/home/xilinx/wo0015_first_board_20261015').exists(),
'dmesg':subprocess.run(['dmesg','--color=never'],capture_output=True,text=True).stdout,
'guard_files':{str(p):read(p) for p in Path('/var/lib/wo0015-s2mm').glob('*') if p.is_file()}}
print(json.dumps(r),flush=True)
'''
    (OUT/'postflight-remote-readonly.py').write_text(source,encoding='utf-8')
    rc,data=t.execute(client,sudo_password,t.sudo_python(source),'postflight-readonly')
    client.close()
    if rc:raise RuntimeError('exit audit failed')
    snapshot=json.loads(data)
    snapshot['strict_host_fingerprint']=fingerprint
    (OUT/'postflight-snapshot.json').write_text(json.dumps(snapshot,indent=2),encoding='utf-8')
    ping=subprocess.run(['ping','-n','2','-w','1000','192.168.2.99'],capture_output=True)
    (OUT/'post-ping.txt').write_bytes(ping.stdout+ping.stderr)
    (OUT/'postflight-result.json').write_text(json.dumps({'status':'READONLY_EXIT_AUDIT_COMPLETE','ping_exit_code':ping.returncode,'known_hosts_unchanged':hashlib.sha256(t.KNOWN.read_bytes()).hexdigest()==t.KNOWN_SHA},indent=2),encoding='utf-8')
    print('Read-only exit audit complete; no hardware was accessed.',flush=True)

if __name__=='__main__':
    try:main()
    except BaseException:
        (OUT/'postflight-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
    input('Press Enter to close this audit console: ')
