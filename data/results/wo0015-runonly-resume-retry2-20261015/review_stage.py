"""Host-only manager acceptance; decoded results never replace raw evidence."""
import hashlib
import json
from pathlib import Path
import sys

OUT=Path(__file__).resolve().parent
stage=sys.argv[1]
number={'transition':2,'dma-single':3,'dma-three':4}[stage]
assert json.loads((OUT/f'request-{number:03d}-done.json').read_bytes())['exit_code']==0
text=(OUT/f'request-{number:03d}-stdout.txt').read_text(encoding='utf-8')
decoder=json.JSONDecoder();rows=[]
while text.strip():
    text=text.lstrip();row,n=decoder.raw_decode(text);rows.append(row);text=text[n:]
if stage=='transition':
    result=rows[-1];assert result['status']=='PASS' and result['mmio_writes']==0
    assert result['cma']['CmaFree']>=32768
    assert result['post_stage_audit']['dmesg_health']=='PASS'
    target='step1-acceptance.json'
else:
    result=rows[0];receipt=rows[-1]['continuation_receipt']
    frames=1 if stage=='dma-single' else 3
    assert result['status']==('DMA_SINGLE_PASS' if frames==1 else 'DMA_THREE_PASS')
    assert result['frame_count']==frames and len(result['frames'])==frames
    assert result['dma_may_be_active'] is False and result['post_stage_audit']['dmesg_health']=='PASS'
    assert all(s['cma_free_kib']>=16384 for s in result['cma_allocation_samples'])
    assert result['allocation_accounting']['hard_gate'] is False
    gate=result['post_alloc_physical_gate']
    assert gate['status']=='POST_ALLOC_PHYSICAL_GATE_PASS'
    tx,rx=gate['mm2s_physical_range'],gate['s2mm_physical_range']
    assert tx[1]-tx[0]==230432 and rx[1]-rx[0]==76800
    assert 0x100000<=tx[0]<tx[1]<=0x20000000 and 0x100000<=rx[0]<rx[1]<=0x20000000
    assert tx[0]%4==rx[0]%4==0 and (tx[1]<=rx[0] or rx[1]<=tx[0])
    for f in result['frames']:
        assert f['byte_mismatches']==0 and f['s2mm_actual_length']==76800
        assert f['accepted_pixels']==76800 and f['sent_beats']==19200 and f['packer_status']==2
        for name in ('s2mm_dmasr','mm2s_dmasr'):
            assert f[name]&0x1002==0x1002 and f[name]&0x4071==0
        c=f['checker']
        assert (c['pixels'],c['sofs'],c['eols'])==(76800,1,240)
        assert (c['input_bytes_delta'],c['input_beats_delta'],c['input_tlast_delta'],c['packet_done_delta'],c['output_total_delta'])==(230432,57608,1,1,76800)
    expected_source=hashlib.sha256((OUT/'board_once_watermark.py').read_bytes()).hexdigest()
    assert receipt['executor_sha256']==expected_source
    assert receipt['continuation_sha256']==hashlib.sha256((OUT/'continuation-execution.json').read_bytes()).hexdigest()
    encoded=(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode()
    assert receipt['result_sha256']==hashlib.sha256(encoded).hexdigest()
    # This is an acceptance record, not a rewritten board result file.
    result={'status':'PASS','stage':stage,'reviewed_result':result,'continuation_receipt':receipt}
    target='step2-acceptance.json' if frames==1 else 'step3-acceptance.json'
with (OUT/target).open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
print(json.dumps({'status':'PASS','stage':stage,'acceptance':target},indent=2))
