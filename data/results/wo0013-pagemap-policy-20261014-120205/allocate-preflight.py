exec(compile(open('/home/xilinx/wo0013_level2_once_20261014_113914/remote-common.py').read(),'remote-common','exec'))
handle=lock();guard();verified_loaded()
sys.path.insert(0,'/home/xilinx/wo0013_pagemap_policy_20261014_120205')
exec(compile(open('/home/xilinx/wo0013_pagemap_policy_20261014_120205/runtime-buffer-check.py').read(),'runtime-buffer-check','exec'))
from pynq import allocate
import numpy as np
buffer=allocate(shape=(76816,),dtype=np.uint8)
try:
    event('ALLOCATED_BEFORE_FILL',physical_address=int(buffer.physical_address),nbytes=int(buffer.nbytes),alignment=int(buffer.physical_address)%8)
    before=pagemap_diagnostic(int(buffer.virtual_address),int(buffer.nbytes),int(buffer.physical_address))
    buffer.fill(0x5a);buffer.sync_to_device()
    evidence=buffer_evidence(buffer)
    print('BUFFER_EVIDENCE_JSON '+json.dumps({'before_fill_pagemap':before,'after_fill':evidence,'sync_method':'sync_to_device','cpu_readback_ok':bool(np.all(buffer==0x5a))}),flush=True)
    assert np.all(buffer==0x5a),'CPU memory readback failed'
finally:
    buffer.freebuffer();event('PREFLIGHT_BUFFER_RELEASED_NO_DMA')
event('ALLOCATION_PREFLIGHT_PASS_NO_DMA_NO_LOAD')
