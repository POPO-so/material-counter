"""Summarize captured evidence only; no board access or Git mutation."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
REMOTE='/home/xilinx/wo0015_runonly_resume_retry1_20261015'
def sha(data):return hashlib.sha256(data).hexdigest()
def load(name):return json.loads((OUT/name).read_bytes())
initial=load('initial-snapshot.json')
exit=load('exit-os-result.json')
run=load('remote-evidence/run-only-result.json')
single=load('remote-evidence/dma-single-result.json')
step1=load('remote-evidence/step1-acceptance.json')
locks=load('locked-evidence.json')
templates=load('command-templates.json')
assert initial['guard_sha256']==locks['guard_sha256']
assert exit['deployed_sha256']==locks['deployed_sha256']
assert run['status']=='RUN_ONLY_PASS' and step1['status']=='PASS'
assert single['status']=='DMA_SINGLE_FAIL' and not single['transfer_started'] and single['frames']==[]
assert not single['dma_start_attempted'] and not single['dma_may_be_active']
assert exit['ownership']['findings']==[] and exit['ownership']['denied']==[]
assert 'error' not in exit['final_registers']
assert exit['boot_id']==initial['boot_id']=='822a9c29-37c0-438c-81c4-da998d57073b'
for number,op in [(1,'deploy'),(2,'run-only'),(3,'archive'),(4,'dma-single')]:
    assert load(f'request-{number:03d}.json')==templates[op]
assert not (OUT/'request-005.json').exists()
assert load('failure-evidence-request.json')['command']==templates['exit-failure']['command']
for n,h in load('launch-lock.json').items():
    assert sha((OUT/n).read_bytes())==h
    assert sha((OUT/'remote-evidence'/n).read_bytes())==h
old_terminal='/var/lib/wo0015-s2mm/history/20261015-retry3-initial-state-terminal-failure.json'
assert exit['guards'][old_terminal]['sha256']==locks['guard_sha256']['terminal-failure.json']
for name in ('overlay-only-pass.json','overlay-only-consumed.json'):
    assert exit['guards']['/var/lib/wo0015-s2mm/'+name]['sha256']==locks['guard_sha256'][name]
assert '/var/lib/wo0015-s2mm/dma-three-consumed.json' not in exit['guards']
new_terminal=json.loads(base64.b64decode(exit['guards']['/var/lib/wo0015-s2mm/terminal-failure.json']['base64']))
assert new_terminal['stage']=='dma-single' and new_terminal['status']=='TERMINAL_FAILURE'
history={}
for name in ('wo0015-first-board-retry3-20261015','wo0015-runonly-proposal-20261015','wo0015-runonly-resume-20261015'):
    directory=OUT.parent/name
    index=json.loads((directory/'evidence-sha256.json').read_bytes())
    assert all(sha((directory/n).read_bytes())==h for n,h in index.items())
    history[name]=len(index)
for label,path in [('main',Path('C:/fpga/material-counter')),('worktree',ROOT)]:
    (OUT/(label+'-git-after.txt')).write_text(subprocess.check_output(['git','-C',str(path),'status','--porcelain=v1'],text=True),encoding='utf-8')
    subprocess.run(['git','-C',str(path),'diff','--check'],check=True,capture_output=True)
summary={'status':'FAIL_STOPPED','stopped_at':'Step 2 / pre-DMA CmaFree allocation-accounting gate',
         'boot_id':exit['boot_id'],'step1':'PASS (Resumed via Run-only)',
         'run_only':'PASS','historical_guard_archive':'PASS','step2':'DMA_SINGLE_FAIL_PRE_TRANSFER',
         'step3':'NOT_EXECUTED','run_only_cr_writes':2,'dma_frames_started':0,
         'allocated_buffers':2,'free_calls_returned':2,'physical_addresses_read':False,
         'tier2_address_gate_executed':False,'Golden_comparison':'NOT_EXECUTED',
         'CmaTotal_kib':exit['cma']['CmaTotal'],'CmaFree_initial_kib':initial['meminfo']['CmaFree'],
         'CmaFree_frame_before_kib':single['cma_free_before_kib'],
         'CmaFree_after_release_kib':single['cma_free_after_kib'],'CmaFree_exit_kib':exit['cma']['CmaFree'],
         'CmaFree_exact_restoration_proven':False,'hardware_ownership_exit':exit['ownership'],
         'terminal_guard':new_terminal,'deployed_sha256':exit['deployed_sha256'],
         'offline_tests':63,'history_index_files_unchanged':history,
         'post_ping_exit_code':int((OUT/'post-ping-exit-code.txt').read_text().strip()),
         'cleanup':load('local-console-cleanup.json')}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
def hex32(n):return f'0x{n:08x}'
dma_rows=[]
for offset,label in [(0,'MM2S_DMACR'),(4,'MM2S_DMASR'),(0x18,'MM2S_SA'),(0x28,'MM2S_LENGTH'),
                     (0x30,'S2MM_DMACR'),(0x34,'S2MM_DMASR'),(0x48,'S2MM_DA'),(0x58,'S2MM_LENGTH')]:
    key=str(offset)
    dma_rows.append(f"| {label} / 0x{offset:02x} | {hex32(run['before']['dma'][key])} | {hex32(run['after']['dma'][key])} | {hex32(exit['final_registers']['dma'][f'0x{offset:02x}'])} |")
checker_rows=[]
for offset,value in run['before']['checker_raw'].items():
    checker_rows.append(f"| {offset} | {hex32(value)} | {hex32(run['after']['checker_raw'][offset])} | {hex32(exit['final_registers']['checker'][offset])} |")
cma_rows='\n'.join(f"| Step 2 {s['stage']} | {s['cma_free_kib']} |" for s in single['cma_allocation_samples'])
guard_rows='\n'.join(f"| `{name}` | `{r['sha256']}` |" for name,r in exit['guards'].items())
reg_table='\n'.join(dma_rows);checker_table='\n'.join(checker_rows)
durations={op:load(f'request-{n:03d}-transport.json')['elapsed_s'] for n,op in [(1,'deploy'),(2,'run-only'),(3,'archive'),(4,'dma-single')]}
report=f'''# WO-0015 Run-only 恢复 Retry 1 核验报告

日期：2026-10-15（Asia/Shanghai）

最终裁决：**FAIL_STOPPED，停留于 Step 2 的分配后、DMA 启动前 CMA 计量门控**。Run-only 和历史 Guard 归档均 PASS，Step 1 为 **PASS (Resumed via Run-only)**；单帧 DMA 未启动，三帧未执行。

## 环境与锁定身份

| 项目 | 实测/执行结果 |
|---|---|
| boot_id（前后相同） | `{exit['boot_id']}` |
| CmaTotal | {exit['cma']['CmaTotal']} KiB |
| 首次 CmaFree | {initial['meminfo']['CmaFree']} KiB |
| 最终 CmaFree | {exit['cma']['CmaFree']} KiB |
| FPGA manager | operating / flags=0 |
| Overlay 新加载 / 软复位 | 0 / 0；无本次加载耗时 |
| SSH | 一次连接；严格指纹匹配；隐藏输入密码 |
| 退出占用扫描 | findings=[]，denied=[] |
| 退出 Ping | exit code={summary['post_ping_exit_code']} |

板端已部署文件由适配器重复对原始文件字节计算 SHA-256；最终取证再次复核一致：

- `wo15.bit`：`{exit['deployed_sha256']['wo15.bit']}`。
- `wo15.hwh`：`{exit['deployed_sha256']['wo15.hwh']}`。
- manifest：`{exit['deployed_sha256']['run-manifest.json']}`。
- 原 `board_once.py`：`{exit['deployed_sha256']['board_once.py']}`。
- 原 `board_preflight.py`：`{exit['deployed_sha256']['board_preflight.py']}`。

Tier 1 来源为 `MEMINFO_IOMEM_VERIFIED`：实测 System RAM 为 `[0x00000000,0x20000000)`，CmaTotal=131072 KiB，执行前 CmaFree 达阈值；安全包络 `[0x00100000,0x20000000)`。这不是实测 CMA 子区间。完整解析、PYNQ 已加载 BIT 路径/hash、manager、进程占用和内核增量证据在 `remote-evidence/run-only-result.json`、`step1-acceptance.json`。

## 阶段 1：Run-only PASS

三份 Guard 的摘要直接在板端对 `read_bytes()` 计算，并回传 Base64 原始字节由主机再次核验，未执行 strip 或文本换行转换。摘要全部与授权历史值一致。

新受限脚本在独立目录 `{REMOTE}` 部署，启动器固定摘要核对全部源码、锁配置，再执行；适配器重复检查源码与原部署工件摘要。请求命令已逐项核对等于生成模板。离线边界/模型/采集/启动篡改拦截/证据原样导出共 **63 passed**。

仅执行两次控制写入：S2MM `0x40400030 = 0x00010003`，随后 MM2S `0x40400000 = 0x00010003`。两份写入意图在写入前落盘，新增固定 `run-only-init-consumed.json` 保留。

| 寄存器 | Run-only 前 | Run-only 后 | 最终取证 |
|---|---|---|---|
{reg_table}

后检两通道 Halted=0、Err=0；SR=0 表示本次尚无 IOC/Idle 完成状态。初次 Idle=0 被允许；这不是帧传输完成。地址、LENGTH、Checker/Packer 计数为零。Run-only 前后与归档前的 dmesg 增量健康检查 PASS。

## 阶段 2：历史 Guard 归档 PASS

旧 terminal 原始字节以独占创建方式复制到 `{old_terminal}`，fsync、重读核对原始 SHA 后，才移除原 canonical terminal。属主 uid/gid=0/0，mode=0600 保留。

旧标记 SHA-256：`{locks['guard_sha256']['terminal-failure.json']}`；最终板端历史文件再次验证相同。原 Overlay consumed Guard 与 Overlay PASS 收据保持字节不变。恢复收据绑定旧 Overlay PASS、新 Run-only PASS、原 terminal hash 与归档位置。

## 阶段 3：Step 2 单帧在 CMA 计量门控 FAIL_STOPPED

原 `board_once.py` 只调用一次，退出码 3。两次 allocate() 已返回，随后执行既有分配计量检查：要求 CmaFree 至少下降两缓冲页对齐总量 304 KiB。实际为：

| 采样 | CmaFree / KiB |
|---|---|
| 初次连接 | {initial['meminfo']['CmaFree']} |
| Run-only before | {run['before']['CmaFree']} |
| Run-only after | {run['after']['CmaFree']} |
| 归档前复核 | {step1['postflight']['CmaFree']} |
{cma_rows}
| 最终 OS 采样 | {exit['cma']['CmaFree']} |

`37884 - 38612 = -728 KiB`，未达 `>=304 KiB`，因此抛出 `CmaFree did not show the required allocated-page decrease before DMA`。全局 CmaFree 在采样期间上升；它不能证明每个缓冲区的底层分配归属，也不能据此推断泄漏。没有修改或放宽这一检查。

失败发生在读取 `physical_address` 与执行地址 Tier 2 之前，因此 **MM2S/S2MM 物理地址未记录，Tier 2 地址校验未执行**。目标缓冲长度为 TX230432 / RX76800 字节。没有执行 Packer ARM、SA/DA/LENGTH 或此帧的 DMACR 写入。

执行器字段：`dma_start_attempted=false`、`transfer_started=false`、`dma_may_be_active=false`、`frames=[]`。S2MM 回收字节为 0，Checker/Packer 本次接受帧数和 EOF 次数为 0；没有 Golden 比对，不能报告“零差异 PASS”。本次结果属于软件准入失败，不构成 DMA 传输结果。

## 阶段 4：Step 3 未执行

Step 2 未 PASS，未创建三帧请求，未生成 dma-three consumed Guard，未分配三帧缓冲区，回收字节与 Golden 结果不适用。

## Checker/Packer 全寄存器只读快照

此 IP 无单独版本寄存器；Checker WO14 / Packer WO15 签名为设计身份。0x00 为既有控制值 1，0x04/0x60 为签名，其余寄存器在三个快照中均为零，包含输入字节/拍/TLAST、packet_done、像素/SOF/EOL/校验和、Packer 状态/像素/拍/错误。

| offset | Run-only 前 | Run-only 后 | 最终取证 |
|---|---|---|---|
{checker_table}

## 失败 Guard 与资源收尾

最终 dmesg 增量另由已部署同源检查函数离线核对，结果见 `exit-kernel-health.json`；该检查仅评价内核日志，不改变 Step 2 失败裁决。

单帧执行器已重新建立 canonical `terminal-failure.json`，stage=`dma-single`，reason 为上述 CMA 计量失败；最终取证保留该原标记，没有覆盖历史归档。Run-only 与单帧 consumed Guard 均保留，三帧 absent。

| Guard 路径 | 最终原始 SHA-256 |
|---|---|
{guard_rows}

失败清理路径依次调用 `free(s2mm_buffer)` 和 `free(mm2s_buffer)`；`after_release` 是两次 free() 均返回后记录。最终 root /proc FD/maps 扫描 findings=[]、denied=[]，单帧进程已退出，未进入 FAIL_HOLD。SSH 已正常关闭，本地凭据控制器控制台已结束，无硬件进程被强制终止。

**CmaFree 未精确恢复至单帧分配前值**（37884→38612 KiB；最终全局采样54448 KiB），因此不声称满足“CMA 原值恢复”验收，也不以全局回升替代单缓冲物理回收证明。当前证据支持两次正常释放调用与无残留设备句柄。

## 归档与复核

各阶段均只调用一次，无自动重试、重载、复位或现场修改原执行器、RTL、BIT/HWH。新脚本与证据只写入独立恢复目录。原开发目录和 worktree 未提交、未合并、未暂存；前后 Git 状态已保存，diff --check 通过。

三个历史索引复核文件数：`{json.dumps(history)}`，全部未变化。

远端命令总耗时（含 Python/PYNQ 初始化与采集，不能解释为纯 Run 写入耗时）：`{json.dumps(durations)}` 秒。

完整本地结果目录：`{OUT}`。

完整远端结果目录：`{REMOTE}`；root 文件均通过 sudo 只读 Base64 导出至本地 `remote-evidence/` 并逐文件核验 SHA-256，未通过文本改写或权限放宽获取。

主要证据：`initial-snapshot.json`、`request-002-stdout.txt`、`request-003-stdout.txt`、`request-004-stdout.txt`、`exit-os-result.json`、`remote-evidence/`、`offline-final.xml`、`summary.json`、`evidence-sha256.json`。当前保持 FAIL_STOPPED，后续操作需另行裁决，不能直接重试 Step 2/3。
'''
(OUT/'verification-report.md').write_text(report,encoding='utf-8')
index={str(p.relative_to(OUT)):sha(p.read_bytes()) for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='evidence-sha256.json' and '__pycache__' not in p.parts}
(OUT/'evidence-sha256.json').write_text(json.dumps(index,indent=2),encoding='utf-8')
print(json.dumps({'status':summary['status'],'stopped_at':summary['stopped_at'],'files_hashed':len(index),'report':str(OUT/'verification-report.md')},indent=2))
