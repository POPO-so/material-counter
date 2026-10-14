exec(compile(open('/home/xilinx/wo0013_level2_once_20261014_113914/remote-common.py').read(),'remote-common','exec'))
handle=lock();guard();verified_loaded()
sys.path.insert(0,'/home/xilinx/wo0013_pagemap_policy_20261014_120205')
exec(compile(open('/home/xilinx/wo0013_pagemap_policy_20261014_120205/runtime-buffer-check.py').read(),'runtime-buffer-check','exec'))
from pynq import MMIO,allocate
import numpy as np
spec=importlib.util.spec_from_file_location('reviewed_replay',ROOT/'dma_replay_verify.py')
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
payload=module.make_tb_payload();expected=module.summarize_payload(payload)
assert len(payload)==76816 and expected.beat_count==19204
assert hashlib.sha256(payload).hexdigest()=='0daefac7dfdf155e3b901a9a40d52025f24f0b9764c45f889abd747083c195c0'
event('EXPECTED_FIXTURE',values=vars(expected))
dma=MMIO(0x40400000,0x2c);monitor=MMIO(0x43C10000,0x28)
buffer=None;started=False;safe_release=False
try:
    # RTL control bit 0 clears all observations and arms the sink; no ap_ctrl protocol.
    initial={name:dma.read(offset) for name,offset in [('dmacr',0),('dmasr',4),('source_address',0x18),('length',0x28)]}
    event('DMA_INITIAL_RAW',registers=initial)
    expected_register=monitor.read(0x24)
    event('EXPECTED_LENGTH_RAW',value=expected_register)
    assert expected_register==76816
    assert not(initial['dmacr'] & 5) and initial['dmasr'] & 1 and not(initial['dmasr'] & 0x7078) and initial['length']==0,'DMA initial state not halted/clean: stop'
    monitor.write(0,1)
    armed={name:monitor.read(offset) for name,offset in [('status',4),('byte_count',8),('beat_count',12),('first_word',16),('last_word',20),('tlast_count',24),('checksum',28),('error_code',32)]}
    event('MONITOR_ARMED_RAW',registers=armed)
    assert armed['status']==1 and all(v==0 for k,v in armed.items() if k!='status'),'Monitor failed to arm cleanly'
    buffer=allocate(shape=(76816,),dtype=np.uint8)
    buffer[:]=np.frombuffer(payload,dtype=np.uint8)
    address=module._buffer_device_address(buffer)
    module.validate_dma_source_alignment(address)
    event('ALLOCATED_FOR_SINGLE_DMA',physical_address=address,nbytes=int(buffer.nbytes),alignment=address%8)
    sync_method=module._sync_buffer_to_device(buffer)
    assert bytes(buffer)==payload,'CPU payload readback differs'
    evidence=buffer_evidence(buffer)
    event('BUFFER_ACCESSIBILITY_PASS',evidence=evidence)
    maps=Path('/proc/self/maps').read_text()
    event('BUFFER_DRM_MAPPING',mappings=[line for line in maps.splitlines() if '/dev/dri/' in line])
    event('BUFFER_SYNCED',address=address,bytes=76816,sync_method=sync_method,coherent=bool(buffer.coherent))
    dma.write(0,initial['dmacr']|1)
    deadline=time.monotonic()+1
    while True:
        sr=dma.read(4)
        if sr & module.DMA_SR_ERROR_MASK:raise RuntimeError('DMA start error '+hex(sr))
        if not(sr&1):break
        if time.monotonic()>deadline:raise TimeoutError('DMA start timeout')
        time.sleep(.001)
    event('DMA_RUNNING_BEFORE_LENGTH',dmasr=sr)
    dma.write(0x18,address)
    with (Path('/home/xilinx/wo0013_pagemap_policy_20261014_120205')/'dma-send-once.json').open('x') as f:
        json.dump({'length':76816,'address':address,'boot_id':BOOT},f);f.flush();os.fsync(f.fileno())
    event('BEFORE_SINGLE_LENGTH_WRITE',length=76816)
    started=True
    start=time.monotonic();dma.write(0x28,76816)
    event('SINGLE_LENGTH_WRITE_RETURNED')
    deadline=start+5
    while True:
        sr=dma.read(4);ms=monitor.read(4)
        event('POLL',dmasr=sr,monitor_status=ms)
        if sr & module.DMA_SR_ERROR_MASK:raise RuntimeError('DMA error '+hex(sr))
        if ms & 4:raise RuntimeError('Monitor error '+hex(ms))
        if sr&2 and ms&2:break
        if time.monotonic()>deadline:raise TimeoutError('MM2S completion timeout')
        time.sleep(.001)
    safe_release=True
    snapshot={'status':ms,'expected_bytes':expected_register}
    for name,offset in [('byte_count',8),('beat_count',12),('first_word',16),('last_word',20),('tlast_count',24),('checksum',28),('error_code',32)]:snapshot[name]=monitor.read(offset)
    final={name:dma.read(offset) for name,offset in [('dmacr',0),('dmasr',4),('source_address',0x18),('length',0x28)]}
    event('FINAL_RAW',dma=final,monitor=snapshot,expected_bytes_source='this_run_single_pretransfer_read_0x43c10024',elapsed_s=time.monotonic()-start)
    assert final['dmasr']&2 and not(final['dmasr']&module.DMA_SR_ERROR_MASK)
    assert final['dmasr']&0x1000,'IOC completion absent'
    module.verify_monitor_snapshot(snapshot,expected)
    buffer.freebuffer();buffer=None
    event('LEVEL2_PASS',dma=final,monitor=snapshot,dma_send_count=1,overlay_load_count=0,boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),fpga_state=Path('/sys/class/fpga_manager/fpga0/state').read_text().strip(),uptime=Path('/proc/uptime').read_text().strip())
except BaseException as exc:
    if buffer is not None and (not started or safe_release):
        buffer.freebuffer();buffer=None
    event('STOP_ERROR_NO_RESET_NO_RETRY',error_type=type(exc).__name__,message=str(exc),transfer_started=started,buffer_retained=buffer is not None)
    print('STOP_RETAIN_PROCESS',flush=True)
    # Retain the buffer, MMIO mappings and ownership lock. No further hardware access.
    while True:time.sleep(1)
