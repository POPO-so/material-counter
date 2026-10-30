import sys,json,subprocess,time
from pathlib import Path
root=Path('/home/xilinx/standalone-g25-20261030')
commands=root/'g3-replay-commands.json'
assert not commands.exists()
commands.write_text(json.dumps({'80':['pause'],'101':['clear'],'160':['pause'],'170':['background'],
                              '190':['page'],'500':['clear','page'],'501':['background']}))
base=[sys.executable,'-B','-m','scripts.standalone.integrated_run','--execute',
 '--manifest',str(root/'export/display-manifest.json'),'--manifest-sha256','2872e1598b3c3ab7ccb5d383f5cee6a8822dbd91eb0f198098eca067489a2966',
 '--boot-id','e48ff153-bd85-4dd6-98f6-438bd51da08d','--native-lib',str(root/'native/rle_components_v2.so'),
 '--source','replay','--duration','180','--frames','2400','--commands',str(commands)]
for display in ('on','off'):
 out=root/('g3-replay-'+display+'.json');assert not out.exists()
 command=base+['--display',display,'--output',str(out)]
 print(json.dumps({'case':display,'command':command,'started_monotonic':time.monotonic()}),flush=True)
 process=subprocess.Popen(command,cwd=root)
 while process.poll() is None:
  time.sleep(.5)
  if out.exists() and json.loads(out.read_text())['state']!='STOPPED':
   print('REPLAY_FAIL_HOLD '+out.read_text()[-2000:],flush=True)
   while True:time.sleep(1)
 assert process.returncode==0
 r=json.loads(out.read_text());assert r['state']=='STOPPED'
 print(json.dumps({k:r[k] for k in ('state','processed','submitted','duration_s','total_count')}),flush=True)
print('G3_REPLAY_PAIR_FINISHED',flush=True)
