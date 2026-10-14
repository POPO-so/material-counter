import os,sys,json,time,hashlib,fcntl,importlib.util
from pathlib import Path
ROOT=Path('/home/xilinx/wo0013_level2_once_20261014_113914')
BOOT='a2fed90e-2394-472f-afcd-1422680fed9d'
def event(stage,**fields):
    row={'stage':stage,'monotonic_s':time.monotonic(),**fields}
    line=json.dumps(row)
    with (ROOT/'events.jsonl').open('a') as f:
        f.write(line+'\n');f.flush();os.fsync(f.fileno())
    print(line,flush=True)
def guard():
    assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()==BOOT,'Boot changed'
    assert Path('/sys/class/fpga_manager/fpga0/state').read_text().strip()=='operating','FPGA not operating'
    manifest=json.loads((ROOT/'manifest.json').read_text())
    for name,entry in manifest['artifacts'].items():
        if name not in ['wo0013_phase_a.bit','wo0013_phase_a.hwh','dma_replay_verify.py','load_reviewed_overlay_only.py']: continue
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==entry['sha256'],'Hash mismatch '+name
    findings=[];kernels=[];denied=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name)==os.getpid():continue
        try:
            cmd=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
            if 'ipykernel_launcher' in cmd:kernels.append(int(p.name))
            reasons=[]
            if any(x in cmd for x in ['fpgautil','openocd','xsdb','run_replay','board_session.py','load_reviewed_overlay_only.py']):reasons.append('hardware_command')
            maps=(p/'maps').read_text()
            if '/dev/mem' in maps or '/dev/xdevcfg' in maps:reasons.append('mapping')
            for fd in (p/'fd').iterdir():
                try:
                    target=os.readlink(fd)
                    if target in ['/dev/mem','/dev/xdevcfg','/dev/fpga0']:reasons.append('hardware_fd')
                except FileNotFoundError:pass
            if reasons:findings.append({'pid':int(p.name),'reasons':reasons})
        except FileNotFoundError:pass
        except PermissionError:denied.append(int(p.name))
    event('PROCESS_GUARD',findings=findings,kernels=kernels,denied=denied,boot_id=BOOT)
    assert not findings and not kernels and not denied,'Other hardware users or incomplete process audit'
def lock():
    f=(ROOT/'exclusive.lock').open('a');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);return f
def pl_state():
    import pynq
    path=Path(pynq.__file__).parent/'pl_server/global_pl_state_.json'
    return json.loads(path.read_text())
def verified_loaded():
    s=pl_state()
    assert s['bitfile_name']==str(ROOT/'wo0013_phase_a.bit'),'Overlay path changed'
    assert s['bitfile_hash']==hashlib.sha1((ROOT/'wo0013_phase_a.bit').read_bytes()).hexdigest(),'Overlay hash mismatch'
    event('LOADED_STATE_VERIFIED',state=s)
