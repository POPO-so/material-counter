"""Close out Retry 2 using raw board evidence; never reinterpret preflight as hardware proof."""
import hashlib,json,subprocess
from pathlib import Path
from datetime import datetime,timezone,timedelta
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
MAIN=Path(r'C:\fpga\material-counter')
def load(n):return json.loads((OUT/n).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
initial=load('initial-snapshot.json')
repair=load('request-001-stdout.txt')
static=load('request-003-stdout.txt')
offline=load('board-offline-result.json')
overlay=load('overlay-only-result.json')
after=load('exit-os-result.json')
binding=load('authorization-rebind-reference.json')
assert initial['boot_id']==static['live_boot_id']==after['boot_id']==binding['authorized_boot_id']
assert repair['status']=='PERMISSION_REPAIR_PASS'
assert static['status']=='STATIC_CONFIGURATION_PASS'
assert static['manifest_boot_id']==static['executor_boot_id']==binding['authorized_boot_id']
assert all(uid==1000 for uid in static['config_owner_uids'].values())
assert offline['status']=='OFFLINE_PREFLIGHT_PASS'
assert overlay['status']=='OVERLAY_ONLY_REJECTED' and not overlay['board_accessed'] and not overlay['pynq_imported']
assert overlay['error']=='PreflightError: CMA reserved-memory tree is unavailable: /proc/device-tree/reserved-memory'
assert not after['device_tree_paths']['/proc/device-tree/reserved-memory']['exists']
assert not after['device_tree_paths']['/sys/firmware/devicetree/base/reserved-memory']['exists']
assert after['ownership']['findings']==[] and after['ownership']['denied']==[]
assert not after['guards']
assert len(list(OUT.glob('request-[0-9][0-9][0-9].json')))==5
for n,h in after['deployed_sha256'].items():assert h==binding['after'][n],(n,h)
assert sha(Path(binding['original_zip']))==binding['original_zip_sha256']
history={}
for name in ('wo0015-first-board-20261015','wo0015-first-board-retry1-20261015'):
    directory=OUT.parent/name
    index=json.loads((directory/'evidence-sha256.json').read_text())
    assert all(sha(directory/n)==h for n,h in index.items())
    history[name]={'unchanged':True,'files_checked':len(index)}
heads={}
for label,path in [('main',MAIN),('worktree',ROOT)]:
    heads[label]=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
    (OUT/(label+'-git-after.txt')).write_text(subprocess.check_output(['git','-C',str(path),'status','--porcelain=v1'],text=True),encoding='utf-8')
    delta=subprocess.check_output(['git','-C',str(path),'diff','--name-status','HEAD'],text=True)
    (OUT/(label+'-tracked-diff.txt')).write_text(delta,encoding='utf-8')
    assert not delta
    subprocess.run(['git','-C',str(path),'diff','--check'],check=True,capture_output=True)
(OUT/'initial-dmesg.txt').write_text(initial['dmesg'],encoding='utf-8')
(OUT/'postflight-dmesg.txt').write_text('\n'.join(after['kernel_log'])+'\n',encoding='utf-8')
(OUT/'proc-iomem.txt').write_text(after['iomem'],encoding='utf-8')
(OUT/'proc-cmdline.txt').write_text(after['cmdline'],encoding='utf-8')
ping_rc=int((OUT/'post-ping-exit-code.txt').read_text().strip())
summary={'status':'FAIL_STOPPED','stopped_at':'STEP_1_RUNTIME_CMA_TOPOLOGY_PREFLIGHT_BEFORE_OVERLAY',
'stage_result':overlay,'boot_id':after['boot_id'],'permission_repair':'PASS','configuration_update':'PASS',
'board_sha256':after['deployed_sha256'],'manifest_sha256':binding['manifest_sha256'],
'cma_initial_kib':initial['meminfo'],'cma_pre_overlay_kib':static['meminfo'],'cma_exit_kib':after['cma'],
'stage_invocations':{'overlay-only':1,'dma-single':0,'dma-three':0},
'overlay_download_count':0,'overlay_load_elapsed_s':None,'cma_allocations':0,
'mmio_reads':0,'mmio_writes':0,'frames_sent':0,'frames_received':0,
'dma_start_attempted':False,'transfer_started':False,'dma_may_be_active':False,
'guard_files':after['guards'],'ownership':after['ownership'],'fpga_manager':after['fpga_manager'],
'post_ping_exit_code':ping_rc,'history':history,'git_heads':heads,
'original_zip_unchanged':True,'functional_logic_unchanged':True,'committed':False,'merged':False}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
now=datetime.now(timezone(timedelta(hours=8))).isoformat()
report=f'''# WO-0015 首次上板 Retry 2 核验报告

生成时间：{now}（Asia/Shanghai）。最终裁决：**FAIL_STOPPED**。

停止于 **Step 1：运行时 CMA 物理范围准入，实际 Overlay 加载之前**。权限修复、配置更新、板端静态核对和 offline 均通过。`overlay-only` 命令仅调用一次，返回 `OVERLAY_ONLY_REJECTED`、exit code 2：

```text
{overlay['error']}
board_accessed=false
pynq_imported=false
```

因此实际 Overlay.download() 为 0 次，未打开 MMIO、未分配 CMA、未启动 DMA。Step 2 / Step 3 未执行。没有自动重试、改写 CMA 检查、替换范围来源、复位 DMA 或重载 Overlay。本次是软件运行时准入拒绝，不是 Overlay 或 DMA 硬件功能失败。

## 权限修复与配置更新

仅在明确批准的 `/home/xilinx/wo0015_first_board_retry1_20261015` 上执行一次 `chown -R --no-dereference xilinx:xilinx`。执行前检查精确路径、所有子路径均位于目标目录内、无符号链接、boot 正确、Guard 为空、BIT/HWH 摘要正确。修复后核对 UID/GID=1000、文件属主读写位；随后使用 xilinx SFTP 成功安装已审查的三个配置文件。静态检查确认配置文件 UID 均为 1000，manifest、执行器授权常量和实时 boot 均为 `{after['boot_id']}`。

本次沿用 Retry 1 已离线审查的配置，manifest SHA-256 为 `{binding['manifest_sha256']}`。只涉及已批准的 boot 常量、manifest 和派生摘要；RTL、功能逻辑、BIT/HWH、packet/Golden、打包逻辑、固定 Guard 与测试判定标准均未修改。原 ZIP 与两个历史证据目录的全部已索引文件摘要复核无变化。

## 板端环境与 CMA

| 项目 | 首次只读准入 | 配置后、Overlay 命令前 | 退出只读检查 |
|---|---|---|---|
| boot_id | `{initial['boot_id']}` | 相同 | `{after['boot_id']}` |
| CmaTotal | {initial['meminfo']['CmaTotal']} KiB | {static['meminfo']['CmaTotal']} KiB | {after['cma']['CmaTotal']} KiB |
| CmaFree | {initial['meminfo']['CmaFree']} KiB | {static['meminfo']['CmaFree']} KiB | {after['cma']['CmaFree']} KiB |
| FPGA manager | {initial['manager_state']} / flags={initial['manager_flags']} | operating / flags=0 | {after['fpga_manager']['state']} / flags={after['fpga_manager']['flags']} |
| FPGA 设备占用 | 阶段前静态扫描通过 | findings=[] / denied=[] | findings=[] / denied=[] |

内核：`{after['kernel_release']}`。`CmaTotal=131072 KiB` 表明系统报告 128 MiB CMA，不能据此确定其物理起止地址或证明 HP 可达性。本轮退出 CmaFree 相对首次快照变化 {after['cma']['CmaFree']-initial['meminfo']['CmaFree']} KiB；本次没有分配任何缓冲，不据此推断泄漏或分配行为。

只读现场证实 `/proc/device-tree` 存在并指向 `/sys/firmware/devicetree/base`；两个路径下的 `reserved-memory` 均不存在。`/proc/iomem` 报告 System RAM 为 `00000000-1fffffff`，本轮未从中确定 CMA 子区间。执行器 `board_preflight.py` 的 `read_cma_ranges()` 在目录缺失时直接拒绝；未进入 PYNQ 导入、Overlay 或 consumed Guard 创建。因此三个硬件 Guard 均为空，未删除或更名任何 Guard。

退出阶段只读取 Linux 文件、进程表与 dmesg；没有 MMIO。FPGA FD/maps 归属扫描通过，未发现占用。板端短时 Python 检查已正常退出，无本次待释放 CMA 对象。已关闭本地凭据窗口并结束 SSH。退出 Ping 返回码 {ping_rc}。

## 工件实测摘要

以下值在 chown 前后、配置后与退出时核对一致：

| 工件 | 板端实测 SHA-256 | 判定 |
|---|---|---|
| wo15.bit | `{after['deployed_sha256']['wo15.bit']}` | 与授权值一致 |
| wo15.hwh | `{after['deployed_sha256']['wo15.hwh']}` | 与授权值一致 |

新 manifest：`{after['deployed_sha256']['run-manifest.json']}`。

`board_once.py`：`{after['deployed_sha256']['board_once.py']}`（功能执行器原样）。`board_preflight.py`：`{after['deployed_sha256']['board_preflight.py']}`（仅已批准的 boot 常量更新）。全包 `SHA256SUMS.json` 逐文件校验通过。

## 三阶段结果与证据边界

| 阶段 | 命令调用次数 / 结果 | 寄存器、统计和 Golden |
|---|---|---|
| Step 1 | 1 次，OVERLAY_ONLY_REJECTED；实际加载 0 次 | CMA 范围门控失败，未读签名、版本、DMA 状态或 Checker/Packer |
| Step 2 | 0 次，NOT_RUN | 未启动 MM2S/S2MM；回收 0 字节；Golden 未比较 |
| Step 3 | 0 次，NOT_RUN | 未启动 MM2S/S2MM；回收 0 字节；Golden 未比较 |

Overlay 加载耗时不适用。`request-005-transport.json` 的 {load('request-005-transport.json')['elapsed_s']:.6f} 秒是被拒绝命令的总耗时，不能写作加载耗时。本轮没有 DMA 状态字、Checker/Packer 计数、唯一 EOF 或零差异结果。原 RTL 无独立版本寄存器；计划核对的身份为 WO14 Checker / WO15 Packer 签名，本轮未实际读取。

## 完整文件与分支

本地完整目录：`{OUT}`。

核验报告：`verification-report.md`；机器可读裁决：`summary.json`；板端原始结果：`board-offline-result.json`、`overlay-only-result.json`、`exit-os-result.json`；全过程命令与日志：`request-001` 至 `request-005`、`failure-readonly-*`、`events.jsonl`；现场：`initial-dmesg.txt`、`postflight-dmesg.txt`、`proc-iomem.txt`、`proc-cmdline.txt`；完整摘要索引：`evidence-sha256.json`。

板端保留原部署目录，已修复的授权配置保留，没有任何硬件 Guard 或待释放 DMA 缓冲。原开发目录 HEAD：`{heads['main']}`；worktree HEAD：`{heads['worktree']}`。两处 tracked diff 均为空，git diff --check 通过，未提交、未合并、未暂存。本次仅新增 Retry 2 结果目录，历史证据保留。

后续需要先核实当前内核的实际 CMA 物理区间及可验证的数据来源，并审查执行器与该内核的兼容性；CmaFree 充足不能替代范围证据。本轮不修改执行器或绕过范围门控来继续。
'''
(OUT/'verification-report.md').write_text(report,encoding='utf-8')
index={str(p.relative_to(OUT)):sha(p) for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='evidence-sha256.json' and '__pycache__' not in p.parts}
(OUT/'evidence-sha256.json').write_text(json.dumps(index,indent=2),encoding='utf-8')
print(json.dumps({'status':summary['status'],'report':str(OUT/'verification-report.md'),'cma_exit':after['cma'],'files_hashed':len(index)},indent=2))
