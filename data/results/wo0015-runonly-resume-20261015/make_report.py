import hashlib
import json
from pathlib import Path
import subprocess

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
PREV=OUT.parent/'wo0015-first-board-retry3-20261015'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
raw=json.loads((OUT/'initial-snapshot.json').read_text())
diagnosis=json.loads((OUT/'collector-format-diagnosis.json').read_text())
assert all(x['one_lf_restores_expected'] for x in diagnosis['rows'].values())
for name,path in [('main',Path('C:/fpga/material-counter')),('worktree',ROOT)]:
    (OUT/(name+'-git-after.txt')).write_text(subprocess.check_output(['git','-C',str(path),'status','--porcelain=v1'],text=True),encoding='utf-8')
    subprocess.run(['git','-C',str(path),'diff','--check'],check=True,capture_output=True)
old_index=json.loads((PREV/'evidence-sha256.json').read_text())
assert all(sha(PREV/n)==h for n,h in old_index.items())
summary={'status':'FAIL_STOPPED','stopped_at':'Stage 1 / R0 host initial read-only Guard comparison',
         'boot_id':raw['boot_id'],'CmaTotal_kib':raw['meminfo']['CmaTotal'],
         'CmaFree_initial_kib':raw['meminfo']['CmaFree'],'CmaFree_exit_kib':None,
         'root_cause':'Host collector read().strip() removed terminal newline before raw-byte SHA comparison.',
         'actual_board_guard_change_established':False,'raw_board_bytes_reverified':False,
         'run_only_writes':0,'adapter_deployed':False,'overlay_loads':0,'cma_allocations':0,
         'dma_frames_started':0,'archive_executed':False,'step1':'NOT_RESUMED',
         'step2':'NOT_EXECUTED','step3':'NOT_EXECUTED','offline_tests_passed':59,
         'historical_retry3_files_unchanged':len(old_index),
         'corrected_collector_executed':False,'no_automatic_retry':True,
         'board_connection_attempts':1,'initial_readonly_exit_code':0,
         'post_ping_exit_code':int((OUT/'post-ping-exit-code.txt').read_text().strip()),
         'cleanup':json.loads((OUT/'local-console-cleanup.json').read_text())}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
