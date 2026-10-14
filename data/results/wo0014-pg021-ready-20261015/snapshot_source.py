def kernel_increment(before, after, anchor_lines=8):
    """Locate the exact previous tail once; head eviction is normal for dmesg.

    A missing or ambiguous anchor cannot establish continuity and fails closed.
    At least the last eight lines must survive (all lines for short snapshots).
    """
    if not before or not after:
        raise RuntimeError('kernel continuity lost: empty snapshot')
    anchor = before[-anchor_lines:]
    matches = [i for i in range(len(after) - len(anchor) + 1)
               if after[i:i + len(anchor)] == anchor]
    if len(matches) != 1:
        raise RuntimeError('kernel continuity lost: tail anchor missing or ambiguous')
    return after[matches[0] + len(anchor):]


"""Read-only OS/files before/after authorized diagnostic50; no PYNQ import."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def regular(p):
    if p.is_symlink() or not stat.S_ISREG(p.stat().st_mode): raise RuntimeError('Not regular: '+str(p))
    return {'bytes':p.stat().st_size,'sha256':sha(p),'mtime_ns':p.stat().st_mtime_ns}

def processes():
    selected=[];conflicts=[];denied=[]
    devs=('/dev/mem','/dev/xdevcfg','/dev/fpga','/dev/uio','/dev/xlnk','/dev/xrt','/dev/dri')
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name)==os.getpid(): continue
        try:
            comm=(p/'comm').read_text().strip()
            cmd=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
            maps=(p/'maps').read_text()
            mapped=[s for s in maps.splitlines() if any(d in s for d in devs)]
            fds=[]
            for fd in (p/'fd').iterdir():
                try:
                    dest=os.readlink(fd)
                    if any(dest.startswith(d) for d in devs): fds.append(dest)
                except FileNotFoundError: pass
            # An idle default Jupyter server is permitted. Active hardware owners are not.
            reasons=[]
            if mapped or fds: reasons.append('hardware_fd_or_mapping')
            if any(w in cmd for w in ('fpgautil','openocd','xsdb','remote-send-once','run_replay')): reasons.append('hardware_command')
            cats=[w for w in ('python','jupyter','notebook','ipykernel','dma','fpga') if w in comm.lower() or w in cmd.lower()]
            row={'pid':int(p.name),'comm':comm,'categories':cats,'fds':fds,'maps':mapped,'reasons':reasons}
            if cats or reasons: selected.append(row)
            if reasons: conflicts.append(row)
        except FileNotFoundError: pass
        except PermissionError: denied.append(int(p.name))
    return {'selected':selected,'conflicts':conflicts,'denied':denied}

target=Path(config['executor_directory'])
files={n:regular(target/n) for n in config['expected_files']}
file_match=all(files[n]['bytes']==v['bytes'] and files[n]['sha256']==v['sha256'] for n,v in config['expected_files'].items())
boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
uptime=Path('/proc/uptime').read_text().strip()
fpga=Path('/sys/class/fpga_manager/fpga0/state').read_text().strip()
flags=Path('/sys/class/fpga_manager/fpga0/flags').read_text().strip()
root=Path('/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq')
pl=root/'pl_server/global_pl_state_.json'
if pl.is_symlink(): raise RuntimeError('Symlink state refused')
try:
    state_text=pl.read_text(encoding='utf-8')
except FileNotFoundError:
    state_text=None
stored=json.loads(state_text) if state_text is not None else {}
if not isinstance(stored,dict):raise RuntimeError('Malformed GlobalState object')
if state_text is not None and not isinstance(stored.get('shutdown_ips'),dict):raise RuntimeError('Malformed or missing shutdown_ips')
bit=Path(stored['bitfile_name']) if 'bitfile_name' in stored else None
identity={'record_path':str(pl),'record_exists':state_text is not None,'record_sha256':sha(pl) if state_text is not None else None,'record':stored,'bit_path':str(bit) if bit else None,
          'bit_sha256':sha(bit) if bit and bit.is_file() else None,'bit_sha1':hashlib.sha1(bit.read_bytes()).hexdigest() if bit and bit.is_file() else None,
          'status':'COLD_BOOT_NO_GLOBAL_STATE' if state_text is None else 'GLOBAL_STATE_PRESENT'}
identity['sha1_record_match']=bit is not None and identity['bit_sha1']==stored.get('bitfile_hash')
logs=subprocess.run(['dmesg','--color=never'],capture_output=True,text=True,timeout=5,check=True).stdout.splitlines()
previous=baseline.get('kernel_lines',[])
try:
    delta=kernel_increment(previous,logs) if previous else []
    continuity=True
except RuntimeError as exc:
    continuity=False
    delta=[str(exc)]
warnings=[s for s in delta if any(w in s.lower() for w in ('warn','error','oops','panic','fault','hung','timeout','continuity lost'))] if previous else []
guardroot=Path('/var/lib/wo0014-diagnostic50')
guard={p.name:{**regular(p),'text':p.read_text(errors='replace')} for p in guardroot.iterdir() if p.is_file()} if guardroot.exists() else {}
audit=processes()
source_hashes={n:sha(root/n) for n in config['pynq_source_pins']}
output={'phase':phase,'boot_id':boot,'uptime':uptime,'fpga_state':fpga,'fpga_flags':flags,
        'kernel_lines':logs,'kernel_continuity':continuity,'kernel_delta':delta,'new_kernel_warnings':warnings,
        'files':files,'overlay_identity':identity,'guard':guard,'process_audit':audit,'pynq_source_hashes':source_hashes,
        'captured_unix_ns':time.time_ns(),'pynq_imported':False}
output['baseline_checks']={
    'new_boot_id':boot!=config['old_boot_id'],
    'recent_boot':boot==config['confirmed_boot_id'] or 0<float(uptime.split()[0])<=3600,
    'manager':fpga=='operating' and flags=='0',
    'no_hardware_owners':not audit['conflicts'] and not audit['denied'],
    'shutdown_safe':not identity['record_exists'] or stored.get('shutdown_ips')=={}}
output['baseline_pass']=all(output['baseline_checks'].values())
if phase=='pre':
    output['execution_checks']={'deployed_bundle_and_artifacts':file_match,
        'pynq_unchanged':source_hashes==config['pynq_source_pins'],
        'guard_unconsumed':not any(n.endswith('.json') for n in guard),
        'record_file_consistent':not identity['record_exists'] or identity['sha1_record_match'],
        'kernel':continuity and not warnings,
        'same_boot_if_recheck':not baseline or boot==baseline['boot_id'],
        'same_record_if_recheck':not baseline or identity['record_sha256']==baseline['overlay_identity']['record_sha256'],
        'evidence_directory_unused':not Path(config['remote_evidence_directory']).exists()}
    output['env_pass']=output['baseline_pass'] and all(output['execution_checks'].values())
elif phase=='post':
    output['post_pass']=(boot==baseline['boot_id'] and fpga=='operating' and flags=='0' and continuity
                         and not warnings and not audit['conflicts'] and not audit['denied']
                         and identity['sha1_record_match'] and stored.get('shutdown_ips')=={}
                         and identity['bit_sha256']==config['expected_files']['wo0014_image.bit']['sha256'])
    evidence=Path(config['remote_evidence_directory'])
    output['executor_status']=json.loads((evidence/'status.json').read_text()) if (evidence/'status.json').is_file() else None
    for key,name in [('supervisor_events','supervisor-events.jsonl'),('child_stdout','child-stdout.log'),('child_stderr','child-stderr.log')]:
        f=evidence/name;output[key]=f.read_text(errors='replace') if f.is_file() else ''
    events=guardroot/'events.jsonl'
    output['hardware_events']=events.read_text().splitlines() if events.is_file() else []
else: raise RuntimeError('Unexpected phase')
if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=boot:raise RuntimeError('Boot changed while collecting')
# Caller stores the complete output in a private namespace and emits one result.
