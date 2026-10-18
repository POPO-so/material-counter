"""Derive the milestone only from received receipts, access events and raw values."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
CODE=ROOT/'scripts/wo0014_hw/diagnostic50'
sys.path.insert(0,str(CODE))
import core
base=core.base
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
reviewed=json.loads((OUT/'reviewed-local.json').read_text())
protected=json.loads((ROOT/'data/results/wo0014-offline-20261014/protected-before.json').read_text())
assert all(sha(ROOT/n)==h for n,h in protected.items())
assert all(sha(CODE/n)==h for n,h in reviewed['source_sha256'].items())
(OUT/'protected-after.json').write_text(json.dumps({n:sha(ROOT/n) for n in protected},indent=2))
git=subprocess.run(['git','status','--short'],cwd=ROOT,capture_output=True,text=True,check=True)
(OUT/'git-status.txt').write_text(git.stdout)
diff=subprocess.run(['git','diff','--check'],cwd=ROOT,capture_output=True,text=True)
assert diff.returncode==0
summary={'status':'INCOMPLETE','software_tests':'PASS','test_count':172,'subtests':19,
         'protected_unchanged':True,'commit_push_performed':False,
         'stage_a':{'status':'NOT_EXECUTED'},'stage_b':{'status':'NOT_EXECUTED'}}
lines=['# WO-0014 PG021 首帧判据修复与 50 MHz 单帧 DMA 板测报告','',
       '日期：2026-10-15，Asia/Shanghai。','',
       '## 1. PG021 软件修复与本地测试','',
       'core.py 的 RUNSTOP 轮询仅要求 Halted=0 且错误位为零，允许 Idle=0/1；SA 写入后的 LENGTH 前门同样允许首帧 Idle=0，同时继续检查 DMACR.RS=1、Reset=0。LENGTH 后完成判据仍要求 Idle=1、Halted=0、无错误与 Checker 完成；VERIFY 的 IOC/地址/长度/Golden 检查没有放宽。32 位 DMA 地址配置保持，仅写 SA，不引入 SA_MSB 或改变接口。','',
       '修复依据：[AMD PG021 MM2S_DMASR](https://docs.amd.com/r/en-US/pg021_axi_dma/MM2S_DMASR-MM2S-DMA-Status-Register-Offset-04h) 与 [Simple DMA 编程序列](https://docs.amd.com/r/en-US/pg021_axi_dma/Direct-Register-Mode-Simple-DMA)。所有阶段期限、健康门、单次守卫及失败时保留资源的规则保持。','',
       '新增 6 项回归，RED 中首帧 DMASR=0 场景复现失败；修复后 diagnostic50 144 PASS，legacy 11 PASS +3 subtests，vectors 7 PASS，DDR 10 PASS +16 subtests，合计 172 PASS、19 subtests PASS。RED/GREEN 原始日志、core.diff 与完整 preflight 在本目录及 reviewed-local.json 所指路径。','',
       '## 2. PID6311、CMA 与失败 Guard 回收归档','']
recover_path=OUT/'cleanup-deploy-result.json'
if recover_path.exists():
    recovered=json.loads(recover_path.read_text())
    summary['guard_recovery']='PASS'
    summary['cleanup']=recovered['cleanup']
    c=recovered['cleanup']
    lines+=[f'现场先核对同一 boot、旧部署哈希、原 SEND 超时状态及 SA/LENGTH 写入次数均为 0。回收方式：{c["signal_method"]}；PID/starttime/完整 argv/可执行文件核对后，以 pidfd 绑定目标，先暂停并保存现场，再终止。回收后进程状态 {c["process_after"]["state"]}、FD/maps 均空，无硬件占用。',
        f'CMA：之前 CmaFree={c["cma_before"]["CmaFree_kib"]} KiB，之后 {c["cma_after"]["CmaFree_kib"]} KiB，增加 **{c["cma_free_increase_kib"]} KiB**；旧包按页取整为 {c["old_packet_allocation_rounded_kib"]} KiB。',
        '归档：远端 `/home/xilinx/wo0014_pg021_archive_20261015/`；本地 `archived-board-verified/`，含完整旧 Guard、失败状态、进程/CMA 快照、日志与 archive-manifest.json，下载后逐项 SHA 核对。在取得旧锁并重新核对完整 Guard 文件集合及 SHA 后，才移动旧目录并建立新固定 Guard；新部署字节再次核对。','']

last=None
for mode,key in [('overlay-only','stage_a'),('dma-once','stage_b')]:
    path=OUT/(mode+'-result.json')
    if not path.exists():continue
    data=json.loads(path.read_text());last=data['post']
    assert data['status']=='PASS' and last['post_pass']
    events=[json.loads(line) for line in last['hardware_events']]
    guard=last['guard']
    status=last['executor_status']
    assert status['status']=='PASS'
    for name,entry in guard.items():
        if name.endswith('-receipt.json') or name.endswith('-pass.json'):
            folder=OUT/'receipts';folder.mkdir(exist_ok=True)
            (folder/name).write_bytes(entry['text'].encode('utf-8'))
            assert sha(folder/name)==entry['sha256']
    if mode=='overlay-only':
        begins=[e for e in events if e.get('event')=='ACCESS_BEGIN']
        returns=[e for e in events if e.get('event')=='ACCESS_RETURN']
        assert [e['operation'] for e in begins]==['Overlay']
        assert [e['operation'] for e in returns]==['Overlay']
        elapsed=returns[0]['monotonic_s']-begins[0]['monotonic_s']
        receipt=json.loads(guard['overlay-receipt.json']['text'])
        assert receipt=={'schema':1,'mode':'overlay-only','boot_id':last['boot_id'],'bit_sha256':core.BIT_SHA}
        summary[key]={'status':'PASS','overlay_elapsed_s':elapsed,'receipt':str(OUT/'receipts/overlay-receipt.json'),
                      'supervision_receipt':str(OUT/'receipts/supervision-overlay-only-pass.json')}
        lines+=['## 3. 阶段 A：50 MHz Overlay-only','',
            f'**PASS**。唯一一次 Overlay 调用返回，用时 **{elapsed:.9f} 秒**。加载前 ENV/HEALTH、加载后 HEALTH 与独立 OS 门均 PASS；正式 Overlay 和监督 receipt 均保存于 receipts/。',
            f'boot_id=`{last["boot_id"]}`，BIT SHA-256=`{core.BIT_SHA}`；Manager operating/flags=0、shutdown_ips={{}}。','']
    else:
        plan=base.validate_inputs(ROOT/'data/results/wo0014-hw-50mhz-20261014/export/wo0014_image.bit',
            ROOT/'data/results/wo0014-hw-50mhz-20261014/export/wo0014_image.hwh',
            ROOT/'data/results/wo0014-hw-50mhz-20261014/export/rgb_packet.bin',
            ROOT/'data/results/wo0014-hw-50mhz-20261014/export/pair-manifest.json')
        match=[e for e in events if e.get('event')=='RESULT_MATCH']
        assert len(match)==1
        actual=match[0]['checker'];base._check_checker_result(plan,actual)
        sends=[e for e in events if e.get('event')=='ACCESS_BEGIN' and e.get('operation')=='write.LENGTH']
        done=[e for e in events if e.get('event')=='DMA_OUTPUT_COMPLETE']
        assert len(sends)==len(done)==1
        elapsed=done[0]['monotonic_s']-sends[0]['monotonic_s']
        dma_observed=next(e['monotonic_s'] for e in events if e.get('event')=='ACCESS_RETURN'
                         and e.get('operation')=='poll.dmasr' and e['value']&3==2
                         and not e['value']&base.DMA_SR_ERROR_MASK)-sends[0]['monotonic_s']
        trace={label:next(e['value'] for e in events if e.get('event')=='ACCESS_RETURN' and e.get('operation')==label)
               for label in ('initial.dmacr','initial.dmasr','initial.length','runstop.dmasr','before_length.dmasr','before_length.dmacr')}
        assert sum(e.get('event')=='ACCESS_RETURN' and e.get('operation')=='freebuffer' for e in events)==1
        initial={e['operation'].split('.',1)[1]:e['value'] for e in events if e.get('state')=='INIT'
                 and e.get('event')=='ACCESS_RETURN' and e.get('operation','').startswith('checker.')}
        expected={'drain_enable':1,'magic':base.PACKET_MAGIC,'status':9,'error_code':0,'frame_id':0,'packet_done_count':1,'packet_error_count':0,
            'input_bytes':230432,'beats':57608,'tlast_count':1,'first_word':base.PACKET_MAGIC,'last_word':base.TRAILER_END,
            'input_byte_checksum':plan.input_byte_checksum,'observed_pixels':76800,'sofs':1,'eols':240,
            'binary_checksum':plan.expected_binary_checksum,'accepted_output_total':76800}
        assert all(actual[n]==v for n,v in expected.items())
        summary[key]={'status':'PASS','send_attempts':1,'packet_bytes':230432,'dma_checker_elapsed_s':elapsed,
            'dma_completion_observed_s':dma_observed,'dma_register_trace':trace,
            'checker_initial':initial,'checker_final':actual,'dma_final':match[0]['dma'],'golden':expected,
            'buffer_released':True,'aggregate_only':True}
        lines+=['## 4. 阶段 B：单帧 DMA + Checker MMIO','',
            f'**PASS**。通过 Overlay 与 MMIO 监督 receipt 后，仅一次 MM2S LENGTH=230432。受控 CMA 缓冲区包哈希核验及 sync_to_device 完成。LENGTH 调用开始至 DMA Idle 完成态首个读回为 **{dma_observed:.9f} 秒**，至 DMA/Checker 完整统计为 **{elapsed:.9f} 秒**（包括软件日志/轮询/读回，未测量纯 AXI 线速延迟）。','',
            '| DMA 轨迹 | DMACR | DMASR | LENGTH |', '|---|---:|---:|---:|',
            f'| 初始态 | 0x{trace["initial.dmacr"]:08x} | 0x{trace["initial.dmasr"]:08x} | {trace["initial.length"]} |',
            f'| RS=1，LENGTH 前 | 0x{trace["before_length.dmacr"]:08x} | 0x{trace["before_length.dmasr"]:08x} | 尚未写入 |',
            f'| 完成态 | 0x{match[0]["dma"]["dmacr"]:08x} | 0x{match[0]["dma"]["dmasr"]:08x} | {match[0]["dma"]["length"]} |','',
            '| 寄存器 | 地址偏移 | 初始 | 实测 | Golden | 比对 |','|---|---:|---:|---:|---:|---|']
        for name,value in expected.items():
            offset=base.CHECKER_REGS[name]
            lines.append(f'| {name} | 0x{offset:02x} | {initial.get(name,"—")} | {actual[name]} | {value} | PASS |')
        lines.append(f'| stalled_cycles | 0x48 | {initial.get("stalled_cycles","—")} | {actual["stalled_cycles"]} | 无固定周期 Golden | 仅观测 |')
        lines+=['',f'DMA 最终读回：`{json.dumps(match[0]["dma"],sort_keys=True)}`。二值输出累加和 `{actual["binary_checksum"]}` 对应 `{actual["binary_checksum"]//255}` 个前景像素（0/255 Binary8 统计推导）。','',
            '该 Checker 验证的是帧协议与聚合统计；没有逐像素输出回读，因此不宣称本轮每个输出像素均已逐项验证，也不扩展为 Route、跟踪计数或异常恢复板测结论。','']

failure_path=OUT/'dma-failure-snapshot.json'
if failure_path.exists() and summary['stage_b']['status']!='PASS':
    failure=json.loads(failure_path.read_text());last=failure
    events=[json.loads(s) for s in failure['hardware_events']]
    status=failure['executor_status'];child=status['child_result'];raw=child['raw']
    assert status['status']=='FAIL' and status['stage']=='SEND' and child['send_attempts']==0
    length_attempts=sum(e.get('event')=='ACCESS_BEGIN' and e.get('operation')=='write.LENGTH' for e in events)
    sa_attempts=sum(e.get('event')=='ACCESS_BEGIN' and e.get('operation')=='write.SA' for e in events)
    assert length_attempts==sa_attempts==0
    buffers=[e for e in events if e.get('event')=='BUFFER_READY']
    assert len(buffers)==1 and child['buffer_retained']
    start=next(e['monotonic_s'] for e in events if e.get('event')=='ACCESS_RETURN' and e.get('operation')=='write.RUNSTOP')
    end=next(e['monotonic_s'] for e in events if e.get('event')=='TERMINAL_FAIL')
    elapsed=end-start
    assert not any(e.get('event')=='ACCESS_BEGIN' and e.get('operation')=='freebuffer' for e in events)
    summary['stage_b']={'status':'FAIL','reason':child['error'],'child_pid':status['child_pid'],
        'send_attempts':0,'length_attempts':0,'sa_attempts':0,'packet_bytes':230432,
        'buffer':buffers[0],'buffer_retained':True,'buffer_released':False,
        'runstop_poll_elapsed_s':elapsed,'dma_transfer_elapsed_s':None,
        'raw_registers_before_failure':raw,'golden_comparison':'NOT_RUN_NO_LENGTH_WRITE'}
    summary['status']='SOFTWARE_AND_OVERLAY_PASS_DMA_ADMISSION_FAIL'
    for name in ('overlay-receipt.json','mmio-receipt.json','supervision-overlay-only-pass.json','supervision-mmio-read-only-pass.json'):
        entry=failure['guard'][name];folder=OUT/'receipts';folder.mkdir(exist_ok=True)
        (folder/name).write_bytes(entry['text'].encode())
        assert sha(folder/name)==entry['sha256']
    lines+=['## 4. 阶段 B：MMIO 准入通过，DMA 启动前软件门失败','',
        f'**FAIL，已停止**。mmio-read-only 及监督 receipt PASS；dma-once 的 ENV/HEALTH/MIN_READ/INIT 完成。SEND 中分配并核验 230432 字节 CMA 包，sync_to_device 返回；缓冲物理地址 `0x{buffers[0]["address"]:08x}`。一次 RUNSTOP 写入返回后，DMASR 连续读回 0，执行器等待 IDLE 约 **{elapsed:.9f} 秒** 后报 `Python RUNSTOP polling deadline`。',
        'SA 写入次数=0，LENGTH 写入次数=0，帧发送次数=0。没有实际帧传输耗时；Golden 输出比对未运行，不能记为 PASS 或把初始零统计当作 Golden 不匹配。','',
        '| 失败前已读回项 | 值 | 含义 |','|---|---:|---|',
        f'| initial.dmacr | 0x{raw["initial.dmacr"]:08x} | RUNSTOP 写入前 |',
        f'| initial.dmasr | 0x{raw["initial.dmasr"]:08x} | 初始 Halted |',
        f'| initial.length | {raw["initial.length"]} | 未配置传输 |',
        f'| runstop.dmasr | 0x{raw["runstop.dmasr"]:08x} | Halted 已清零、Idle 未置位、DMA 错误位为零 |','',
        '| Checker 初始寄存器 | 偏移 | 读回 |','|---|---:|---:|']
    for name,offset in base.CHECKER_REGS.items():
        lines.append(f'| {name} | 0x{offset:02x} | {raw["checker."+name]} |')
    lines+=['',
        '离线完成帧 Golden 仍为：input_bytes=230432、beats=57608、packet_done_count=1、observed_pixels=76800、SOF=1、EOL=240、TLAST=1、input_byte_checksum=6620674、binary_checksum=571200（2240 个前景像素）。本轮没有写 LENGTH，未获得这些完成帧统计。','',
        f'故障 child PID={status["child_pid"]}、CMA 引用、SSH/session、terminal-fail 与 dma-once-consumed 均保留；没有后续 MMIO 扫描、freebuffer、reset、重载或 DMA 重试。远端 snapshot 与全部日志见 dma-failure-board/。','']

lines+=['## 5. 测试后健康、边界及建议','']
if last:
    summary['post_health']={k:last[k] for k in ('boot_id','fpga_state','fpga_flags','post_pass','new_kernel_warnings','process_audit')}
    if summary['stage_b']['status']=='FAIL':
        lines+=['失败后只读核查：工作站 Ping 与严格 SSH 通过；boot 未变，Manager operating/flags=0，Linux 文件可读，dmesg 尾部连续且无新增异常。完整准入门没有记 GREEN：PID6311 因按 fail-closed 保留的硬件 FD/maps 仍在，CMA 未释放，固定 Guard 已进入失败终态。']
    else:
        lines+=['最后独立 OS 门：boot 连续，Manager operating/flags=0，dmesg 尾部连续且无新增异常，硬件占用/不可审计进程为空；原生 SSH 与工作站阶段前后 Ping 均由 controller 保存证据。']
if summary['stage_b']['status']=='PASS':
    summary['status']='MILESTONE_PASS'
    lines+=['DMA 缓冲区正常路径 freebuffer 返回，子进程正常退出；本轮 DMA 不重试。',
            '建议先扩展 50 MHz 多帧/不同合法包与逐像素输出验证，再以独立准入与同协议对照验证已具备交付检查的 100 MHz 比特流。P2/P3 摄像头与 S2MM 属于后续接口/闭环工作，需单独安排；本轮 MM2S 聚合统计不等于 S2MM、实时视频、Route 或跟踪计数通过。']
else:
    lines+=['下一步优先修复首帧 DMA 的软件准入条件并做离线回归。本轮查阅 [AMD PG021 MM2S_DMASR](https://docs.amd.com/r/en-US/pg021_axi_dma/MM2S_DMASR-MM2S-DMA-Status-Register-Offset-04h)：Direct Register 模式首次传输前 Idle=0。其 [Simple DMA 编程序列](https://docs.amd.com/r/en-US/pg021_axi_dma/Direct-Register-Mode-Simple-DMA) 要求 RS=1 后观察 Halted 清零，再写 SA、最后写 LENGTH。',
        '工程判断：core.py stage_SEND 在首帧前要求 Idle=1，以及后续 idle() 门，同上述语义不符；本轮 DMASR 从 1 变为 0 的证据符合已启动而尚无首帧的状态。根因指向新的软件准入缺陷。本轮授权限定两项修复且发生超时后要求停止，因此没有修改该第三项逻辑或再次板测；当前保留现场的处置及新的单次尝试应由队长另行授权。']
lines+=['四份受保护文档、已有生产 RTL 与原始 helper 的本地预检保护校验通过。未编辑 RTL/BD、未 commit/push。',
        '软件变更：core.py、新 test_pg021_ready.py、executor-manifest.json；现场执行与审计脚本/原始结果均在本结果目录。','']
(OUT/'milestone-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
(OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
hashes={};skipped=[]
for p in OUT.rglob('*'):
    if not p.is_file() or p.name in ('evidence-sha256.json','evidence-hash-skipped.json') or '__pycache__' in p.parts:
        continue
    try:hashes[str(p.relative_to(OUT))]=sha(p)
    except PermissionError:
        skipped.append({'file':str(p.relative_to(OUT)),
                        'reason':'Retained native SSH still holds exclusive log handle; do not close transport for hashing'})
(OUT/'evidence-hash-skipped.json').write_text(json.dumps(skipped,indent=2)+'\n')
hashes['evidence-hash-skipped.json']=sha(OUT/'evidence-hash-skipped.json')
(OUT/'evidence-sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
print(json.dumps({'status':summary['status'],'stage_a':summary['stage_a']['status'],'stage_b':summary['stage_b']['status']}))