rows='\n'.join(f"| {n} | `{r['stripped_sha256']}` | `{r['locked_raw_sha256']}` | 是 |" for n,r in diagnosis['rows'].items())
report=f'''# WO-0015 Run-only 恢复执行核验报告

日期：2026-10-15（Asia/Shanghai）

最终裁决：**FAIL_STOPPED**。停留于阶段 1 / R0 的主机只读采集 Guard 摘要检查；未执行 Run-only，Step 1 未恢复，Step 2/3 均未调用。此次是主机采集器检查失败，不能解读为 DMA 硬件异常或板端 Guard 确已变化。

## 授权与边界

用户已批准完整 Run-only、条件归档及原单帧/三帧恢复；本次采用新的独立结果目录。严格单次停止后没有再次连接板卡或启动修正版；既有 Overlay、所有 Guard、原 Retry 3 报告与证据均未被本次修改。

锁定 boot：`822a9c29-37c0-438c-81c4-da998d57073b`。

BIT 授权摘要：`a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420`。

HWH 授权摘要：`bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6`。

上述工件摘要沿用已锁定证据；本次在重新计算板端 BIT/HWH 摘要之前停止，不能列为本次重新实测。已复核原 Retry 3 索引 {len(old_index)} 份文件均未变化。

## 本次实测环境

一次严格 SSH 指纹认证成功；一次 root 只读采集正常退出，exit code=0。密码通过原生隐藏提示输入，未写入日志。

| 项目 | 本次结果 |
|---|---|
| boot_id | `{raw['boot_id']}` |
| CmaTotal | {raw['meminfo']['CmaTotal']} KiB |
| CmaFree（唯一采样） | {raw['meminfo']['CmaFree']} KiB |
| FPGA manager | {raw['manager_state']} / flags={raw['manager_flags']} |
| Overlay 加载次数 / 耗时 | 0 / 不适用 |
| 退出后 Ping | exit code={summary['post_ping_exit_code']} |

原始 uname、进程表、dmesg 和三个 Guard 的被截尾文本均保存在 `initial-snapshot.json`；完整传输 stdout/stderr 与认证事件另存。CmaFree 大于准入阈值，但尚未完成 Tier 1 全部证据与 /proc FD/maps 占用扫描，不能称完整 Pre-check PASS。没有退出 CMA 二次采样，不声称全局 CmaFree 恢复到某值。

## 根因与摘要差异

复用的主机控制器内嵌只读采集函数 `read(p)` 返回 `Path(p).read_text().strip()`。此函数适合 boot_id 等标量，但用于 Guard 文件时去掉末尾 LF；控制器随后以该截尾字符串重新计算 SHA-256，与方案锁定的原始字节摘要不同而停止。这是本次主机执行实现的缺陷。

| Guard | 被截尾文本 SHA-256 | 历史原始字节 SHA-256 | 补一个 LF 后匹配历史摘要 |
|---|---|---|---|
{rows}

三份文本都在补一个 LF 后精确匹配历史原始字节摘要。该分析充分解释了采集器变换造成的差异，**不替代重新读取板端原始字节**。因此本次没有根据补 LF 推断继续写寄存器，也没有归档/删除/改写任何板端 Guard。现场旧 terminal marker 仍发挥阻断作用，无需为本次零硬件访问新建或替换它。

## 寄存器、物理地址与数据结果

| 阶段 | 本次动作 | 结果 |
|---|---|---|
| Run-only 前后寄存器 | 未打开 MMIO，零读写 | 无本次寄存器快照 |
| 历史失败 Guard 归档 | 未调用 | 原标记未改动 |
| 单帧 | 未分配 TX/RX，未调用执行器 | 物理地址、Checker/Packer、Golden 均 N/A |
| 三帧 | 未分配 TX/RX，未调用执行器 | 物理地址、Checker/Packer、Golden 均 N/A |

上次 Retry 3 的 CR=0x10002、SR=1 仅是历史值，不能充当本次前后对比。本次无 Run-only 写入、Overlay 加载、软复位、SA/DA/LENGTH 写入或数据传输。

## 离线准备及修正

`recovery_adapter.py` 已离线实现受限 RX→TX 两次 CR 写入、固定 once Guard、现场身份核对、Run-only 后检与原始 terminal 独占复制/fsync/hash 校验后解除阻断；没有上传或在板端执行。

独立静态复核确认写入白名单与归档复制顺序，同时指出未来执行前还必须完成两项准备：将适配器及离线模型源码纳入启动时独立摘要锁定；为 root 0600 结果文件提供受控的 xilinx 可读导出路径，避免 SFTP 权限拒绝。当前适配器仍是未部署原型，不宣称已可直接执行。详情见 `adapter-review.json`。

本次失败控制器 `controller.py` 原样保存；修正版另存 `controller-next-proposed.py`，仅将 Guard 采集改为 `read_bytes().decode('utf-8')`，保留原始换行。新增回归在原版本下实际失败 1 项，修正版下通过；适配器边界、离线模型与采集器回归合计 **59 passed**。日志及 JUnit XML 在本目录。离线通过不代表板卡验证通过，修正版没有重新启动。

## 资源与分支

本次未创建 PYNQ/MMIO 或 CMA 对象，因此没有本次硬件句柄或缓冲区需要释放。只读远端命令已退出；本地控制器 PID 24300 已不存在。尚未完成板端全量 FD/maps 后检，不能据此断言其他进程绝无硬件占用。

未提交、未合并、未暂存。原开发目录与 worktree 的前后 git status 已保存，`git diff --check` 通过。旧 Retry 3 的 FAIL_STOPPED 结论保持不变。

## 后续执行边界

本轮因已触发主机检查停止，不自动重新执行。若再次授权，应先使用保留原始字节的采集器重新核验 Guard、boot、完整环境与实际寄存器，再按原方案单次执行。不得用本地补 LF 的结果替代板端原始字节检查，也不得跳过 Guard。

完整结果目录：`{OUT}`。
'''
(OUT/'verification-report.md').write_text(report,encoding='utf-8')
index={str(p.relative_to(OUT)):sha(p) for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='evidence-sha256.json' and '__pycache__' not in p.parts}
(OUT/'evidence-sha256.json').write_text(json.dumps(index,indent=2),encoding='utf-8')
print(json.dumps({'status':summary['status'],'tests':59,'evidence_files':len(index),'report':str(OUT/'verification-report.md')},indent=2))
