import json,os,hashlib
from pathlib import Path
exec(compile(open('/home/xilinx/wo0013_level2_once_20261014_113914/remote-common.py').read(),'remote-common','exec'))
handle=lock();guard();verified_loaded()
import pynq
base=Path(pynq.__file__).parent
for name in ['buffer.py','pl_server/xrt_device.py']:
    print('SOURCE_ID '+json.dumps({'name':name,'sha256':hashlib.sha256((base/name).read_text().encode()).hexdigest()}),flush=True)
print('CURRENT_LINUX '+json.dumps({'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'iomem':Path('/proc/iomem').read_text(),'meminfo':Path('/proc/meminfo').read_text(),'cap_status':Path('/proc/self/status').read_text(),'iommu_groups':[str(p) for p in Path('/sys/kernel/iommu_groups').glob('*')],'drm_driver':os.path.realpath('/sys/class/drm/renderD128/device/driver'),'dt_iommu':[str(p) for p in Path('/proc/device-tree').glob('**/iommus')]}),flush=True)
