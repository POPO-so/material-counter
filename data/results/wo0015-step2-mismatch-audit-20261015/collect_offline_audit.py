"""Reproducible local evidence audit. No SSH, MMIO, PYNQ, or Vivado use."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
PREV=OUT.parent/'wo0015-runonly-resume-retry2-20261015'
BUNDLE=OUT.parent/'wo0015-retry3-two-tier-bundle-20261015'
BUILD=OUT.parent/'wo0015-hw-50mhz-20261015'
def sha(raw):return hashlib.sha256(raw).hexdigest()
def save(name,row):(OUT/name).write_text(json.dumps(row,indent=2),encoding='utf-8')
index=json.loads((PREV/'evidence-sha256.json').read_bytes())
assert all(sha((PREV/n).read_bytes())==h for n,h in index.items())
exit=json.loads((PREV/'exit-os-result.json').read_bytes())
result=json.loads((PREV/'remote-evidence/dma-single-result.json').read_bytes())
manifest=json.loads((BUNDLE/'run-manifest.json').read_bytes())
assert sha((BUNDLE/'run-manifest.json').read_bytes())==result['manifest_sha256']
golden=(BUNDLE/manifest['frames'][0]['binary_path']).read_bytes()
assert sha(golden)==manifest['frames'][0]['binary_sha256']
assert sha((BUNDLE/'wo15.bit').read_bytes())=='a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420'
hwh=BUILD/'export/wo15.hwh'
assert sha(hwh.read_bytes())=='bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6'
assert not any(n.endswith('.bin') for n in exit['result_files'])
c=exit['final_registers']['checker']
saved={'evidence_type':'HISTORICAL_POST_STOP_SNAPSHOT_NOT_CURRENT_BOARD_STATE',
       'snapshot_path':str(PREV/'exit-os-result.json'),'snapshot_sha256':sha((PREV/'exit-os-result.json').read_bytes()),
       'historical_files_verified':len(index),'checker_raw':c,'dma_raw':exit['final_registers']['dma'],
       'frame_id':c['0x10'],'completed_packet_count':c['0x14'],'pixels':c['0x34'],'eols':c['0x3c'],
       'fg_count_register_exists':False,'output_checksum':c['0x40'],
       'derived_foreground_pixels':c['0x40']//255,
       'packer_raw':{k:c[k] for k in ('0x4c','0x50','0x54','0x58','0x5c','0x60')},
       'byte_mismatches':result['byte_mismatches'],'first_mismatch':result['first_mismatch'],
       'historical_rx_bytes_available':False,'expected_at_first_mismatch':golden[result['first_mismatch']],
       'actual_at_first_mismatch':None,
       'actual_inference':'first 4 bytes match Golden; byte at offset4 differs from 0x00, value unknown'}
save('saved-board-evidence.json',saved)
root=ET.parse(hwh).getroot();modules={m.get('INSTANCE'):m for m in root.iter('MODULE')}
def params(m):return {p.get('NAME'):p.get('VALUE') for p in m.findall('./PARAMETERS/PARAMETER')}
dma=params(modules['axi_dma_0']);ps=params(modules['ps7']);pipeline=params(modules['image_pipeline'])
bus={}
for n in ('axi_dma_0','hp1_fabric','ps7'):
    bus[n]=[{k:v for k,v in b.attrib.items() if k in ('NAME','BUSNAME','DATAWIDTH','VLNV')}
            for b in modules[n].findall('./BUSINTERFACES/BUSINTERFACE')
            if b.get('BUSNAME') in ('axi_dma_0_M_AXI_S2MM','hp1_fabric_M00_AXI')]
save('static-bus-audit.json',{'hwh_sha256':sha(hwh.read_bytes()),
    'dma':{k:dma[k] for k in ('C_M_AXI_S2MM_DATA_WIDTH','C_S_AXIS_S2MM_TDATA_WIDTH',
                              'C_M_AXI_MM2S_DATA_WIDTH','C_M_AXIS_MM2S_TDATA_WIDTH')},
    'ps':{k:ps[k] for k in ('PCW_S_AXI_HP0_DATA_WIDTH','PCW_S_AXI_HP1_DATA_WIDTH')},
    'pipeline':{k:pipeline[k] for k in ('WIDTH','HEIGHT','THRESHOLD')},'bus':bus,
    'converter':'hp1_fabric/s00_couplers/auto_pc: AXI4 to AXI3; both sides32; no auto_us/auto_ds',
    'runtime_afi1_mode_sampled':False,
    'runtime_afi1_control_addresses':['0xF8009000','0xF8009014'],
    'generated_post_config_mask_value':['mask=1,value=1','mask=1,value=1']})
sys.path.insert(0,str(ROOT/'scripts/wo0015'))
import board_once as run
synthetic=OUT/'synthetic-only';synthetic.mkdir(exist_ok=True)
hypothesis=b''.join((b'\x00'*4 if word%2==0 else b'\xa5'*4) for word in range(len(golden)//4))
alternative=bytearray(golden)
for i in range(4,39524):alternative[i]^=1
assert hypothesis!=bytes(alternative)
analyses={}
for label,raw in [('even_zero_odd_a5',hypothesis),('different_contiguous_corruption',bytes(alternative))]:
    d=run.analyze_rx_mismatch(raw,golden)
    assert d['mismatch_bytes']==39520 and d['first_mismatch']==4
    d['evidence_type']='OFFLINE_SYNTHETIC_NOT_BOARD_RX'
    (synthetic/(label+'.bin')).write_bytes(raw)
    (synthetic/(label+'.json')).write_text(json.dumps(d,indent=2),encoding='utf-8')
    analyses[label]=d
foreground={name:sum(v==255 for off,v in enumerate(golden) if (off//4)%2==parity)
            for name,parity in [('even',0),('odd',1)]}
save('parity-hypotheses.json',{'evidence_type':'OFFLINE_SYNTHETIC_NOT_BOARD_RX',
      'golden_sha256':sha(golden),'golden_bytes':len(golden),'golden_foreground_total':golden.count(255),
      'golden_foreground_by_word_parity':foreground,'words_per_parity':9600,
      'actual_rx_pattern_confirmed':False,'nonunique_aggregate_proved':True,'analyses':analyses})
sources={
 'executed-script':(PREV/'remote-evidence/board_once_watermark.py',[(440,451),(510,526),(945,974)]),
 'build-tcl':(ROOT/'scripts/wo0015/create_s2mm_bd.tcl',[(150,195),(244,254),(280,289)]),
 'hwh':(hwh,[(997,1014),(1444,1449),(3044,3070),(3112,3118),(4110,4115),(5105,5116)]),
 'bd':(BUILD/'project/wo15_50m.srcs/sources_1/bd/wo15/wo15.bd',[(838,853),(1435,1467)]),
 'ps7-post-config':(BUILD/'project/wo15_50m.gen/sources_1/bd/wo15/ip/wo15_ps7_0/ps7_init.tcl',[(150,158),(529,546)]),
 'xsim-tb':(ROOT/'fpga/sim/wo0015/tb_axis_dma_s2mm_pipeline.sv',[(1,12),(103,121),(215,221),(495,509)])}
snippets={}
for label,(path,ranges) in sources.items():
    lines=path.read_text(encoding='utf-8',errors='replace').splitlines()
    snippets[label]={'path':str(path),'sha256':sha(path.read_bytes()),
       'excerpts':[{'line':i,'text':lines[i-1]} for a,b in ranges for i in range(a,min(b,len(lines))+1)]}
save('local-source-evidence.json',snippets)
save('primary-sources.json',[
 {'title':'AMD UG585 AFI register overview','url':'https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-Overview',
  'used_for':'AFI channel controls select 32/64 interface mode; not a board measurement'},
 {'title':'AMD UG585 AFI_RDCHAN_CTRL Details','url':'https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-AFI_RDCHAN_CTRL-Details',
  'used_for':'bit0=1 selects32, bit0=0 selects64'},
 {'title':'AMD UG585 AFI_WRCHAN_CTRL Details','url':'https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-AFI_WRCHAN_CTRL-Details',
  'used_for':'bit0=1 selects32, bit0=0 selects64'}])
save('scope-audit.json',{'board_connections':0,'overlay_loads':0,'hardware_cma_allocations':0,'dma_starts':0,
    'vivado_builds':0,'rtl_modified':False,'bit_hwh_modified':False,
    'root_cause_status':'UNCONFIRMED: no historical RX bytes or AFI1 mode readback',
    'confirmed_defect':'historical Golden mismatch path did not persist RX bytes before free',
    'new_executor_sha256':sha((ROOT/'scripts/wo0015/board_once.py').read_bytes())})
print(json.dumps({'status':'OFFLINE_AUDIT_EVIDENCE_COLLECTED','derived_foreground':saved['derived_foreground_pixels'],
                  'synthetic_mismatch':analyses['even_zero_odd_a5']['mismatch_bytes'],'runtime_AF1_known':False},indent=2))
