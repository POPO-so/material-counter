"""Board-side OS evidence helpers; no PYNQ import or hardware access."""
import base64
import fcntl
import hashlib
import json
import math
import os
import select
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

BOOT='52955bb3-db18-4505-b209-78cbf3969d89'
GUARD=Path('/var/lib/wo0014-diagnostic50')
PYTHON='/usr/local/share/pynq-venv/bin/python'

def snapshot(config, baseline):
    # Isolate exec globals: snapshot_source contains its own audit variables.
    namespace={'config':config,'baseline':baseline,'phase':'post'}
    exec(compile(snapshot_source,'read-only-os-snapshot','exec'),namespace)
    output=namespace['output']
    print('OS_SNAPSHOT '+json.dumps({k:output[k] for k in ('boot_id','fpga_state','fpga_flags','kernel_continuity','new_kernel_warnings')}),flush=True)
    return output

def os_gate(report, config, allowed_owners=()):
    if (report['boot_id']!=BOOT or report['fpga_state']!='operating' or report['fpga_flags']!='0'
            or not report['kernel_continuity'] or report['new_kernel_warnings']
            or report['overlay_identity']['record'].get('shutdown_ips')!={}
            or not report['overlay_identity']['sha1_record_match']
            or report['overlay_identity']['bit_sha256']!=config['expected_files']['wo0014_image.bit']['sha256']
            or report['process_audit']['denied']
            or any(p['pid'] not in allowed_owners for p in report['process_audit']['conflicts'])
            or report['pynq_source_hashes']!=config['pynq_source_pins']):
        raise RuntimeError('OS/boot/kernel/identity/exclusive health gate failed')
    for name,entry in config['expected_files'].items():
        if any(report['files'][name][k]!=entry[k] for k in ('bytes','sha256')):
            raise RuntimeError('Reviewed files changed '+name)

def real_guard():
    if not stat.S_ISDIR(GUARD.lstat().st_mode):
        raise RuntimeError('Guard must be a real non-symlink directory')
    lock=GUARD/'exclusive.lock'
    if not stat.S_ISREG(lock.lstat().st_mode):
        raise RuntimeError('Guard lock must be regular, not a symlink')

def digest(path):
    if not stat.S_ISREG(path.lstat().st_mode):raise RuntimeError('Not a regular file '+str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()

def guard_unchanged(expected):
    if {p.name for p in GUARD.iterdir()}!=set(expected):
        raise RuntimeError('Guard file set changed')
    if any(digest(GUARD/n)!=entry['sha256'] for n,entry in expected.items()):
        raise RuntimeError('Guard bytes changed')

def cma():
    text=Path('/proc/meminfo').read_text()
    values={line.split(':')[0]:int(line.split()[1]) for line in text.splitlines() if line.startswith(('CmaTotal:','CmaFree:'))}
    if set(values)!={'CmaTotal','CmaFree'}:raise RuntimeError('CMA accounting unavailable')
    return {'CmaTotal_kib':values['CmaTotal'],'CmaFree_kib':values['CmaFree']}

def proc_identity(pid):
    p=Path('/proc')/str(pid)
    if not p.exists():return {'pid':pid,'state':'ABSENT','fds':[],'maps':''}
    text=(p/'stat').read_text()
    fields=text[text.rfind(')')+2:].split()
    return {'pid':pid,'state':fields[0],'starttime':fields[19],
            'argv':(p/'cmdline').read_bytes().rstrip(b'\0').split(b'\0'),
            'fds':[os.readlink(fd) for fd in (p/'fd').iterdir()],
            'maps':(p/'maps').read_text(),'stat':text,'status':(p/'status').read_text()}

def serialize_proc(row):
    return {**row,'argv':[x.decode(errors='replace') for x in row.get('argv',[])]}

def archive_payload(folder):
    return {str(p.relative_to(folder)):base64.b64encode(zlib.compress(p.read_bytes())).decode()
            for p in folder.rglob('*') if p.is_file()}

def receipt_gate(mode, config):
    for prior in (['overlay-only'] if mode=='mmio-read-only' else ['overlay-only','mmio-read-only']):
        expected={'mode':prior,'manifest_sha256':config['manifest_sha']}
        if json.loads((GUARD/('supervision-'+prior+'-pass.json')).read_text())!=expected:
            raise RuntimeError('Previous successful supervision missing')
