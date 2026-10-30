import sys,subprocess,json,time
from pathlib import Path
root=Path('/home/xilinx/standalone-g25-20261030')
boot='e48ff153-bd85-4dd6-98f6-438bd51da08d'
base=[sys.executable,'-B','-m','scripts.standalone.g25_run','--execute',
 '--manifest',str(root/'export/display-manifest.json'),'--manifest-sha256','2872e1598b3c3ab7ccb5d383f5cee6a8822dbd91eb0f198098eca067489a2966',
 '--boot-id',boot,'--native-lib',str(root/'native/rle_components_v2.so')]
cases=[('high-pixels','high_runs','scan',3,True),
       ('moving-off','moving','off',35,False),('moving-scan','moving','scan',35,False),
       ('high-off','high_runs','off',35,False),('high-scan','high_runs','scan',35,False),
       ('moving-dynamic','moving','dynamic',35,False)]
for name,pattern,display,duration,verify in cases:
 output=root/('g25-'+name+'.json')
 assert not output.exists(),output
 command=base+['--output',str(output),'--pattern',pattern,'--display',display,'--duration',str(duration)]
 if verify:command+=['--verify-pixels']
 print(json.dumps(dict(case=name,command=command,started_monotonic=time.monotonic())),flush=True)
 process=subprocess.Popen(command,cwd=root)
 while process.poll() is None:
  time.sleep(.5)
  if output.exists():
   report=json.loads(output.read_text())
   if report['state']=='FAIL_HOLD':
    print('MATRIX_FAIL_HOLD '+name+' '+report.get('error',''),flush=True)
    while True:time.sleep(1)
 if process.returncode!=0:raise RuntimeError('case failed '+name)
 report=json.loads(output.read_text())
 assert report['state']=='STOPPED'
 print(json.dumps(dict(case=name,result='STOPPED',frames=report['processed_frames'],fps=report['processing_fps'])),flush=True)
print('G25_MATRIX_FINISHED',flush=True)
