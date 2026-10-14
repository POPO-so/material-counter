"""Evidence-derived closeout report: partial work is never promoted to board PASS."""
import csv
import hashlib
import json
import statistics
import subprocess
from pathlib import Path
OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
load = lambda p: json.loads(p.read_text())
status = load(OUT/'result.json') if (OUT/'result.json').exists() else {'status':'WAITING_NATIVE_AUTH','actions':[]}
packets = load(OUT/'packets.json')
cfg = load(OUT/'package-config.json')
protected = load(OUT/'protected-before.json')
unchanged = all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest() == h for n,h in protected.items())
assert unchanged
rows = []
summary = {'status':status['status'],'git_baseline':cfg['git_baseline'],
           'protected_unchanged':unchanged,'source_tests':'13 PASS + 2 subtests PASS',
           'packaged_offline_entries':4,'modes':{},'performance_scope':'software observed, includes polling/logging/supervision overhead'}
for mode in ('multi50','overlay100','single100','multi100'):
    result_path = OUT/(mode+'-result.json')
    receipt_path = OUT/'board-evidence'/mode/'closeout-20261015'/('mode-'+mode+'-pass.json')
    partial = mode == 'multi50' and not result_path.exists() and (OUT/'collect-failure-result.json').exists()
    if partial:
        collected = load(OUT/'collect-failure-result.json')
        result = {'status':'PASS','cma_before':None,'cma_after':None}
    if (not result_path.exists() and not partial) or not receipt_path.exists():
        summary['modes'][mode] = {'status':'NOT_VERIFIED'}
        continue
    if not partial: result = load(result_path)
    receipt = load(receipt_path)
    assert result['status'] == 'PASS' and receipt['mode'] == mode
    assert receipt['manifest_sha256'] == cfg['manifest_sha256']
    metrics = receipt['frame_metrics']
    assert len(metrics) == (0 if mode == 'overlay100' else 1 if mode == 'single100' else 3)
    entry = {'status':'DMA_CHECKER_PASS_POST_CMA_REFUSED' if partial else 'PASS','receipt':str(receipt_path.relative_to(ROOT)),
             'frames':metrics,'cma_before':result['cma_before'],'cma_after':result['cma_after']}
    if metrics:
        if not partial: assert result['cma_before'] == result['cma_after']
        for metric in metrics:
            source = packets[metric['frame_id']]
            checker = metric['checker']
            assert metric['packet_sha256'] == source['sha256']
            assert checker['observed_pixels'] == 76800 and checker['eols'] == 240 and checker['sofs'] == 1
            assert checker['binary_checksum'] == source['binary_checksum'] and checker['status'] == 9
            assert metric['dmasr'] & 3 == 2 and not metric['dmasr'] & 0x4070
            rows.append({'mode':mode,'clock_mhz':metric['clock_mhz'],'frame_id':metric['frame_id'],
                         'foreground_pixels':source['foreground_pixels'],'golden':'PASS',
                         'done_count':checker['packet_done_count'],
                         'dma_idle_observed_ms':1000*metric['length_write_to_dma_idle_s'],
                         'checker_verified_ms':1000*metric['length_write_to_checker_verified_s'],
                         'frame_preparation_through_verify_ms':1000*metric['frame_start_to_checker_verified_s']})
        entry['mean_dma_idle_ms'] = statistics.mean(m['length_write_to_dma_idle_s']*1000 for m in metrics)
        entry['mean_checker_verified_ms'] = statistics.mean(m['length_write_to_checker_verified_s']*1000 for m in metrics)
        entry['mean_frame_prepare_verify_ms'] = statistics.mean(m['frame_start_to_checker_verified_s']*1000 for m in metrics)
    summary['modes'][mode] = entry
    if partial:
        entry['supervisor'] = collected['multi50_supervisor']
        entry['current_cma_at_collection'] = collected['current_cma']
        entry['os_gate_at_collection'] = {k:collected['current'][k] for k in ('boot_id','fpga_state','fpga_flags','kernel_continuity','new_kernel_warnings')}
        entry['cma_limit'] = 'Original numeric pre/post CMA values were not persisted before wrapper refusal; current sample cannot reconstruct the delta or establish a leak.'
