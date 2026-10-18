"""OS-gated deployment or one closeout mode, inside native authenticated sudo."""
import zipfile
import io

cfg = deployment['new_config']
pre = snapshot(cfg, previous_os)
expected_before = expected_50 if mode in ('preflight', 'deploy', 'multi50', 'overlay100') else expected_100

def live_gate(report, expected_bit):
    if (report['boot_id'] != BOOT or report['fpga_state'] != 'operating'
            or report['fpga_flags'] != '0' or not report['kernel_continuity']
            or report['new_kernel_warnings'] or report['process_audit']['denied']
            or report['process_audit']['conflicts']
            or report['overlay_identity']['record'].get('shutdown_ips') != {}
            or not report['overlay_identity']['sha1_record_match']
            or report['overlay_identity']['bit_sha256'] != expected_bit
            or report['pynq_source_hashes'] != cfg['pynq_source_pins']):
        raise RuntimeError('Live OS/boot/kernel/ownership/identity/source gate failed')
    for name, entry in cfg['expected_files'].items():
        if any(report['files'][name][k] != entry[k] for k in ('bytes', 'sha256')):
            raise RuntimeError('Parent reviewed deployment changed: ' + name)
    if (GUARD / 'terminal-fail.json').exists():
        raise RuntimeError('Parent terminal failure blocks all closeout actions')

live_gate(pre, expected_before)
if mode == 'preflight':
    if target.exists() or Path('/home/xilinx/wo0014-closeout-20261015.zip').exists():
        raise RuntimeError('New deployment/upload path already exists; no overwrite')
    print('ACTION_JSON ' + json.dumps({'status':'PASS','action':'preflight','pre':pre,'post':pre}),flush=True)
elif mode == 'deploy':
    fixed_members = {'closeout/'+n for n in ('closeout_worker.py','closeout_child.py','closeout_session.py','closeout-manifest.json')}
    fixed_members.update({'diagnostic50/'+n for n in ('baseline_pynq_image_once.py','core.py','child.py','session.py',
                                                    'supervisor.py','deadline.py','authentication.py','executor-manifest.json')})
    fixed_members.update({f'closeout/artifacts{clock}/{name}' for clock in (50,100)
                          for name in ('wo0014_image.bit','wo0014_image.hwh','pair-manifest.json')})
    fixed_members.update({f'closeout/packets/frame-{frame}.bin' for frame in range(4)})
    if set(package_files) != fixed_members:
        raise RuntimeError('Fixed reviewed package member set differs')
    source = Path('/home/xilinx/wo0014-closeout-20261015.zip')
    if digest(source) != zip_sha:
        raise RuntimeError('Transferred package SHA differs')
    if target.exists():
        raise RuntimeError('Deployment path already exists, no overwrite')
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            dest = target / info.filename
            if (info.is_dir() or not dest.resolve().is_relative_to(target.resolve())
                    or info.filename not in package_files):
                raise RuntimeError('Unexpected ZIP member')
            data = archive.read(info)
            if hashlib.sha256(data).hexdigest() != package_files[info.filename]:
                raise RuntimeError('ZIP member hash differs')
        if set(archive.namelist()) != set(package_files):
            raise RuntimeError('ZIP member set differs')
        target.mkdir()
        for info in archive.infolist():
            dest = target / info.filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open('xb') as stream:
                stream.write(archive.read(info)); stream.flush(); os.fsync(stream.fileno())
    for name, expected in package_files.items():
        if digest(target / name) != expected:
            raise RuntimeError('Deployed file SHA differs')
    post = snapshot(cfg, pre); live_gate(post, expected_before)
    print('ACTION_JSON ' + json.dumps({'status':'PASS','action':'deploy','pre':pre,'post':post,
                                      'deployed_files':package_files,'zip_sha256':zip_sha}), flush=True)
else:
    for name, expected in package_files.items():
        if digest(target / name) != expected:
            raise RuntimeError('Deployed bytes changed')
    evidence = target / ('evidence-' + mode)
    if evidence.exists():
        raise RuntimeError('One-shot evidence already exists')
    before_cma = cma()
    args = [PYTHON, '-u', str(target / 'closeout/closeout_session.py'), '--mode',mode,
            '--execute','--authorize-stage',mode,'--manifest-sha256',manifest_sha,
            '--evidence-dir',str(evidence)]
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=dict(os.environ, XILINX_XRT='/usr'))
    output = {'stdout':bytearray(), 'stderr':bytearray()}
    def drain(pipe, name):
        for line in iter(pipe.readline, b''):
            output[name].extend(line)
            if name == 'stderr':
                sys.stderr.buffer.write(line); sys.stderr.flush()
    for pipe, name in ((process.stdout,'stdout'),(process.stderr,'stderr')):
        threading.Thread(target=drain,args=(pipe,name),daemon=True).start()
    limit = time.monotonic() + 110
    while process.poll() is None:
        status_path = evidence / 'status.json'
        if status_path.is_file():
            try: status = json.loads(status_path.read_text())
            except json.JSONDecodeError: status = {}
            if status.get('status') == 'FAIL' or status.get('state') == 'FAIL':
                print('ACTION_FAILED_HOLD ' + json.dumps(status),flush=True)
                while True: time.sleep(1)
        if time.monotonic() >= limit:
            print('ACTION_FAILED_HOLD outer deadline, child retained',flush=True)
            while True: time.sleep(1)
        time.sleep(.05)
    status = json.loads((evidence / 'status.json').read_text())
    if process.returncode or status.get('status') != 'PASS':
        print('ACTION_FAILED_HOLD supervisor did not PASS',flush=True)
        while True: time.sleep(1)
    expected_after = expected_50 if mode == 'multi50' else expected_100
    post = snapshot(cfg, pre); live_gate(post, expected_after)
    after_cma = cma()
    if mode != 'overlay100' and after_cma != before_cma:
        raise RuntimeError('CMA accounting changed after successful DMA/free')
    files = {str(p.relative_to(target)):base64.b64encode(zlib.compress(p.read_bytes())).decode()
             for p in evidence.rglob('*') if p.is_file()}
    guard = GUARD / 'closeout-20261015'
    files.update({str(p.relative_to(GUARD)):base64.b64encode(zlib.compress(p.read_bytes())).decode()
                  for p in guard.rglob('*') if p.is_file()})
    print('ACTION_JSON ' + json.dumps({'status':'PASS','action':mode,'pre':pre,'post':post,
                                      'supervisor':status,'cma_before':before_cma,'cma_after':after_cma,
                                      'evidence_bytes':files,'stdout':output['stdout'].decode(errors='replace'),
                                      'stderr':output['stderr'].decode(errors='replace')}),flush=True)
