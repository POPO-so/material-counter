"""Create a hash-bound immutable package; no network or board operations."""
import ast
import hashlib
import json
import zipfile
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
CODE = ROOT / 'scripts/wo0014_hw'
sys.path.insert(0, str(CODE / 'diagnostic50'))
import core
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
packets = json.loads((OUT / 'packets.json').read_text())
new_names = ('closeout_worker.py','closeout_child.py','closeout_session.py')
old_names = ('baseline_pynq_image_once.py','core.py','child.py','session.py','supervisor.py','deadline.py',
             'authentication.py','executor-manifest.json')
members = {'closeout/'+n:CODE/'closeout'/n for n in new_names}
members.update({'diagnostic50/'+n:CODE/'diagnostic50'/n for n in old_names})
pins = {n:sha(CODE/'closeout'/n) for n in new_names}
pins.update({'diagnostic50/'+n:sha(CODE/'diagnostic50'/n) for n in old_names})
profiles = {}
for clock in (50,100):
    export = ROOT / f'data/results/wo0014-hw-{clock}mhz-20261014/export'
    pair = json.loads((export/'pair-manifest.json').read_text())
    assert pair['clock_mhz'] == clock
    if clock == 100:
        assert pair['implemented_timing']['wns'] == 1.167 and pair['implemented_timing']['tns'] == 0
        report = (export.parent/'reports/timing-implemented.rpt').read_text()
        assert '      1.167        0.000' in report
    profile = {'clock_mhz':clock,'build_id':pair['build_id']}
    for field,name in (('bit','wo0014_image.bit'),('hwh','wo0014_image.hwh'),('pair_manifest','pair-manifest.json')):
        path = f'artifacts{clock}/{name}'
        profile[field] = {'path':path,'sha256':sha(export/name)}
        members['closeout/'+path] = export/name
    profiles[str(clock)] = profile
    for packet in packets:
        plan = core.base.validate_inputs(export/'wo0014_image.bit',export/'wo0014_image.hwh',
                                         OUT/packet['file'],export/'pair-manifest.json')
        assert plan.expected_binary_checksum == packet['binary_checksum']
        assert plan.packet_sha256 == packet['sha256']
specs = [{'frame_id':r['frame_id'],'packet':{'path':'packets/'+r['file'],'sha256':r['sha256']},
          'foreground_pixels':r['foreground_pixels']} for r in packets]
manifest = {'schema':1,'boot_id':'52955bb3-db18-4505-b209-78cbf3969d89',
            'parent_50_bit_sha256':core.BIT_SHA,
            'parent_50_executor_manifest_sha256':sha(CODE/'diagnostic50/executor-manifest.json'),
            'source_sha256':pins,'profiles':profiles,'baseline_frame':specs[0],
            'modes':{'overlay100':{'packets':[]},'single100':{'packets':[specs[0]]},
                     'multi50':{'packets':specs[1:]},'multi100':{'packets':specs[1:]}}}
(OUT/'closeout-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
members['closeout/closeout-manifest.json'] = OUT/'closeout-manifest.json'
members.update({'closeout/packets/'+r['file']:OUT/r['file'] for r in packets})
for name,p in members.items():
    if p.suffix == '.py': ast.parse(p.read_text(encoding='utf-8'))
with zipfile.ZipFile(OUT/'wo0014-closeout-20261015.zip','w',compression=zipfile.ZIP_DEFLATED) as package:
    for name,p in members.items(): package.writestr(name,p.read_bytes())
config = {'schema':1,'package_files':{n:sha(p) for n,p in members.items()},
          'zip_sha256':sha(OUT/'wo0014-closeout-20261015.zip'),
          'manifest_sha256':sha(OUT/'closeout-manifest.json'),
          'git_baseline': '5a153c4c91bc985882853c39c24b52c3130bd7a2',
          'target':'/home/xilinx/wo0014_closeout_20261015',
          'previous_os':json.loads((ROOT/'data/results/wo0014-pg021-ready-20261015/dma-once-result.json').read_text())['post'],
          'deployment':json.loads((ROOT/'data/results/wo0014-pg021-ready-20261015/deployment-config.json').read_text())}
(OUT/'package-config.json').write_text(json.dumps(config,indent=2)+'\n')
print(json.dumps({'status':'LOCAL_PACKAGE_READY','files':len(members),'zip_bytes':(OUT/'wo0014-closeout-20261015.zip').stat().st_size,
                  'profile50':profiles['50'],'profile100':profiles['100']},indent=2))
