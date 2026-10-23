"""Replay collected metadata locally; no additional board call or allocation."""
import json,re,sys
from pathlib import Path
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT.parents[2]/'scripts/wo0015'))
import board_preflight as pre
r=json.loads((OUT/'board-cma-probe.json').read_text(encoding='utf-8'))
mem={line.split(':')[0]:int(line.split(':')[1].split()[0]) for line in r['meminfo']['stdout'].splitlines()}
result={'boot_id':r['boot_id_after'],'evaluation':'local replay of captured metadata only',
'CmaTotal_kib':mem['CmaTotal'],'CmaFree_kib':mem['CmaFree'],
'physical_cma_range_known':False,'measured_cma_ranges':None,
'system_ram_ranges':[list(pair) for pair in pre._system_ram_intervals(r['iomem']['stdout'])],
'cmdline_has_cma_argument':bool(re.search(r'(?:^|\s)cma=',r['cmdline']['stdout'])),
'bootargs_has_cma_argument':bool(re.search(r'(?:^|\s)cma=',r['chosen_bootargs']['stdout'])),
'debugfs_cma_exists':r['debug_cma']['exists'],'board_accessed_by_replay':False}
try:
    admission=pre.resolve_cma_sources(meminfo=mem,cmdline=r['cmdline']['stdout'],
        bootargs=r['chosen_bootargs']['stdout'],dmesg_text=r['dmesg_filtered']['stdout'],
        iomem_text=r['iomem']['stdout'])
    result.update({'decision':'ADMITTED',**admission.as_evidence()})
except pre.PreflightError as exc:
    result.update({'decision':'REJECTED_MISSING_CMA_SOURCE','error':str(exc),
                   'retry3_runtime_eligible':False})
assert result['decision']=='REJECTED_MISSING_CMA_SOURCE'
(OUT/'captured-runtime-admission.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
