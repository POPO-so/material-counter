"""Authorized PID6311 retirement and archive/reset of exactly its failed guard."""
old=deployment['old_config'];new=deployment['new_config']
pre=snapshot(old,previous_os)
os_gate(pre,old,allowed_owners=(6311,))
real_guard()
expected_guard=previous_failure['guard']
guard_unchanged(expected_guard)
status=pre['executor_status']
if (status['status']!='FAIL' or status['reason']!='CHILD_FAIL' or status['child_pid']!=6311
        or status['commands']!=['ENV','HEALTH','MIN_READ','INIT','SEND']
        or status['child_result']['error']!='TimeoutError: Python RUNSTOP polling deadline'
        or status['child_result']['send_attempts']!=0):
    raise RuntimeError('Not the explicitly authorized first-frame software failure')
events=[json.loads(s) for s in pre['hardware_events']]
if any(e.get('event')=='ACCESS_BEGIN' and e.get('operation') in ('write.SA','write.LENGTH') for e in events):
    raise RuntimeError('SA/LENGTH activity prevents authorized no-inflight cleanup')
if (GUARD/'send-attempt.json').exists():raise RuntimeError('Unexpected SEND marker')
buffer_events=[e for e in events if e.get('event')=='BUFFER_READY']
if len(buffer_events)!=1 or buffer_events[0]['bytes']!=230432:
    raise RuntimeError('Old buffer evidence differs')
old_proc=proc_identity(6311)
reference=previous_failure['hardware_process_records']['6311']
ref_fields=reference['stat'][reference['stat'].rfind(')')+2:].split()
expected_argv=[s.encode() for s in reference['cmdline'].rstrip('\0').split('\0')]
cma_before=cma();pidfd=None
if old_proc['state'] not in ('ABSENT','Z'):
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):
        raise RuntimeError('pidfd supervision required for controlled cleanup')
    pidfd=os.pidfd_open(6311)
    current=proc_identity(6311)
    if current['starttime']!=ref_fields[19] or current['argv']!=expected_argv:
        raise RuntimeError('PID identity changed; do not signal')
    if Path(os.readlink('/proc/6311/exe')).resolve()!=Path(expected_argv[0].decode()).resolve():
        raise RuntimeError('Old executable differs')
    signal.pidfd_send_signal(pidfd,signal.SIGSTOP)
    until=time.monotonic()+3
    while proc_identity(6311)['state'] not in ('T','t'):
        if time.monotonic()>=until:raise RuntimeError('Old child did not stop')
        time.sleep(.02)
    current=proc_identity(6311)
    if current['starttime']!=ref_fields[19] or current['argv']!=expected_argv:
        raise RuntimeError('Stopped process identity differs')
elif old_proc['state']=='Z' and (old_proc['starttime']!=ref_fields[19] or old_proc['fds'] or old_proc['maps']):
    raise RuntimeError('Zombie identity or resources differ')
archive=Path('/home/xilinx/wo0014_pg021_archive_20261015')
archive.mkdir(mode=0o700)
(archive/'before.json').write_text(json.dumps(pre,indent=2))
(archive/'process-before.json').write_text(json.dumps(serialize_proc(old_proc),indent=2))
(archive/'cma-before.json').write_text(json.dumps(cma_before))
shutil.copytree(GUARD,archive/'guard-before')
shutil.copytree(Path(old['remote_evidence_directory']),archive/'old-evidence-before')
guard_unchanged(expected_guard)
if pidfd is not None:
    signal.pidfd_send_signal(pidfd,signal.SIGKILL)
    if not select.select([pidfd],[],[],3)[0]:raise RuntimeError('Old process exit not confirmed')
    os.close(pidfd)
limit=time.monotonic()+5
while True:
    current=proc_identity(6311)
    if current['state'] in ('ABSENT','Z') and not current['fds'] and not current['maps']:
        break
    if time.monotonic()>=limit:raise RuntimeError('Old FD/maps remain')
    time.sleep(.05)
lock=(GUARD/'exclusive.lock').open('a')
limit=time.monotonic()+5
while True:
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
    except BlockingIOError:
        if time.monotonic()>=limit:raise RuntimeError('Old lock not released')
        time.sleep(.05)
guard_unchanged(expected_guard)
minimum_kib=math.ceil(230432/os.sysconf('SC_PAGE_SIZE'))*os.sysconf('SC_PAGE_SIZE')//1024
cma_samples=[];limit=time.monotonic()+5
while True:
    cma_after=cma();cma_samples.append({'monotonic_s':time.monotonic(),**cma_after})
    released_kib=cma_after['CmaFree_kib']-cma_before['CmaFree_kib']
    if old_proc['state'] in ('ABSENT','Z') or released_kib>=minimum_kib:break
    if time.monotonic()>=limit:
        raise RuntimeError('CMA free accounting did not recover the old allocation')
    time.sleep(.05)
post=snapshot(old,pre);os_gate(post,old)
guard_unchanged(expected_guard)
shutil.copytree(Path(old['remote_evidence_directory']),archive/'old-evidence-final')
GUARD.rename(archive/'guard-final')
GUARD.mkdir(mode=0o700)
cleanup={'pid':6311,'process_after':serialize_proc(current),'cma_before':cma_before,
         'cma_after':cma_after,'cma_free_increase_kib':released_kib,
         'old_packet_allocation_rounded_kib':minimum_kib,'signal_method':'pidfd stop then kill' if pidfd is not None else 'already inactive',
         'cma_release_samples':cma_samples,
         'sa_writes':0,'length_writes':0,'post_no_owners':True}
(archive/'cleanup.json').write_text(json.dumps(cleanup,indent=2))
archive_record={'boot_id':BOOT,'authorization':'2026-10-15 captain PG021 software-failure recovery',
                'files':{str(p.relative_to(archive)):digest(p) for p in archive.rglob('*') if p.is_file()}}
(archive/'archive-manifest.json').write_text(json.dumps(archive_record,indent=2))
target=Path(new['executor_directory'])
if target.parent!=Path('/home/xilinx'):raise RuntimeError('Invalid deployment target')
target.mkdir(mode=0o755)
for name,entry in new['expected_files'].items():
    if Path(name).name!=name:raise RuntimeError('Invalid deployment file name')
    data=zlib.decompress(base64.b64decode(deployment['changed_files'][name])) if name in deployment['changed_files'] else (Path(old['executor_directory'])/name).read_bytes()
    if len(data)!=entry['bytes'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:
        raise RuntimeError('Deployment differs from tested bytes '+name)
    with (target/name).open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
new_post=snapshot(new,post);os_gate(new_post,new)
if list(GUARD.iterdir()):raise RuntimeError('New guard unexpectedly consumed')
print('ACTION_JSON '+json.dumps({'status':'PASS','action':'cleanup-deploy','post':new_post,
    'cleanup':cleanup,'archive':archive_record,'archive_encoding':'zlib-base64',
    'archive_bytes':archive_payload(archive)}),flush=True)
