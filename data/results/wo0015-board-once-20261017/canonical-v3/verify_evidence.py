import base64, hashlib, json, pathlib, shutil, struct
ROOT = pathlib.Path(__file__).resolve().parent
REMOTE = pathlib.PurePosixPath('/home/xilinx/wo0015')
BOOT = '2cc2ab91-2e3f-450e-b9e7-361ec38e2815'
MANIFEST = 'e4d4da53e66c2fd4f1a3df082439dd439e54fa4ef1ba422367cab25ae9e98935'
def load(name): return json.loads((ROOT/name).read_bytes())
def digest(data): return hashlib.sha256(data).hexdigest()
inv=load('remote-evidence-inventory.json')
assert inv['boot_id']==BOOT and not inv['terminal_failure_present']
for item in inv['files']:
    rel=pathlib.PurePosixPath(item['relative'])
    assert not rel.is_absolute() and '..' not in rel.parts
    data=(ROOT/pathlib.Path(*rel.parts)).read_bytes()
    assert len(data)==item['bytes'] and digest(data)==item['sha256'], item['relative']
guard_dir=ROOT/'board-guards'; guard_dir.mkdir(exist_ok=True)
for item in inv['guards']:
    assert pathlib.PurePosixPath(item['name']).name==item['name']
    data=base64.b64decode(item['data_base64'], validate=True)
    assert len(data)==item['bytes'] and digest(data)==item['sha256']
    target=guard_dir/item['name']
    if target.exists(): assert target.read_bytes()==data
    else: target.write_bytes(data)
    guard=json.loads(data)
    assert guard['boot_id']==BOOT and guard['manifest_sha256']==MANIFEST
assert {g['name'] for g in inv['guards']}=={'overlay-only-consumed.json','overlay-only-pass.json','dma-single-consumed.json','dma-single-pass.json'}
s1=load('overlay-only-result.json'); s2=load('dma-single-result.json')
assert s1['status']=='OVERLAY_LOAD_PASS' and s2['status']=='DMA_SINGLE_PASS'
for result in [s1,s2]:
    assert result['boot_id']==BOOT and result['manifest_sha256']==MANIFEST
assert s2['frame_count']==1 and not s2['dma_may_be_active'] and s2['transfer_started']
frame=s2['frames'][0]; capture=frame['raw_capture']
assert capture['capture_status']=='SAVED' and capture['captured_before_release'] is True
raw={}
for name,key,hashkey,size in [('rx_raw.bin','rx_path','rx_sha256',76800),('tx_raw.bin','tx_path','tx_sha256',230432),('expected_golden.bin','expected_golden_path','expected_golden_sha256',76800)]:
    rel=pathlib.PurePosixPath(capture[key]).relative_to(REMOTE)
    data=(ROOT/pathlib.Path(*rel.parts)).read_bytes()
    assert len(data)==size and digest(data)==capture[hashkey]
    target=ROOT/name
    if target.exists(): assert target.read_bytes()==data
    else: target.write_bytes(data)
    raw[name]=data
rx=raw['rx_raw.bin']; golden=raw['expected_golden.bin']
mismatches=sum(a!=b for a,b in zip(rx,golden))
assert mismatches==frame['byte_mismatches']==capture['mismatch_bytes']==0
rw=[x[0] for x in struct.iter_unpack('<I',rx)]
gw=[x[0] for x in struct.iter_unpack('<I',golden)]
parity={}
for name,start in [('even',0),('odd',1)]:
    ids=list(range(start,len(rw),2))
    selected=b''.join(rx[4*i:4*i+4] for i in ids)
    expected=b''.join(golden[4*i:4*i+4] for i in ids)
    p={'word_count':len(ids),'poison_words':sum(rw[i]==0xA5A5A5A5 for i in ids),'zero_words':sum(rw[i]==0 for i in ids),'golden_match_words':sum(rw[i]==gw[i] for i in ids),'golden_match_bytes':sum(a==b for a,b in zip(selected,expected)),'zero_byte_count':selected.count(0),'poison_byte_count':selected.count(0xA5),'mismatch_bytes':sum(a!=b for a,b in zip(selected,expected))}
    p['poison_word_bytes']=p['poison_words']*4; p['zero_word_bytes']=p['zero_words']*4
    for key,value in capture['parity'][name].items(): assert p[key]==value,(name,key)
    parity[name]=p
snapshots={
    'pre-overlay':next(x for x in s1['afi1_snapshots'] if x['stage']=='before_overlay_load'),
    'post-overlay':next(x for x in s1['afi1_snapshots'] if x['stage']=='after_overlay_load'),
    'post-dma':next(x for x in s2['afi1_snapshots'] if x['stage']=='post_dma'),
}
registers={}
for stage,snap in snapshots.items():
    regs=snap['registers']; assert regs['AFI1_RDCHAN_CTRL']['value']==1 and regs['AFI1_WRCHAN_CTRL']['value']==0xF01
    assert regs['SLCR_FPGA_RST_CTRL']['value']==0
    registers[stage]={n:'0x%08X'%regs[k]['value'] for n,k in [('FPGA0_CLK_CTRL','SLCR_FPGA0_CLK_CTRL'),('FPGA_RST_CTRL','SLCR_FPGA_RST_CTRL'),('AFI1_RDCHAN_CTRL','AFI1_RDCHAN_CTRL'),('AFI1_WRCHAN_CTRL','AFI1_WRCHAN_CTRL')]}
