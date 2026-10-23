"""CMA metadata only: no PYNQ import, device open, allocation, or state change."""
import json,subprocess
from pathlib import Path
BOOT='822a9c29-37c0-438c-81c4-da998d57073b'
def textfile(path):
    try:return {'stdout':Path(path).read_bytes().replace(b'\0',b'').decode('utf-8','replace'),'error':None}
    except OSError as e:return {'stdout':'','error':str(e)}
def command(argv):
    p=subprocess.run(argv,capture_output=True,text=True)
    return {'argv':argv,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
before=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
assert before==BOOT,'boot mismatch; stop metadata probe'
r={'boot_id_before':before,'cmdline':textfile('/proc/cmdline'),
'chosen_bootargs':textfile('/proc/device-tree/chosen/bootargs'),
'dmesg_filtered':command(['sh','-c','dmesg | grep -iE "cma|memory|reserved"']),
'iomem':textfile('/proc/iomem'),
'meminfo':command(['sh','-c','grep -E "^Cma(Total|Free):" /proc/meminfo']),
'page_size':command(['getconf','PAGESIZE']),
'debug_cma':{'path':'/sys/kernel/debug/cma','exists':Path('/sys/kernel/debug/cma').exists(),'areas':{}},
'prohibited_operations':{'pynq_import':0,'overlay_load':0,'device_open':0,'cma_allocation':0,'dma_start':0}}
root=Path('/sys/kernel/debug/cma')
if root.is_dir():
    r['debug_cma']['entries']=sorted(p.name for p in root.iterdir())
    for area in sorted(root.iterdir()):
        if area.is_dir():
            r['debug_cma']['areas'][area.name]={n:textfile(area/n) for n in ('base_pfn','count','used','order_per_bit') if (area/n).is_file()}
r['boot_id_after']=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
assert r['boot_id_after']==BOOT,'boot changed during metadata probe'
print(json.dumps(r,indent=2),flush=True)