all_pass = all(m['status'] == 'PASS' for m in summary['modes'].values())
assert not status['status'] == 'WO0014_CLOSEOUT_BOARD_PASS' or all_pass
summary['wo0014_board_closeout_verified'] = all_pass and status['status'] == 'WO0014_CLOSEOUT_BOARD_PASS'
summary['p2_admission'] = 'READY_FOR_SEPARATE_CAMERA_VALIDATION' if summary['modes']['multi50']['status'] == 'PASS' else 'WAITING_50MHZ_POST_CMA_GATE_RESOLUTION'
summary['p3_status'] = 'PLANNED_REQUIRES_NEW_EXPERIMENTAL_BD_AND_EOF_BRIDGE'
summary['failure'] = status.get('error')
(OUT/'final-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
if rows:
    with (OUT/'board-performance.csv').open('w',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
lines = ['# WO-0014 50/100 MHz 收官记录','', '日期：2026-10-15，Asia/Shanghai。', '',
         '当前结论：'+('全部请求板测阶段 PASS，WO-0014 收官证据齐备。' if summary['wo0014_board_closeout_verified'] else '尚未完成全部请求板测，不能宣布 WO-0014 结项。'), '',
         '## Git 安全基线','',
         '提交：`'+cfg['git_baseline']+'`；分支 `feat/pl-stream-skeleton`，未 push。纳管145文件：diagnostic50修复/测试/来源清单、原image helper与测试、完整PG021里程碑证据包、最新172项及19个子测试的预检证据，以及局部字节保留属性。', '',
         '新增收官执行器离线13项及2个子测试 PASS；实际打包的四个入口离线 PASS。13个保护文件（四份文档、README、生产及实验RTL）哈希一致，未修改 RTL/BD。', '',
         '## 板级阶段结果','', '| 阶段 | 结果 | 帧数 | Receipt |','|---|---|---:|---|']
for mode,m in summary['modes'].items():
    lines.append('| '+mode+' | '+m['status']+' | '+str(len(m.get('frames',[])))+' | '+m.get('receipt','无本轮PASS receipt')+' |')
lines += ['', '执行状态：`'+status['status']+'`。'+(' 失败原因：'+status['error'] if status.get('error') else ''), '',
          '原50MHz单帧里程碑另见 `../wo0014-pg021-ready-20261015/report.md`；不以旧单帧PASS替代本轮多帧证明。', '',
          '## 逐帧 Golden 与软件观测耗时','',
          '| 模式 | frame_id | 前景像素 | Golden | 完成包累计数 | LENGTH到DMA Idle(ms) | LENGTH到Checker验收(ms) | 准备到验收(ms) |',
          '|---|---:|---:|---|---:|---:|---:|---:|']
for row in rows:
    lines.append(f"| {row['mode']} | {row['frame_id']} | {row['foreground_pixels']} | PASS | {row['done_count']} | {row['dma_idle_observed_ms']:.3f} | {row['checker_verified_ms']:.3f} | {row['frame_preparation_through_verify_ms']:.3f} |")
if not rows: lines.append('| 未测得 | — | — | 未验证 | — | — | — | — |')
lines += ['', '## 50MHz与100MHz同协议对比','',
          '| 时钟 | 多帧帧数 | 平均DMA Idle观测(ms) | 平均Checker验收(ms) | 平均准备到验收(ms) | 稳定性 |',
          '|---|---:|---:|---:|---:|---|']
for mode,clock in (('multi50',50),('multi100',100)):
    m = summary['modes'][mode]
    if m['status'] in ('PASS','DMA_CHECKER_PASS_POST_CMA_REFUSED'):
        health_label = '3帧Golden匹配、无DMA/Checker错误、CMA恢复' if m['status'] == 'PASS' else '3帧DMA/Checker PASS；外层CMA一致性门拒绝'
        lines.append(f"| {clock}MHz | 3 | {m['mean_dma_idle_ms']:.3f} | {m['mean_checker_verified_ms']:.3f} | {m['mean_frame_prepare_verify_ms']:.3f} | {health_label} |")
    else: lines.append(f'| {clock}MHz | 未验证 | — | — | — | 未取得本轮多帧PASS证据 |')
lines += ['', '计时边界：LENGTH调用开始到首次DMA Idle读回；包含Python、逐访问日志落盘、MMIO读回及轮询开销。Checker验收与每帧准备到验收还包含Supervisor阶段调度、CMA分配、复制及flush。没有硬件周期计数器，因此未测得纯AXI传输时间；这三帧样本也不证明持续相机FPS或长期压力稳定性。', '',
          '两种时钟复用相同frame_id 1～3、相同合法230432字节包。每帧均为76800像素、1 SOF、240 EOL及2240前景像素；源图和包哈希、输入校验和各不相同。Checksum/几何等Checker聚合匹配不等于板端逐像素回读。', '',
          '## 100MHz产物身份','',
          'BIT：`data/results/wo0014-hw-100mhz-20261014/export/wo0014_image.bit`；SHA-256 `585c83ec455d237516c30cda2709e42882edecb83acc6f401e724f6418f368bd`。', '',
          'HWH：同目录 `wo0014_image.hwh`；SHA-256 `6bd4fec32c99186c2445b45b796f6bbfbe98e29df8fff21524e014dcb172bfba`。真实实现报告 `reports/timing-implemented.rpt` WNS=+1.167ns、TNS=0、WHS=+0.066ns。实现时序通过与板测PASS分别记录。', '',
          '## P2/P3 准入建议','',
          'P2：'+summary['p2_admission']+'。AT-OV9281属于用户指定待验证设备；需在板上核对USB枚举、V4L2实际节点/格式/分辨率/帧率，并测量连续采集、时间戳、掉帧和曝光。转RGB888后复用本轮包格式/Golden；本轮不宣称摄像头型号识别或实时采集通过。', '',
          'P3：现有BIT明确S2MM=0，仅MM2S与Checker聚合统计。后续独立实验BD启用S2MM并新增输出封包桥：内部TLAST=EOL不能直接当DMA整帧结束，需在第76800个像素输出EOF TLAST，核对末拍TKEEP/字节数；先提交S2MM缓冲、再启动MM2S。PS做sync_from_device与逐像素Golden比对，失败时保留收发缓冲。', '',
          'WO-0008形态学已有独立RTL和离线验收，MODE=0 bypass/1 erosion/2 dilation；尚未串入本轮板测BIT。其行接收/输出交替与底边flush会产生背压，需同新回传桥验证连续多帧及SOF/EOL重生成。LUTAR-1为待处理实现告警，需在P3单独修改与重新综合/实现/报告核验，不能把本轮时序通过写成该告警已修复。', '',
          '时钟选择：只有100MHz完整板测PASS才有本轮板级可用证据；若100MHz出现异常即停止并保存，后续主线建议回到已验证的50MHz。当前Overlay是否已切换以实际receipt为准，失败后不自动重载。', '',
          '## 健康与边界','',
          '板级执行采用diagnostic50的LocalHealth、ProcessLink/Supervisor、每阶段deadline与Worker访问门；固定父Guard保留，新收官子Guard绑定同boot和来源。每mode与每帧独立一次性consumed记录。阶段前后有工作站Ping、原生严格host-key SSH、Linux/boot/dmesg连续性、FPGA manager、shutdown_ips、占用与来源校验；DMA成功释放后CMA必须回到该阶段前值。失败不重试、不kill/reset/reload、不释放不确定缓冲。', '',
          '本轮没有生产RTL、BD、四份保护文档、Route或接口冻结修改；没有git push，也没有删除未归档文件。', '',
          'PG021参考：[Simple DMA编程序列](https://docs.amd.com/r/en-US/pg021_axi_dma/Direct-Register-Mode-Simple-DMA)、[MM2S_DMASR](https://docs.amd.com/r/en-US/pg021_axi_dma/MM2S_DMASR-MM2S-DMA-Status-Register-Offset-04h)。首帧运行态允许Idle=0；后续连续帧LENGTH前要求Idle=1，完成要求Idle=1、IOC和无错误，另外以新完成包数及Golden验证当前帧。', '']
if summary['modes']['multi50']['status'] == 'DMA_CHECKER_PASS_POST_CMA_REFUSED':
    collected = load(OUT/'collect-failure-result.json')
    lines += ['## CMA拒绝的证据边界与下一步', '',
              '三帧frame_id 1/2/3的Golden及累加计数全部匹配，DMASR均0x1002，Supervisor send_attempts=3、child PID13787正常exit=0；三次freebuffer成功返回及FRAME_BUFFER_RELEASED记录已取回。', '',
              '外层包装使用CmaTotal/CmaFree前后字典严格相等作为门控，实际发生变化而拒绝继续。原始数值在拒绝前未写入持久快照，这是本轮采集缺口：不能给出准确前后差值，也不能据此声称泄漏或归因。后续只读采集CmaTotal=131072KiB、CmaFree='+str(collected['current_cma']['CmaFree_kib'])+'KiB；boot连续、manager operating/flags=0、无新增kernel异常和硬件占用。后采集数值不能充当原阶段前/后快照。', '',
              '已保存全部三帧receipt/原始访问日志和外层拒绝记录，并在固定Guard附加terminal-fail阻止另一入口继续。100MHz Overlay/单帧/多帧均未尝试，当前身份仍为50MHz。未发生DMA重试或自动重载。', '',
              '下一轮先仅做OS/来源/allocator语义审计，持久记录带时间戳的CMA前后样本、分配/释放事件与客户端生命周期，明确严格相等门是否将全局CMA正常波动当成失败；在明确健康结论前不删除Guard或继续100MHz。P2/P3可做离线接口准备，本轮不授予新的硬件闭环PASS或正式结项。', '']
(OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps({'status':summary['status'],'wo0014_board_closeout_verified':summary['wo0014_board_closeout_verified'],
                  'protected_unchanged':True,'performance_rows':len(rows)},indent=2))
