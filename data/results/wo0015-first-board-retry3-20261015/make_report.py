"""Close out the approved one-shot Retry 3 from raw evidence, without another board operation."""
import hashlib,json,subprocess,xml.etree.ElementTree as ET,zipfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
MAIN=Path(r'C:\fpga\material-counter')
def load(n):return json.loads((OUT/n).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
initial=load('initial-snapshot.json')
static=load('request-002-stdout.txt')
exit_os=load('exit-os-result.json')
package=load('updated-bundle-receipt.json')
stages=exit_os['stage_results']
for n,result in stages.items():(OUT/n).write_text(json.dumps(result,indent=2),encoding='utf-8')
overlay=stages['overlay-only-result.json']
state=stages['initial-state-result.json']
assert overlay['status']=='OVERLAY_LOAD_PASS'
assert state['status']=='INITIAL_STATE_FAIL_STOPPED'
assert state['errors']==['MM2S Halted != 0','S2MM Halted != 0']
assert state['dma']['0x04']==state['dma']['0x34']==1
assert state['dma']['0x00']==state['dma']['0x30']==0x10002
assert state['checker']['0x04']==0x574f3134 and state['checker']['0x60']==0x574f3135
assert initial['boot_id']==state['boot_id']==exit_os['boot_id']==package['packaged_boot_id']
assert static['cma_admission']['source']=='MEMINFO_IOMEM_VERIFIED'
assert static['cma_admission']['permitted_buffer_ranges']==[[0x00100000,0x20000000]]
assert not static['cma_admission']['physical_cma_range_known']
assert exit_os['ownership']['findings']==[] and exit_os['ownership']['denied']==[]
assert len(list(OUT.glob('request-[0-9][0-9][0-9].json')))==5
assert 'dma-single-result.json' not in stages and 'dma-three-result.json' not in stages
guards={Path(n).name:json.loads(v) for n,v in exit_os['guards'].items()}
assert set(guards)=={'overlay-only-pass.json','overlay-only-consumed.json','terminal-failure.json'}
assert guards['terminal-failure.json']['stage']=='initial-state'
assert guards['terminal-failure.json']['status']=='TERMINAL_FAILURE'
for n,h in exit_os['deployed_sha256'].items():
    assert sha(Path(package['bundle'])/n)==h
assert sha(Path(package['archive']))==package['archive_sha256']
with zipfile.ZipFile(package['archive']) as z:
    sums=json.loads(z.read('SHA256SUMS.json'))
    assert all(hashlib.sha256(z.read(n)).hexdigest()==h for n,h in sums.items())
testroot=ET.parse(OUT/'full-offline-final.xml').getroot()
suites=list(testroot.iter('testsuite'))
assert sum(int(s.attrib['tests']) for s in suites)==436
assert all(int(s.attrib[k])==0 for s in suites for k in ('errors','failures','skipped'))
wo_tests=sum('scripts.wo0015.' in t.attrib.get('classname','') for t in testroot.iter('testcase'))
assert wo_tests==127,wo_tests
history={}
for name in ('wo0015-first-board-20261015','wo0015-first-board-retry1-20261015','wo0015-first-board-retry2-20261015','wo0015-cma-compat-20261015'):
    directory=OUT.parent/name
    index=json.loads((directory/'evidence-sha256.json').read_text())
    assert all(sha(directory/n)==h for n,h in index.items())
    history[name]={'unchanged':True,'files_checked':len(index)}
heads={}
for label,path in [('main',MAIN),('worktree',ROOT)]:
    heads[label]=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
    (OUT/(label+'-git-after.txt')).write_text(subprocess.check_output(['git','-C',str(path),'status','--porcelain=v1'],text=True),encoding='utf-8')
    subprocess.run(['git','-C',str(path),'diff','--check'],check=True,capture_output=True)
(OUT/'initial-dmesg.txt').write_text(initial['dmesg'],encoding='utf-8')
(OUT/'postflight-dmesg.txt').write_text('\n'.join(exit_os['kernel_log'])+'\n',encoding='utf-8')
(OUT/'raw-registers.json').write_text(json.dumps({'boot_id':state['boot_id'],'checker':state['checker'],'dma':state['dma'],'checker_hex':{k:f'0x{v:08x}' for k,v in state['checker'].items()},'dma_hex':{k:f'0x{v:08x}' for k,v in state['dma'].items()}},indent=2),encoding='utf-8')
(OUT/'guard-snapshot.json').write_text(json.dumps(guards,indent=2),encoding='utf-8')
ping_rc=int((OUT/'post-ping-exit-code.txt').read_text().strip())
overlay_pre=overlay['runtime_preflight']['cma_admission']
summary={'status':'FAIL_STOPPED','stopped_at':'STEP_1_INITIAL_DMA_HALTED_GATE_AFTER_OVERLAY_PASS',
'boot_id':exit_os['boot_id'],'tier1':'PASS','tier1_source':'MEMINFO_IOMEM_VERIFIED',
'safety_envelope':[0x00100000,0x20000000],'exact_cma_subregion_known':False,
'overlay_load':'PASS','initial_dma_state':'FAIL_HALTED','overlay_download_count':1,
'stage_invocations':{'overlay-only':1,'initial-state-readonly':1,'dma-single':0,'dma-three':0},
'dma_start_attempted':False,'transfer_started':False,'tx_rx_allocate_calls':0,
'tx_rx_physical_addresses':None,'tier2_board_result':'NOT_RUN','golden_comparison':'NOT_RUN',
'cma_initial_kib':initial['meminfo'],'cma_static_kib':static['meminfo'],
'cma_overlay_preflight_kib':{'CmaTotal':overlay_pre['CmaTotal_kib'],'CmaFree':overlay_pre['CmaFree_kib']},
'cma_initial_mmio_kib':state['cma'],'cma_exit_kib':exit_os['cma'],
'dma_registers':state['dma'],'checker_registers':state['checker'],
'board_sha256':exit_os['deployed_sha256'],'full_offline_tests_passed':431,'additional_subtests_passed':5,
'wo0015_tests_passed':wo_tests,'new_two_tier_tests':12,'post_ping_exit_code':ping_rc,
'post_ownership':exit_os['ownership'],'guards':guards,'package':package,'history':history,
'git_heads':heads,'committed':False,'merged':False}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
now=datetime.now(timezone(timedelta(hours=8))).isoformat()
report=f'''# WO-0015 首次上板 Retry 3 核验报告

生成时间：{now}（Asia/Shanghai）。最终裁决：**FAIL_STOPPED**。

停止位置：**Step 1：Overlay 加载与签名通过之后，补充 DMA 初始就绪核对未通过**。新的两级 CMA 架构已落地，板端 Tier 1 PASS；锁定 50 MHz 工件实际加载一次，签名符合预期，但两个通道实测 Halted=1，不满足本次正式授权的 Halted=0 准入条件。已立即阻断 Step 2 / Step 3，无自动重试、RUN 修复写入、DMA 复位或 Overlay 重载。

## 本次架构与离线验证

本次队长正式授权缺少精确静态 CMA 子区间时采用内核记账加 DDR 安全包络。`MEMINFO_IOMEM_VERIFIED` 要求 System RAM 精确为 `[0,0x20000000)`、CmaTotal=131072 KiB、CmaFree>=32768 KiB，允许包络 `[0x00100000,0x20000000)`，并保持 `physical_cma_range_known=false`。有精确元数据时仍优先验证；显式冲突的 cma= 信息仍拒绝。本次包络不是 CMA 子区间实测。

Tier 2 检查在任何寄存器写入前核对 allocator 给出的 physical_address 与 device_address 一致、地址不低于 1 MiB、整段落在 DDR/HP 包络、长度精确、MM2S 8 字节/S2MM 4 字节对齐、两段不重叠；MM2S 8 字节约束保留 HWH 中无 DRE 的 64-bit 内存端口要求。证据新增分配前、TX 后、RX 后、释放后 CmaFree，转移前要求总下降至少覆盖两个页取整缓冲的 304 KiB，正常 PASS 要求最终余量恢复原值。全局 CmaFree 仅是分配记账佐证，不能单独证明某一进程的地址所有权。

新增两级规则测试 12 项，首次 RED 为 6 failed / 6 passed，具体缺口随后修复。最终全仓 `python/`、`scripts/` 回归 **431 passed + 5 subtests passed**，其中 WO-0015 **127 项通过**。首次全仓在未修改的 WO-0014 40 ms deadline 测试失败，已保留原始日志；单独复核通过，随后完整复跑全绿。未改动该旧测试或其代码。新包 PC offline / Mock 通过，均不是板端 DMA 证据。

独立 Worker 审查了门控与 HOLD 路径。Step 1 的初态检查由一次补充只读快照强制执行；失败时记录 terminal-failure，且本地调度器必须存在 Step 1 acceptance 才能发单帧命令。本次未生成 Step 1 acceptance。未知在途 DMA 仍须保留缓冲/进程、不得释放或复位；本次没有进入 DMA，在途风险为否。

## 工件、部署与 boot 身份

新部署目录 `/home/xilinx/wo0015_first_board_retry3_20261015`，由 xilinx 上传并以 xilinx 解压，配置及执行文件 UID=1000，后续硬件命令使用 sudo。不存在前次 root 解压导致的配置覆盖失败。整个新包 SHA256SUMS 在板端逐项核对，退出时再次实测核心文件摘要一致。

boot 全程为 `{exit_os['boot_id']}`。新 manifest `{package['manifest_sha256']}`。BIT/HWH 保持原锁定字节：

| 工件 | 板端退出时实测 SHA-256 |
|---|---|
| wo15.bit | `{exit_os['deployed_sha256']['wo15.bit']}` |
| wo15.hwh | `{exit_os['deployed_sha256']['wo15.hwh']}` |
| board_once.py | `{exit_os['deployed_sha256']['board_once.py']}` |
| board_preflight.py | `{exit_os['deployed_sha256']['board_preflight.py']}` |

新包 ZIP：`{package['archive']}`；SHA-256 `{package['archive_sha256']}`。本次源码修改仅为 CMA/Tier 2 与相关测试/说明，功能 RTL、BIT/HWH、Golden、打包入口和完成/数据判定不变；包内 boot 配置按此前与本次批准绑定，源码默认历史 boot 未改。原包、原离线包和全部历史结果逐项复核无覆盖。

## Step 1 实测

1. Tier 1 静态结果 `STATIC_TIER1_PASS`，source=MEMINFO_IOMEM_VERIFIED。所有文件摘要、boot、进程 FD/maps、manager 均通过。
2. `overlay-only` 调用一次，实际下载一次，返回 `OVERLAY_LOAD_PASS`。仅执行器读取两个签名。随后一次补充快照读取 Checker/Packer 和 DMA CR/SR/地址/长度，未写 DMA 寄存器。
3. 实测 Checker signature **0x574F3134**、Packer signature **0x574F3135**。RTL 没有独立版本寄存器；没有伪造版本读数。

| 项目 | MM2S | S2MM |
|---|---|---|
| DMACR | 0x00010002 | 0x00010002 |
| DMASR | 0x00000001 | 0x00000001 |
| Run/Stop（CR bit 0） | 0 | 0 |
| Halted（SR bit 0） | **1，门控失败** | **1，门控失败** |
| Idle（SR bit 1） | 0，首次传输前本次规则允许 | 0，首次传输前本次规则允许 |
| Err | 0 | 0 |
| IOC | 0 | 0 |
| SA / DA | 0 | 0 |
| LENGTH | 0 | 0 |

初态快照的 errors 为 `MM2S Halted != 0`、`S2MM Halted != 0`。没有通过补写 Run=1 或 reset 把现场改成期望状态。上述停止只证明未满足本轮初始就绪要求，**不能认定 DMA 传输发生故障，因为没有启动传输**。

Checker status/error、packet_done/error、输入字节/握手/TLAST、像素/SOF/EOL/checksum/累计输出计数均为 0。Packer status=0、accepted_pixels=0、sent_beats=0、error=0。完整快照在 `raw-registers.json` 与 `initial-state-result.json`。

Overlay 命令从传输发出到退出总耗时 **{load('request-004-transport.json')['elapsed_s']:.6f} 秒**，包括 PYNQ 导入与前后审计；实际 Overlay.download() 调用未单独计时，不能把这个数值称为纯加载耗时。内核原始日志包含 fpga_manager 写入 wo15.bin 和 xclbin 装载事件，保留于后检与 dmesg 文件。执行器 dmesg 增量审计为 PASS；厂商驱动的原始 skip-kind 等消息完整保留，未改写为“零日志”。

## Step 2 / Step 3 与 Tier 2 板端证据

| 阶段 | 调用次数 | 结果 |
|---|---|---|
| Step 1 Overlay | 1 | Overlay/签名 PASS；组合 Step 1 因 Halted FAIL_STOPPED |
| Step 1 初态只读快照 | 1 | INITIAL_STATE_FAIL_STOPPED |
| Step 2 单帧 | 0 | NOT_RUN |
| Step 3 三帧 | 0 | NOT_RUN |

未调用 `pynq.allocate()` 申请 TX/RX，故没有实际缓冲 phys_addr，也没有本次“分配前/中/释放后”的 Tier 2 板端样本。未下发帧、未回收数据，Golden **未比较**；不能填入零差异结论。Mock 中的模拟地址、CMA 数值和三帧计数没有冒充本节实测。

## 环境与退出资源检查

| 快照点 | CmaTotal（KiB） | CmaFree（KiB） |
|---|---|---|
| 首次只读连接 | {initial['meminfo']['CmaTotal']} | {initial['meminfo']['CmaFree']} |
| 新包静态审计 | {static['meminfo']['CmaTotal']} | {static['meminfo']['CmaFree']} |
| Overlay 执行器加载前准入 | {overlay_pre['CmaTotal_kib']} | {overlay_pre['CmaFree_kib']} |
| 加载后初态 MMIO 快照 | {state['cma']['CmaTotal']} | {state['cma']['CmaFree']} |
| 退出只读审计 | {exit_os['cma']['CmaTotal']} | {exit_os['cma']['CmaFree']} |

这些是系统/驱动全局余量快照；本次没有用户 TX/RX 分配周期，不能据此认定本次 DMA 泄漏或已验证分配回收。首次至退出净变化 {exit_os['cma']['CmaFree']-initial['meminfo']['CmaFree']} KiB；没有臆测变化原因。

退出 boot 未变，FPGA manager=`{exit_os['fpga_manager']['state']}`、flags=`{exit_os['fpga_manager']['flags']}`。root 级设备 FD/maps 归属扫描 findings=[]、denied=[]；板端短时 Python 检查正常退出，SSH 与本地凭据窗口均已关闭。没有本次 TX/RX 对象需要释放。退出 Ping 返回码 {ping_rc}。没有触发不确定 DMA HOLD，也没有强杀可能持有 DMA 缓冲的进程。

Guard 现场保留：`overlay-only-consumed.json`、`overlay-only-pass.json`、`terminal-failure.json`。terminal stage=`initial-state`，reason=`MM2S Halted != 0; S2MM Halted != 0`，身份绑定相同 boot/manifest/BIT/HWH。没有单帧或三帧 consumed Guard，未删除、改名或改换 Guard 路径。

## 结果路径、分支与后续边界

完整目录：`{OUT}`。

主要文件：`summary.json`、`verification-report.md`、`static-check` 对应的 `request-002-stdout.txt`、`offline-result.json`、`overlay-only-result.json`、`initial-state-result.json`、`raw-registers.json`、`guard-snapshot.json`、`exit-os-result.json`、`initial-dmesg.txt`、`postflight-dmesg.txt`、`request-001` 至 `request-005`、`failure-readonly-*`、`full-offline-final.*`、`evidence-sha256.json`。

原目录 HEAD `{heads['main']}`；worktree HEAD `{heads['worktree']}`。保持未提交、未合并、未暂存，git diff --check 通过。正式 WO-0015 文件原为未跟踪，实际变化由 before 文件、原包对比 `.diff` 与 `scope-audit.json` 留证，未用空 tracked diff 隐藏脚本修改。

本轮已结束硬件推进。若后续要从当前 Run/Stop=0、Halted=1 状态建立运行态，需要队长单独裁决允许的 DMA 控制寄存器初始化与现有 terminal/consumed Guard 处理；本轮没有执行这些额外操作，也没有把首次 Halted=1 自动豁免为 PASS。
'''
(OUT/'verification-report.md').write_text(report,encoding='utf-8')
index={str(p.relative_to(OUT)):sha(p) for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='evidence-sha256.json' and '__pycache__' not in p.parts}
(OUT/'evidence-sha256.json').write_text(json.dumps(index,indent=2),encoding='utf-8')
print(json.dumps({'status':summary['status'],'stopped_at':summary['stopped_at'],'wo_tests':wo_tests,'files_hashed':len(index),'report':str(OUT/'verification-report.md')},indent=2))
