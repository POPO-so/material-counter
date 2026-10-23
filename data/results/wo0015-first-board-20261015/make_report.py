"""Summarize observed boot-gate rejection without promoting offline hashes to board evidence."""
import hashlib
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone, timedelta

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
MAIN=Path(r'C:\fpga\material-counter')
BUNDLE=OUT.parent/'wo0015-first-board-bundle-20261015'
def load(name):return json.loads((OUT/name).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
before=load('initial-snapshot.json')
after=load('postflight-snapshot.json')
exit_audit=load('postflight-result.json')
offline=load('local-offline-result.json')
manifest=json.loads((BUNDLE/'run-manifest.json').read_text(encoding='utf-8'))
assert before['boot_id']!=manifest['boot_id']
assert after['boot_id']==before['boot_id']
assert not after['remote_bundle_directory_exists']
assert after['ownership'].get('findings')==[] and after['ownership'].get('denied')==[]
assert not after['guard_files']
assert exit_audit['known_hosts_unchanged']
assert offline['status']=='OFFLINE_PREFLIGHT_PASS'
assert not list(OUT.glob('request-[0-9][0-9][0-9].json'))
for name,digest in manifest['executor_sha256'].items():
    assert sha(BUNDLE/name)==digest
    assert sha(ROOT/'scripts/wo0015'/name)==digest
assert sha(BUNDLE/'wo15.bit')==manifest['bit_sha256']
assert sha(BUNDLE/'wo15.hwh')==manifest['hwh_sha256']
heads={}
for label,path in [('main',MAIN),('worktree',ROOT)]:
    heads[label]=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
    status=subprocess.check_output(['git','-C',str(path),'status','--porcelain=v1'],text=True)
    (OUT/(label+'-git-after.txt')).write_text(status,encoding='utf-8')
    diff=subprocess.check_output(['git','-C',str(path),'diff','--name-status','HEAD'],text=True)
    (OUT/(label+'-tracked-diff.txt')).write_text(diff,encoding='utf-8')
    assert not diff
for label,snapshot in [('initial',before),('postflight',after)]:
    (OUT/(label+'-dmesg.txt')).write_text(snapshot['dmesg'],encoding='utf-8')
summary={
'status':'FAIL_STOPPED','stopped_at':'STEP_1_PRE_OVERLAY_BOOT_GATE',
'reason':'live boot_id differs from approved request and immutable manifest',
'approved_boot_id':manifest['boot_id'],'observed_boot_id':before['boot_id'],
'postflight_boot_id':after['boot_id'],
'cma_before_kib':before['meminfo'],'cma_after_kib':after['meminfo'],
'overlay_load_count':0,'overlay_load_elapsed_s':None,'dma_single_invocations':0,
'dma_three_invocations':0,'cma_allocations':0,'bundle_upload_count':0,
'mmio_reads':0,'mmio_writes':0,'frames_sent':0,'frames_received':0,
'board_bit_sha256':None,'board_hwh_sha256':None,
'local_bit_sha256':manifest['bit_sha256'],'local_hwh_sha256':manifest['hwh_sha256'],
'local_zip_sha256':sha(OUT.parent/'wo0015-first-board-bundle-20261015.zip'),
'postflight_ownership':after['ownership'],'postflight':exit_audit,'git_heads':heads,
'source_hashes_unchanged':True,'committed':False,'merged':False,
'stages':{'step1':'FAIL_STOPPED_AT_BOOT_GATE','step2':'NOT_RUN','step3':'NOT_RUN'}}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
now=datetime.now(timezone(timedelta(hours=8))).isoformat()
report=f'''# WO-0015 首次上板核验报告

生成时间：{now}（Asia/Shanghai）。

最终裁决：**FAIL_STOPPED**。停留在 **Step 1：Overlay 加载前的 boot 身份准入**。

批准文档 `docs/verification/wo-0015-first-board-request-20261015.md` 明确要求固定 boot；本次正式授权沿用该门控。严格指纹 SSH 认证成功，但实测 boot 不符，因此立即停止上板流程。未上传或解包上板包，未加载 Overlay，未导入板端 PYNQ，未进行 MMIO、CMA 分配或 DMA。Step 2 和 Step 3 未执行。本次没有重试、修改执行器、改写 manifest、删除 Guard、复位 DMA 或重启板卡。

## 板端环境与退出检查

| 项目 | 加载前准入 | 只读退出检查 |
|---|---|---|
| boot_id | `{before['boot_id']}` | `{after['boot_id']}` |
| 申请锁定 boot_id | `{manifest['boot_id']}` | 不匹配，拒绝推进 |
| CmaTotal | {before['meminfo']['CmaTotal']} KiB | {after['meminfo']['CmaTotal']} KiB |
| CmaFree | {before['meminfo']['CmaFree']} KiB | {after['meminfo']['CmaFree']} KiB |
| FPGA manager state / flags | {before['manager_state']} / {before['manager_flags']} | {after['manager_state']} / {after['manager_flags']} |
| WO-0015 Guard 文件 | 空 | 空 |

内核：`{' '.join(before['uname'])}`。

SSH 实测指纹：`{after['strict_host_fingerprint']}`；固定 known_hosts 摘要保持不变。批准解释器路径存在。Overlay 加载次数为 0，加载耗时不适用；`initial-readonly-transport.json` 的 {load('initial-readonly-transport.json')['elapsed_s']:.6f} 秒仅为只读准入命令耗时。

本次没有申请 CMA 缓冲，因此没有待释放的本次 TX/RX 缓冲。两次快照间 CmaFree 变化 {after['meminfo']['CmaFree']-before['meminfo']['CmaFree']} KiB；这是观测到的系统余量变化，未确定原因，不能据此认定本次发生分配或泄漏。退出时复用交付执行器中原样提取的只读 `audit_process_ownership` 函数扫描 `/proc/*/fd` 与 maps：findings=[]、denied=[]，没有发现 FPGA 设备占用进程。板端短时只读 Python 检查正常退出；常驻系统 Python/Jupyter 服务未被终止。`remote_bundle_directory_exists=false`。退出 Ping 返回码 {exit_audit['ping_exit_code']}。初始及退出原始 dmesg 已保存；它们仅提供系统现场，不构成 DMA 结果。本地两个凭据窗口对应的控制进程也已关闭，见 `local-console-cleanup.json`。

## 工件身份

| 工件 | 本地实测 SHA-256（离线准入通过） | 板端实测 SHA-256 |
|---|---|---|
| wo15.bit | `{manifest['bit_sha256']}` | 未测：boot 门控失败，未上传 |
| wo15.hwh | `{manifest['hwh_sha256']}` | 未测：boot 门控失败，未上传 |

ZIP 本地实测：`{summary['local_zip_sha256']}`。

Manifest 本地实测：`{sha(BUNDLE/'run-manifest.json')}`。包内与 worktree 两份执行器源码均与锁定 manifest 摘要相符。`local-offline-result.json` 为 OFFLINE_PREFLIGHT_PASS、board_accessed=false、pynq_imported=false，仅是 PC 离线证据。

## 三阶段结果

| 阶段 | 执行/裁决 | DMA 状态及寄存器 | Checker / Packer / Golden |
|---|---|---|---|
| Step 1 | 准入失败，Overlay 命令执行 0 次 | 未读；MMIO 次数 0 | 签名与版本未读；未建立 Overlay PASS 收据 |
| Step 2 | NOT_RUN，命令执行 0 次 | 未读、未启动 | 无统计；0 帧回收；Golden 未比较 |
| Step 3 | NOT_RUN，命令执行 0 次 | 未读、未启动 | 无统计；0 帧回收；Golden 未比较 |

没有“零差异”或 DMA 通过结论。本次拒绝属于硬件操作前的 boot 身份门控，不是 FPGA/DMA 功能失败。

## 文件与分支状态

完整结果目录：`{OUT}`。

主要证据：`initial-snapshot.json`、`postflight-snapshot.json`、`initial-dmesg.txt`、`postflight-dmesg.txt`、`events.jsonl`、`controller-error.txt`、`*-dispatch.json`、`*-stdout.txt`、`*-stderr.txt`、`summary.json`、`evidence-sha256.json`。

原目录 HEAD：`{heads['main']}`；worktree HEAD：`{heads['worktree']}`。两处 tracked diff 均为空；原有未跟踪交付及工作状态保留。本次只新增本结果目录，没有提交、合并或暂存。实际 before/after 状态见 `main-git-*.txt` 与 `worktree-git-*.txt`。

若要在当前 boot 上开展硬件验证，需要先复核新 boot 的板端基线并另行明确授权 boot 绑定；本轮不会自行修改锁定文档、manifest 或工件来继续。
'''
(OUT/'verification-report.md').write_text(report,encoding='utf-8')
index={str(p.relative_to(OUT)):sha(p) for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='evidence-sha256.json' and '__pycache__' not in p.parts}
(OUT/'evidence-sha256.json').write_text(json.dumps(index,indent=2),encoding='utf-8')
print(json.dumps({'status':summary['status'],'report':str(OUT/'verification-report.md'),'files_hashed':len(index),'cma_before':before['meminfo'],'cma_after':after['meminfo']},indent=2))
