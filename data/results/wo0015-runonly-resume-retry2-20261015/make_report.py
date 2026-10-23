"""Report captured evidence; this host-only program never accesses the board."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
REMOTE='/var/lib/wo0015-s2mm/continuations/watermark-retry2-20261015'
def sha(raw):return hashlib.sha256(raw).hexdigest()
def load(name):return json.loads((OUT/name).read_bytes())
initial=load('initial-snapshot.json');exit=load('exit-os-result.json')
stage1=load('remote-evidence/step1-acceptance.json');single=load('remote-evidence/dma-single-result.json')
locks=load('locked-evidence.json');launch=load('launch-lock.json');templates=load('command-templates.json')
assert initial['guard_sha256']==locks['guard_sha256']
assert exit['deployed_sha256']==locks['deployed_sha256']
assert initial['boot_id']==exit['boot_id']==locks['boot_id']
assert stage1['status']=='PASS' and stage1['mmio_writes']==stage1['overlay_loads']==0
assert single['status']=='DMA_SINGLE_FAIL' and single['byte_mismatches']==39520 and single['first_mismatch']==4
assert single['transfer_started'] and not single['dma_may_be_active']
assert all(s['cma_free_kib']>=16384 for s in single['cma_allocation_samples'])
assert single['post_alloc_physical_gate']['status']=='POST_ALLOC_PHYSICAL_GATE_PASS'
assert (OUT/'board_once_watermark.py').read_bytes()==(ROOT/'scripts/wo0015/board_once.py').read_bytes()
for n,h in launch.items():
    assert sha((OUT/n).read_bytes())==h and sha((OUT/'remote-evidence'/n).read_bytes())==h
for number,op in [(1,'deploy'),(2,'transition'),(3,'dma-single')]:
    assert load(f'request-{number:03d}.json')==templates[op]
assert not (OUT/'request-004.json').exists()
assert load('failure-evidence-request.json')['command']==templates['exit-failure']['command']
guard_prefix='/var/lib/wo0015-s2mm/'
marker=json.loads(base64.b64decode(exit['guards'][guard_prefix+'terminal-failure.json']['base64']))
assert marker['stage']=='dma-single' and '39520' in marker['reason']
for name in ('overlay-only-pass.json','overlay-only-consumed.json','run-only-init-consumed.json','dma-single-consumed.json'):
    assert exit['guards'][guard_prefix+name]['sha256']==locks['guard_sha256'][name]
assert guard_prefix+'dma-single-watermark-retry2-consumed.json' in exit['guards']
assert guard_prefix+'dma-three-watermark-retry2-consumed.json' not in exit['guards']
assert guard_prefix+'dma-single-watermark-retry2-pass.json' not in exit['guards']
assert exit['ownership']['findings']==[] and exit['ownership']['denied']==[]
suite=next(ET.parse(OUT/'full-offline-final.xml').getroot().iter('testsuite'))
assert int(suite.attrib['failures'])==int(suite.attrib['errors'])==0
assert int((OUT/'full-offline-final-exit-code.txt').read_text())==0
assert load('offline-continuation-result.json')['status']=='OFFLINE_CONTINUATION_MOCK_PASS'
history={}
for name in ('wo0015-first-board-retry3-20261015','wo0015-runonly-proposal-20261015',
             'wo0015-runonly-resume-20261015','wo0015-runonly-resume-retry1-20261015'):
    d=OUT.parent/name;index=json.loads((d/'evidence-sha256.json').read_bytes())
    assert all(sha((d/n).read_bytes())==h for n,h in index.items());history[name]=len(index)
for label,path in [('main',Path('C:/fpga/material-counter')),('worktree',ROOT)]:
    (OUT/(label+'-git-after.txt')).write_text(subprocess.check_output(['git','-C',str(path),'status','--porcelain=v1'],text=True),encoding='utf-8')
    subprocess.run(['git','-C',str(path),'diff','--check'],check=True,capture_output=True)
regs=exit['final_registers'];gate=single['post_alloc_physical_gate']
summary={'status':'FAIL_STOPPED','stopped_at':'Step 2 / S2MM Binary8 Golden comparison',
         'boot_id':exit['boot_id'],'stage1_precheck_archive':'PASS','step2':'FAIL_GOLDEN','step3':'NOT_EXECUTED',
         'overlay_loads':0,'run_only_writes':0,'dma_frames_started':1,'s2mm_actual_bytes':76800,
         'byte_mismatches':39520,'first_mismatch':4,'raw_s2mm_binary_preserved':False,
         'post_alloc_gate':gate,'cma_samples':single['cma_allocation_samples'],
         'CmaTotal_kib':exit['cma']['CmaTotal'],'CmaFree_initial_kib':initial['meminfo']['CmaFree'],
         'CmaFree_exit_kib':exit['cma']['CmaFree'],'final_dma':regs['dma'],'final_checker':regs['checker'],
         'ownership_exit':exit['ownership'],'terminal_failure':marker,'free_calls_returned':2,
         'dma_may_be_active':False,'full_offline_tests':'448 passed + 5 subtests',
         'watermark_regressions':17,'continuation_boundary_tests':6,'offline_mock_frames':4,
         'new_executor_sha256':launch['board_once_watermark.py'],'base_deployed_sha256':exit['deployed_sha256'],
         'history_files_verified':history,'post_ping_exit_code':int((OUT/'post-ping-exit-code.txt').read_text()),
         'local_cleanup':load('local-console-cleanup.json')}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
def hx(v):return f'0x{v:08x}'
dma_names={0:'MM2S_DMACR',4:'MM2S_DMASR',0x18:'MM2S_SA',0x28:'MM2S_LENGTH',
           0x30:'S2MM_DMACR',0x34:'S2MM_DMASR',0x48:'S2MM_DA',0x58:'S2MM_LENGTH'}
dma_table='\n'.join(f"| {n} | {hx(stage1['dma'][str(o)])} | {hx(regs['dma'][f'0x{o:02x}'])} |" for o,n in dma_names.items())
checker_table='\n'.join(f"| {o} | {hx(stage1['checker'][o])} | {hx(v)} / {v} |" for o,v in regs['checker'].items())
cma_table='\n'.join(f"| {s['stage']} | {s['cma_free_kib']} |" for s in single['cma_allocation_samples'])
physical_table='\n'.join(f"| {n} | [{hx(gate[k][0])}, {hx(gate[k][1])}) | {gate[k][1]-gate[k][0]} |" for n,k in [('MM2S','mm2s_physical_range'),('S2MM','s2mm_physical_range')])
guard_table='\n'.join(f"| `{n}` | `{r['sha256']}` |" for n,r in exit['guards'].items())
report=f'''# WO-0015 CMA 水位门控修正与恢复 Retry 2 核验报告

日期：2026-10-15（Asia/Shanghai）

最终裁决：**FAIL_STOPPED，停在 Step 2 的 S2MM Binary8 与 Golden 全量比对**。本轮已完成一次真实单帧传输并回收 76800 字节，发现 **39520 字节差异，首个差异 offset=4（从 0 开始）**；未执行 Step 3。此次已不再是软件 CMA 算术差值准入失败，不能将传输完成等同于数据闭环 PASS。

## 环境、工件与资源

| 项目 | 实测结果 |
|---|---|
| boot_id（前后相同） | `{exit['boot_id']}` |
| CmaTotal | 131072 KiB |
| 首次连接 CmaFree | {initial['meminfo']['CmaFree']} KiB |
| Pre-check CmaFree | {stage1['cma']['CmaFree']} KiB |
| 退出 CmaFree | {exit['cma']['CmaFree']} KiB |
| FPGA manager | operating / flags=0 |
| Overlay 加载 / Reset / Run-only 重新写入 | 0 / 0 / 0；本次加载耗时不适用 |
| 退出进程 FD/maps 占用 | findings=[]，denied=[] |
| 退出 Ping | exit code={summary['post_ping_exit_code']} |

板端 Pre-check 和退出均复核原部署工件字节摘要：

- BIT：`{exit['deployed_sha256']['wo15.bit']}`。
- HWH：`{exit['deployed_sha256']['wo15.hwh']}`。
- base manifest：`{exit['deployed_sha256']['run-manifest.json']}`。

上述摘要严格匹配授权。已加载 BIT 路径仍为 `/home/xilinx/wo0015_first_board_retry3_20261015/wo15.bit`，PYNQ global state 的路径/hash、manager 与 boot 均通过。Tier 1 为 `MEMINFO_IOMEM_VERIFIED`，System RAM 精确 `[0,0x20000000)`，安全包络 `[0x00100000,0x20000000)`，未声称获知静态 CMA 子区间。

## Tier 2 修正与离线验证

仅修改 `scripts/wo0015/board_once.py` 中 run_frames、PynqBoardAPI.device_address、MockBoardAPI.__init__，加 16 MiB 常量和整数地址解析；README 同步更新，新增 `test_cma_watermark.py`。RTL、BIT/HWH 和原板端部署文件均未修改。

取消“分配时至少下降304 KiB”和“释放后精确恢复原值”硬门控，将差值和页对齐字节数仅作遥测。分配前、每次分配后、释放后均须至少 16384 KiB；物理地址必须是非零整数，physical/device 地址相同；严格验证下限1 MiB、512 MiB DDR上界、尺寸、对齐与不重叠。MM2S保留8字节对齐要求，S2MM为4字节。Tier 1 仍要求至少32768 KiB。

17项新回归先观察 RED（11 failed、6 passed），修正后17 passed；全量最终 **448 passed + 5 subtests**，6项续执行归档/边界测试通过，实际续执行 wrapper 的 Mock 回放共4帧零差异、4次free、0次Overlay加载。首次全量中旧WO-0014短时限测试曾出现1项已知抖动，日志保留；定向复查和完整重跑通过，未修改旧测试。

## 身份链与单次权限

原 manifest 仍绑定旧部署作为工件/基础身份；实际执行的是独立、字节同于已修正源代码的 `board_once_watermark.py`，其 SHA-256 为 `{launch['board_once_watermark.py']}`，另由 `continuation-execution.json` 和主机固定启动摘要绑定，不能误称旧 manifest 单独绑定了新执行器。

执行与证据在 root 管理的 `{REMOTE}`。上传先落独立暂存目录，ZIP一次读入、验证固定SHA后从同一字节流解包。启动器核对OUT与祖先root属主、无group/other写权限、无符号链接，持有flock，执行同一份已核验脚本字节；重读配置/执行器再次回核固定摘要。Step 3还要求单帧续执行PASS收据绑定新executor/config/result摘要。

旧 Overlay PASS、Overlay consumed、Run-only consumed 和旧单帧 consumed 均原样保留。旧单帧token继续阻断旧执行器；本次明确授权使用固定 `dma-single-watermark-retry2-consumed.json` / `dma-three-watermark-retry2-consumed.json`，不允许换路径或自动重试。每条请求已核对等于固定模板。

## 阶段 1：Pre-check 与历史归档 PASS

三步之前只读确认两CR=0x10003、两SR=0、SA/DA/LENGTH=0、Checker/Packer初始零计数，签名、CMA、占用与内核增量检查PASS。没有重新写DMACR、重载或软复位。

上一轮 CMA 算术失败的 terminal 与单帧consumed原始字节复制至 `/var/lib/wo0015-s2mm/history/20261015-retry1-cma-accounting/`，目标独占创建、fsync、重读校验摘要、保留uid/gid/mode。只移除当前旧terminal，旧single consumed canonical仍保留。恢复once Guard和Step1收据均持久化。

## 阶段 2：单帧真实传输，Golden FAIL

实际一次调用，`transfer_started=true`、`dma_start_attempted=true`、`dma_may_be_active=false`，退出码3。Tier 2已PASS，实际地址与尺寸：

| 缓冲 | 物理区间（左闭右开） | nbytes |
|---|---|---|
{physical_table}

两区间在DDR包络内、对齐、尺寸精确、不重叠；S2MM回收76800字节/19200 words。遥测差值为 -676 KiB，不再据此误熔断。

| CMA 采样 | CmaFree / KiB |
|---|---|
{cma_table}

四次水位均PASS；最终OS另采样52852 KiB。全局值不等于原值不再是错误，也不能据此单独证明每一缓冲区的内核页归属。此次没有直接追踪/证实页迁移过程，按授权修正规则处理全局计数。

软件先通过DMA Idle+新IOC+无错误、Packer DONE/计数/无错、Checker当前帧/累计计数、S2MM实际长度检查，随后在全量回读比对失败。停止后的独立寄存器快照进一步确认：

| 项目 | 停止后实测 |
|---|---|
| MM2S/S2MM DMASR | 0x00001002 / 0x00001002：Idle=1、IOC=1、Halted=0、Err=0 |
| Checker status / error | 0x9 / 0 |
| packet_done / packet_error | 1 / 0 |
| 输入字节 / 输入拍 / 输入TLAST | 230432 / 57608 / 1 |
| 像素 / SOF / EOL | 76800 / 1 / 240 |
| output_total / output_stalls | 76800 / 0 |
| output checksum / 前景像素 | 571200 / 2240 |
| Packer status / pixels / beats / error | DONE(2) / 76800 / 19200 / 0 |
| Golden | **39520/76800字节不一致，首差offset=4，FAIL** |

这些是停止后取证值，不虚构比对瞬间的完整帧收据。`frames=[]` 是因为原执行器在Golden成功之后才append帧记录，不能据此认为“没有传输”。输入TLAST=1是输入包统计，Packer没有独立输出EOF事件计数寄存器；可观测完成证据为DONE、19200拍及S2MM精确长度，不能冒充额外测得的EOF计数器。

**证据限制：现有失败路径只保存差异数量与首偏移，没有持久化完整RX二进制、差异位置列表或失败RX摘要。** 缓冲已释放，本轮不会读已释放物理范围、补跑或重建伪原始数据；本报告不凭计数推断差异根因。

## 阶段 3：三帧未执行

未创建三帧请求、三帧consumed或PASS收据；没有三帧地址/恢复/Golden验证结果，不能以离线Mock代替。

## 完整寄存器快照

| DMA寄存器 | Pre-check | 停止后 |
|---|---|---|
{dma_table}

| Checker/Packer offset | Pre-check | 停止后 hex / decimal |
|---|---|---|
{checker_table}

0x04=WO14 Checker签名，0x60=WO15 Packer签名；RTL无独立版本寄存器。完整原始环境、内核日志和寄存器在 `exit-os-result.json`。

## 失败 Guard、清理与历史保留

当前terminal已重新立起，stage=`dma-single`，reason为Golden的39520字节差异。新的单帧 consumed保留，无新单帧PASS，三帧guard不存在。旧归档均保留。

| Guard/历史路径 | 原始SHA-256 |
|---|---|
{guard_table}

异常发生于DMA已完成后的比较，未进入FAIL_HOLD；两次free均返回后记录after_release=37232 KiB。最终进程FD/maps扫描为空，manager正常，内核增量健康 `exit-kernel-health.json` 为 `{load('exit-kernel-health.json')['status']}`；远端命令已退出，SSH已关闭，本地凭据控制台已结束，未强制终止硬件占用进程。

所有旧结果索引均复核未变：`{json.dumps(history)}`。未提交、未合并、未暂存；前后Git状态、实际source差异已保存，git diff --check通过。硬件失败后未修改板端代码/工件或执行重试。

完整本地结果目录：`{OUT}`。

板端root结果目录：`{REMOTE}`；经sudo只读Base64导出，主机逐文件核验原始SHA，原件在 `remote-evidence/`。本目录包含完整stdout/stderr、传输耗时、Guard原始字节、寄存器、dmesg、摘要索引与测试日志。

当前维持 **FAIL_STOPPED**。单帧计数与DMA完成证据不等于S2MM数据闭环通过；后续调查/硬件操作必须另行裁决，本轮不继续。
'''
(OUT/'verification-report.md').write_text(report,encoding='utf-8')
index={str(p.relative_to(OUT)):sha(p.read_bytes()) for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='evidence-sha256.json' and '__pycache__' not in p.parts}
(OUT/'evidence-sha256.json').write_text(json.dumps(index,indent=2),encoding='utf-8')
print(json.dumps({'status':summary['status'],'mismatches':39520,'evidence_files':len(index),'report':str(OUT/'verification-report.md')},indent=2))