for k in ['mm2s_dmasr','s2mm_dmasr']:
    assert frame[k]==0x1002,k
pre=json.loads((ROOT.parent/'pre-v3-canonical-check.json').read_bytes())
summary={'boot_id':BOOT,'manifest_sha256':MANIFEST,'stage1_status':s1['status'],'stage2_status':s2['status'],'tx_bytes':len(raw['tx_raw.bin']),'rx_bytes':len(rx),'byte_mismatches':mismatches,'mm2s_dmasr':'0x%08X'%frame['mm2s_dmasr'],'s2mm_dmasr':'0x%08X'%frame['s2mm_dmasr'],'registers':registers,'parity':parity,'raw_files':{n:{'bytes':len(b),'sha256':digest(b)} for n,b in raw.items()},'downloaded_evidence_files_verified':len(inv['files']),'guard_receipts_verified':len(inv['guards']),'terminal_failure_present':False,'precheck':{'registers':pre['registers'],'recorded_bit_path':pre['recorded_bit_path'],'failed_expected_bit_path':pre['failed_expected_bit_path'],'canonical_expected_bit_path':pre['canonical_expected_bit_path']},'captured_before_release':capture['captured_before_release']}
(ROOT/'acceptance-summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
lines=['# WO-0015 AFI1 v3 单帧闭环验收','',f"Boot ID: `{BOOT}`",f"Manifest SHA-256: `{MANIFEST}`",'',f"Stage 1: **{s1['status']}**；Stage 2: **{s2['status']}**。",f"TX 230,432 B → RX 76,800 B；byte_mismatches = **{mismatches}**。",'MM2S / S2MM DMASR 均为 `0x00001002`（Idle=1、IOC_Irq=1、Halted=0、错误位=0）。','', '## 前置路径与寄存器核查','',f"PYNQ 全局 BIT 路径：`{pre['recorded_bit_path']}`。",f"上次子目录 Manifest 期望路径：`{pre['failed_expected_bit_path']}`。",f"本次规范期望路径：`{pre['canonical_expected_bit_path']}`。",'只读前置核查的 AFI1 RD / WR 分别为 `0x00000001` / `0x00000F01`。','', '## 寄存器实测','', '| 阶段 | FPGA0_CLK_CTRL | FPGA_RST_CTRL | AFI1_RDCHAN_CTRL | AFI1_WRCHAN_CTRL |','|---|---|---|---|---|']
for stage,regs in registers.items(): lines.append('| '+stage+' | '+' | '.join('`'+v+'`' for v in regs.values())+' |')
lines += ['', '## 本地原始转储独立复算','', '| 32-bit Word 组 | Word 数 | Poison Word / 字节 | Zero Word / 字节 | Golden Match Word / 字节 | Mismatch 字节 |','|---|---:|---:|---:|---:|---:|']
for name,p in parity.items(): lines.append(f"| {name} | {p['word_count']:,} | {p['poison_words']:,} / {p['poison_word_bytes']:,} B | {p['zero_words']:,} / {p['zero_word_bytes']:,} B | {p['golden_match_words']:,} / {p['golden_match_bytes']:,} B | {p['mismatch_bytes']} |")
lines += ['', 'Poison 和 Zero 按完整 `0xA5A5A5A5`、`0x00000000` Word 统计，字节数为对应 Word 覆盖的字节数；Zero 与 Golden Match 可重叠。逐字节统计 `0x00` 时，even / odd 两组各为 37,280 B。','', '## 归档完整性','', '6 份板端生成的结果/诊断/原始文件已按板端 SHA-256 验证；4 份活跃 Guard / PASS receipt 已按原始字节备份。板端不存在 terminal-failure.json。RX 与 Golden 的 SHA-256 完全一致。', '原始完整文件保存在 `dma-single-diagnostics/`，本目录另保留 `rx_raw.bin`、`tx_raw.bin`、`expected_golden.bin` 三个规范文件名副本。', '旧部署完整保存在 `/home/xilinx/wo0015-backup-20261017-canonical-v3/`；旧路径失败 Guard 的归档位置见 `guard-archive.json`。','', '本次结果为单帧硬件闭环验收。未修改或重新打包授权 v3 载荷。']
(ROOT/'acceptance-report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'stage1_status':s1['status'],'stage2_status':s2['status'],'byte_mismatches':mismatches,'registers':registers,'mm2s_dmasr':summary['mm2s_dmasr'],'s2mm_dmasr':summary['s2mm_dmasr'],'parity':parity,'evidence_hashes_verified':len(inv['files']),'guard_receipts_verified':len(inv['guards']),'rx_equals_golden':rx==golden},indent=2))
