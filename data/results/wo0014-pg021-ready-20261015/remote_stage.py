"""Exactly one reviewed session stage; failure retains the actual child/session."""
cfg=deployment['new_config']
pre=snapshot(cfg,previous_os);os_gate(pre,cfg)
sys.path.insert(0,cfg['executor_directory'])
import session
session.require_secure_session()
if session.verify_local()!=cfg['manifest_sha']:raise RuntimeError('Runtime manifest differs')
if mode!='overlay-only':receipt_gate(mode,cfg)
evidence=Path(cfg['remote_evidence_directory'])
if evidence.exists():raise RuntimeError('Stage evidence directory already consumed')
args=[PYTHON,'-u',str(Path(cfg['executor_directory'])/'session.py'),
      '--mode',mode,'--execute','--authorize-stage',mode,
      '--executor-manifest-sha',cfg['manifest_sha'],'--evidence-dir',str(evidence)]
print('STAGE_ENTRY_BEGIN '+mode,flush=True)
process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                         env=dict(os.environ,XILINX_XRT='/usr'))
def drain(pipe,dest):
    for line in iter(pipe.readline,b''):
        dest.buffer.write(line);dest.flush()
for pipe,dest in ((process.stdout,sys.stdout),(process.stderr,sys.stderr)):
    threading.Thread(target=drain,args=(pipe,dest),daemon=True).start()
deadline=time.monotonic()+100
while process.poll() is None:
    status_path=evidence/'status.json'
    if status_path.is_file():
        try:status=json.loads(status_path.read_text())
        except json.JSONDecodeError:status={}
        if status.get('status')=='FAIL':
            print('ACTION_FAILED_HOLD '+json.dumps(status),flush=True)
            while True:time.sleep(1)
    if time.monotonic()>=deadline:
        print('ACTION_FAILED_HOLD '+mode+' outer collection deadline; child retained',flush=True)
        while True:time.sleep(1)
    time.sleep(.05)
code=process.returncode
status=json.loads((evidence/'status.json').read_text()) if (evidence/'status.json').is_file() else {}
if code or status.get('status')!='PASS':
    print('ACTION_FAILED_HOLD '+mode+' executor failed',flush=True)
    while True:time.sleep(1)
post=snapshot(cfg,pre);os_gate(post,cfg)
expected={'mode':mode,'manifest_sha256':cfg['manifest_sha']}
if json.loads((GUARD/('supervision-'+mode+'-pass.json')).read_text())!=expected:
    raise RuntimeError('Final supervision PASS receipt missing')
print('ACTION_JSON '+json.dumps({'status':'PASS','action':mode,'pre':pre,'post':post}),flush=True)
