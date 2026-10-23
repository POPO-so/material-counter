"""Record a conditional static call chain from pinned source; never import PYNQ."""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1]
ROOT = OUT.parents[2]
PYNQ = OUT / 'rootfs/extracted/pynq'
HISTORY = ROOT / 'data/results/wo0015-runonly-resume-retry2-20261015/remote-evidence/board_once_watermark.py'

def evidence(path, spans):
    raw = path.read_bytes()
    lines = raw.decode('utf-8').splitlines()
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
            'excerpts': [{'line': i, 'text': lines[i-1]} for a,b in spans for i in range(a,min(b,len(lines))+1)]}

buffer = (PYNQ / 'buffer.py').read_text(encoding='utf-8')
xrt = (PYNQ / 'pl_server/xrt_device.py').read_text(encoding='utf-8')
for expected in ['def sync_to_device(self):', 'def sync_from_device(self):',
                 'self.flush()', 'self.invalidate()', 'return not self.coherent']:
    assert expected in buffer
for expected in ['cacheable=0', 'coherent=False', 'flags = pyxrt.bo.flags.normal',
                 'bo.sync(pyxrt.xclBOSyncDirection.XCL_BO_SYNC_BO_TO_DEVICE)',
                 'bo.sync(pyxrt.xclBOSyncDirection.XCL_BO_SYNC_BO_FROM_DEVICE)']:
    assert expected in xrt
result = json.loads((OUT / 'golden-and-cache-audit.json').read_bytes())
result['cache_static_audit'] = {
    'classification': 'VERIFIED_LOCAL_IMAGE_LOGIC; applicability to historical installed runtime INFERRED/UNKNOWN',
    'condition': 'Historical executor source plus unmodified candidate image package; EmbeddedDevice active; no runtime replacement/monkeypatch',
    'historical_allocation': 'PynqBoardAPI.allocate passes shape=(size,), dtype=u1 only; no cacheable or target keyword',
    'allocation_chain': ['pynq.allocate -> buffer.allocate(target=None)',
                         'Device.active_device.allocate -> Device.allocate -> self.default_memory.allocate',
                         'EmbeddedDevice.default_memory -> EmbeddedXrtMemory (inherits XrtMemory.allocate)',
                         'XrtMemory.allocate -> _xrt_allocate(cacheable=0)',
                         'device.allocate_bo -> pyxrt.bo.flags.normal -> pyxrt.bo(handle, max(size,8192), flags, idx)',
                         'PynqBuffer(..., coherent=False)'],
    'cacheable_parameter_default': 0,
    'xrt_bo_flags_default': 'pyxrt.bo.flags.normal (cacheable flag only when cacheable argument truthy)',
    'pynqbuffer_coherent_default_on_this_path': False,
    'pynqbuffer_cacheable_property_on_this_path': True,
    'cacheable_semantics': 'PynqBuffer.cacheable is simply not coherent. Its True value is distinct from the cacheable=0 allocation keyword and does not prove kernel mapping cache attributes.',
    'cpu_to_device': ['historical api.sync_to_device(buffer)',
                      'callable buffer.sync_to_device exists -> primary branch (adapter flush fallback not selected)',
                      'PynqBuffer.sync_to_device -> self.flush',
                      'coherent=False -> self.device.flush(bo, offset, virtual_address, nbytes)',
                      'XrtDevice.flush -> bo.sync(pyxrt.xclBOSyncDirection.XCL_BO_SYNC_BO_TO_DEVICE)'],
    'device_to_cpu': ['historical api.sync_from_device(s2mm_buffer)',
                      'callable buffer.sync_from_device exists -> primary branch (adapter invalidate fallback not selected)',
                      'PynqBuffer.sync_from_device -> self.invalidate',
                      'coherent=False -> self.device.invalidate(bo, offset, virtual_address, nbytes)',
                      'XrtDevice.invalidate -> bo.sync(pyxrt.xclBOSyncDirection.XCL_BO_SYNC_BO_FROM_DEVICE)'],
    'sync_for_cpu_sync_for_device': 'Not the buffer methods selected by this executor/candidate class; names are not interchangeable with primary sync_to/from methods.',
    'libcma': 'No libcma/xlnk call in the selected buffer.allocate -> EmbeddedDevice/XrtMemory -> pyxrt BO chain; presence of libcma.so does not establish use.',
    'unknown': ['Historical package hashes, actual class/method resolution and buffer coherent metadata were not captured',
                'Runtime pyxrt dispatch, kernel BO cache mapping and actual cache-maintenance success were not measured',
                'Static sync calls neither exclude a cache problem nor prove it caused the mismatch'],
    'evidence': [evidence(HISTORY, [(440,450),(508,522),(915,931),(950,970)]),
                 evidence(PYNQ / 'buffer.py', [(38,47),(85,91),(104,129),(138,163)]),
                 evidence(PYNQ / 'pl_server/device.py', [(127,145)]),
                 evidence(PYNQ / 'pl_server/embedded_device.py', [(493,497),(550,576)]),
                 evidence(PYNQ / 'pl_server/xrt_device.py', [(59,88),(113,129),(322,352)])]
}
(OUT / 'golden-and-cache-audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('Recorded local-image static synchronization call chain; historical runtime equivalence remains unknown.')
