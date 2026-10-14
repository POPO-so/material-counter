import os,json,re,struct,hashlib,xml.etree.ElementTree as ET,mmap,ctypes
from pathlib import Path
from ddr_validate import validate_buffer
ART=Path('/home/xilinx/wo0013_level2_once_20261014_113914')
def pagemap_diagnostic(virtual,length,address):
    offset=virtual%4096;count=(offset+length+4095)//4096
    rows=[]
    try:
        with open('/proc/self/pagemap','rb') as f:
            f.seek((virtual//4096)*8)
            for i in range(count):
                raw=struct.unpack('<Q',f.read(8))[0]
                rows.append({'page':i,'raw':raw,'present':bool(raw&(1<<63)),'swapped':bool(raw&(1<<62)),'pfn':raw&((1<<55)-1)})
    except (OSError,struct.error) as error:
        return {'rows':rows,'available':False,'error':type(error).__name__}
    return {'rows':rows,'available':True}
def allocator_proof(buffer):
    import pynq
    from pynq.buffer import PynqBuffer
    base=Path(pynq.__file__).parent
    sources={'buffer.py':'532f1cee01509b4f90e3976d4270a0a5f7d525d4ba94f7233b0b5fda0f281463','pl_server/xrt_device.py':'fb4bdbe4cac759c66feeb0248e13c83592ddfad32b36bc83764484411812556a'}
    actual={name:hashlib.sha256((base/name).read_text().encode()).hexdigest() for name in sources}
    assert actual==sources,'Allocator changed since source audit'
    assert isinstance(buffer,PynqBuffer) and type(buffer.bo).__module__=='pyxrt'
    assert type(buffer.device).__name__=='EmbeddedDevice'
    driver=os.path.realpath('/sys/class/drm/renderD128/device/driver')
    assert driver=='/sys/bus/platform/drivers/zocl-drm','Unexpected allocator driver'
    assert not list(Path('/sys/kernel/iommu_groups').glob('*')) and not list(Path('/proc/device-tree').glob('**/iommus')),'IOMMU translation not audited'
    return {'source_hashes':actual,'buffer_type':str(type(buffer)),'bo_type':str(type(buffer.bo)),'device_type':str(type(buffer.device)),'drm_driver':driver,'api':'pynq.allocate(shape=(76816,), dtype=uint8), no custom target or imported memory','physical_address_source':'PynqBuffer.physical_address -> device_address -> EmbeddedDevice.get_device_address(bo) -> pyxrt.bo.address()','iommu_groups':[]}
def buffer_evidence(buffer):
    proof=allocator_proof(buffer)
    assert os.sysconf('SC_PAGE_SIZE')==4096
    hwh=ART/'wo0013_phase_a.hwh'
    assert hashlib.sha256(hwh.read_bytes()).hexdigest()=='a31d71f8e7004de710fa4da07078b80e2a0a8b40ff52ac523d322f21362df1fb'
    xml=ET.parse(hwh)
    axi=[(int(x.attrib['BASEVALUE'],0),int(x.attrib['HIGHVALUE'],0)+1) for x in xml.findall('.//MEMRANGE') if x.attrib.get('MASTERBUSINTERFACE')=='M_AXI_MM2S' and x.attrib.get('INSTANCE')=='ps7']
    module=next(m for m in xml.findall('.//MODULE') if m.attrib.get('INSTANCE')=='axi_dma_0')
    params={p.attrib['NAME']:p.attrib['VALUE'] for p in module.findall('./PARAMETERS/PARAMETER')}
    width=int(params['C_M_AXI_MM2S_ADDR_WIDTH'])
    assert params['C_INCLUDE_SG']=='0' and params['C_INCLUDE_S2MM']=='0' and params['C_SG_LENGTH_WIDTH']=='17'
    ram=[]
    for line in Path('/proc/iomem').read_text().splitlines():
        match=re.fullmatch(r'([0-9a-f]+)-([0-9a-f]+) : System RAM',line)
        if match:ram.append((int(match[1],16),int(match[2],16)+1))
    dt=Path('/proc/device-tree');ac=int.from_bytes((dt/'#address-cells').read_bytes(),'big');sc=int.from_bytes((dt/'#size-cells').read_bytes(),'big')
    dt_ranges=[]
    for p in dt.glob('memory*'):
        data=(p/'reg').read_bytes();stride=(ac+sc)*4
        assert len(data)%stride==0
        for i in range(0,len(data),stride):
            start=int.from_bytes(data[i:i+ac*4],'big');size=int.from_bytes(data[i+ac*4:i+stride],'big');dt_ranges.append((start,start+size))
    address=int(buffer.physical_address);virtual=int(buffer.virtual_address);length=int(buffer.nbytes)
    assert address==int(buffer.device_address) and int(buffer.bo.address())==address
    assert buffer.offset==0 and buffer.dtype.itemsize==1 and tuple(buffer.shape)==(76816,)
    bo_size=int(buffer.bo.size());offset=virtual%4096;count=(offset+length+4095)//4096
    print('BUFFER_PROPERTIES_BEFORE_PAGE_CHECK '+json.dumps({'physical_address':address,'virtual_address':virtual,'device_address':int(buffer.device_address),'bo_address':int(buffer.bo.address()),'bo_size':bo_size,'nbytes':length,'c_contiguous':bool(buffer.flags.c_contiguous),'page_count':count}),flush=True)
    diagnostic=pagemap_diagnostic(virtual,length,address)
    for row in diagnostic['rows']:
        assert not row['swapped'],'DMA buffer unexpectedly swapped'
        if row['present'] and row['pfn']:
            assert row['pfn']*4096==address-offset+row['page']*4096,'Pagemap contradicts BO physical address'
    complete=diagnostic['available'] and len(diagnostic['rows'])==count and all(x['present'] and x['pfn'] for x in diagnostic['rows'])
    pages=[x['pfn']*4096 for x in diagnostic['rows']] if complete else None
    cap=int(re.search(r'^CapEff:\s+([0-9a-f]+)',Path('/proc/self/status').read_text(),re.M)[1],16)
    control=mmap.mmap(-1,4096);control[0]=1
    caddr=ctypes.addressof(ctypes.c_char.from_buffer(control))
    diagnostic['anonymous_control']=pagemap_diagnostic(caddr,4096,0)
    control.close()
    diagnostic['cap_sys_admin']=bool(cap&(1<<21));diagnostic['complete_pfn_evidence']=bool(complete)
    cma_match=re.search(r'^CmaTotal:\s+(\d+) kB',Path('/proc/meminfo').read_text(),re.M)
    assert cma_match and int(cma_match[1])>0
    import subprocess
    dmesg=subprocess.run(['dmesg'],capture_output=True,text=True,check=True,timeout=5).stdout
    cma_source=[x for x in dmesg.splitlines() if '[drm] Memory 0 is not reserved in device tree. Will allocate memory from CMA' in x]
    assert cma_source,'CMA allocation backend evidence missing'
    evidence=dict(bo_address=int(buffer.bo.address()),bo_size=bo_size,physical_pages=pages,virtual_offset=offset,contiguous=bool(buffer.flags.c_contiguous),ram_ranges=ram,dt_ranges=dt_ranges,axi_ranges=axi,address_bits=width,alignment=8,supported_allocator=True)
    result=validate_buffer(address,length,**evidence)
    return {'result':result,'physical_address':address,'device_address':int(buffer.device_address),'virtual_address':virtual,'buffer_nbytes':length,'allocation_evidence':evidence,'pagemap':diagnostic,'allocator_proof':proof,'cma_total_kb':int(cma_match[1]),'cma_backend_dmesg':cma_source,'pynq_backend':'PynqBuffer via XRT pyxrt.bo normal, ZOCL CMA; pagemap auxiliary, contradictions fatal'}
