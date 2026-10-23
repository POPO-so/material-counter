"""Evidence-based closeout of the deployment permission fuse; no board retry."""
import hashlib,json,subprocess,zipfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
MAIN=Path(r'C:\fpga\material-counter')
def load(n):return json.loads((OUT/n).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
before=load('initial-snapshot.json')
after=load('postflight-snapshot.json')
rebind=load('authorization-rebind.json')
uploaded=load('request-001-stdout.txt')
exit_result=load('postflight-result.json')
assert before['boot_id']==after['boot_id']==rebind['authorized_boot_id']
assert not after['guard_files']
assert after['ownership'].get('findings')==[] and after['ownership'].get('denied')==[]
assert exit_result['known_hosts_unchanged']
assert 'PermissionError' in (OUT/'controller-error.txt').read_text()
assert not list(OUT.glob('request-003*'))
assert sha(Path(rebind['original_zip']))==rebind['original_zip_sha256']
for n,record in after['deployed_files'].items():
    assert record['sha256']==rebind['before'][n],(n,record)
for n in ('wo15.bit','wo15.hwh'):
    assert uploaded[n]==after['deployed_files'][n]['sha256']==rebind['after'][n]
assert after['deployed_files']['board_preflight.py']['uid']==0
old=OUT.parent/'wo0015-first-board-20261015'
assert all(sha(old/n)==h for n,h in json.loads((old/'evidence-sha256.json').read_text()).items())
heads={}
for label,folder in [('main',MAIN),('worktree',ROOT)]:
    heads[label]=subprocess.check_output(['git','-C',str(folder),'rev-parse','HEAD'],text=True).strip()
    (OUT/(label+'-git-after.txt')).write_text(subprocess.check_output(['git','-C',str(folder),'status','--porcelain=v1'],text=True),encoding='utf-8')
    delta=subprocess.check_output(['git','-C',str(folder),'diff','--name-status','HEAD'],text=True)
    (OUT/(label+'-tracked-diff.txt')).write_text(delta,encoding='utf-8')
    assert not delta
    subprocess.run(['git','-C',str(folder),'diff','--check'],check=True,capture_output=True)
for label,snapshot in [('initial',before),('postflight',after)]:
    (OUT/(label+'-dmesg.txt')).write_text(snapshot['dmesg'],encoding='utf-8')
summary={'status':'FAIL_STOPPED','stopped_at':'STEP_1_DEPLOYMENT_CONFIGURATION_BEFORE_OVERLAY',
'cause':'root-owned extracted files rejected unprivileged SFTP configuration upload',
'boot_id':before['boot_id'],'boot_gate_pass':True,
'board_bit_sha256':after['deployed_files']['wo15.bit']['sha256'],
'board_hwh_sha256':after['deployed_files']['wo15.hwh']['sha256'],
'authorized_manifest_sha256':rebind['manifest_sha256'],
'board_manifest_sha256':after['deployed_files']['run-manifest.json']['sha256'],
'authorized_config_applied':False,'cma_before_kib':before['meminfo'],
'cma_after_kib':after['meminfo'],'overlay_load_count':0,'overlay_load_elapsed_s':None,
'mmio_reads':0,'mmio_writes':0,'cma_allocations':0,'frames_sent':0,'frames_received':0,
'stage_invocations':{'overlay-only':0,'dma-single':0,'dma-three':0},
'stages':{'step1':'FAIL_STOPPED_DEPLOYMENT','step2':'NOT_RUN','step3':'NOT_RUN'},
'ownership':after['ownership'],'original_zip_unchanged':True,'previous_evidence_unchanged':True,
'functional_code_changed':False,'committed':False,'merged':False,'git_heads':heads}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
now=datetime.now(timezone(timedelta(hours=8))).isoformat()
report=f'''# WO-0015 首次上板 retry1 核验报告

时间：{now}（Asia/Shanghai）。最终裁决：**FAIL_STOPPED**。

停留位置：**Step 1 部署授权配置，Overlay 加载之前**。本次新 boot 身份准入已通过。包上传与解压成功、BIT/HWH 板端摘要匹配，但解压命令以 root 运行，导致配置文件归 root 所有；随后以 xilinx 身份通过 SFTP 覆盖 `board_preflight.py` 授权常量时收到 `PermissionError: [Errno 13] Permission denied`。这是代理部署工具的权限安排错误。没有重试上传、现场改权限或修补后继续，没有执行三条上板阶段命令。

本次没有加载 Overlay、导入板端 PYNQ、访问 MMIO、分配 CMA 或启动 DMA。Step 2 / Step 3 未执行。该结果属于部署失败，不能认定 FPGA 或 DMA 功能失败。

## 授权绑定与工件边界

队长在本聊天中明确重授权 boot 为 `{rebind['authorized_boot_id']}`。原 ZIP 保持不变；仅在本结果目录的 `authorized-deployment/` 中更新 `board_preflight.py` 的一个授权 boot 常量、manifest 的 boot 字段与执行器摘要，以及 SHA256SUMS 派生摘要。`board_once.py`、RTL、BIT/HWH、packet、Golden、打包逻辑、固定 Guard、功能判定标准均未修改。申请单已记录本次授权及具体命令。PC 离线与 Mock 通过，仅为离线证据。

本地新 manifest SHA-256：`{rebind['manifest_sha256']}`。

板端 manifest 仍为原包摘要 `{summary['board_manifest_sha256']}`，授权配置未生效。因此没有执行板端 offline、overlay-only、dma-single 或 dma-three。退出只读实测确认三个配置文件全部保持原包字节，首个失败文件未被截断或修改。

原 ZIP SHA-256：`{rebind['original_zip_sha256']}`。

| 工件 | 板端上传后与退出时实测 SHA-256 | 判定 |
|---|---|---|
| wo15.bit | `{summary['board_bit_sha256']}` | 与授权摘要一致 |
| wo15.hwh | `{summary['board_hwh_sha256']}` | 与授权摘要一致 |

板端目录保留为 `/home/xilinx/wo0015_first_board_retry1_20261015`，用于追溯部署失败；未删除、改名或覆盖旧实验。

## 环境与资源

| 项目 | 准入 | 只读退出检查 |
|---|---|---|
| boot_id | `{before['boot_id']}` | `{after['boot_id']}` |
| CmaTotal | {before['meminfo']['CmaTotal']} KiB | {after['meminfo']['CmaTotal']} KiB |
| CmaFree | {before['meminfo']['CmaFree']} KiB | {after['meminfo']['CmaFree']} KiB |
| FPGA manager state / flags | {before['manager_state']} / {before['manager_flags']} | {after['manager_state']} / {after['manager_flags']} |
| WO-0015 Guard | 空 | 空 |

Overlay 加载次数 0，加载耗时不适用。原包解压与哈希命令耗时 {load('request-001-transport.json')['elapsed_s']:.6f} 秒，这不是 Overlay 加载耗时。

退出 root 级 `/proc/*/fd` 与 maps 扫描 findings=[]、denied=[]，未发现 FPGA 设备占用；扫描复用原交付 `audit_process_ownership` 函数。短时板端只读 Python 进程正常退出。本次没有申请任何 TX/RX CMA 缓冲，因此没有本次待释放对象。CmaFree 两次快照差 {after['meminfo']['CmaFree']-before['meminfo']['CmaFree']} KiB，记录为系统余量变化，不能据此推断本次泄漏。原始进程表、dmesg 和 UID/权限证据均已保留。退出 Ping 返回码 {exit_result['ping_exit_code']}；SSH 严格指纹 `{after['strict_host_fingerprint']}`，known_hosts 未改变。完成后的本地凭据窗口已关闭，见 `local-console-cleanup.json`。

## 三阶段结果

| 阶段 | 本轮结果 | DMA / Checker / Packer / Golden |
|---|---|---|
| Step 1 | boot 通过、BIT/HWH hash 通过；配置部署失败，FAIL_STOPPED | 未加载，未读寄存器；无签名、初始 DMA 状态或计数实测 |
| Step 2 | NOT_RUN，阶段命令 0 次 | 未发送/回收帧；Golden 未比较 |
| Step 3 | NOT_RUN，阶段命令 0 次 | 未发送/回收帧；Golden 未比较 |

原 RTL 无单独版本寄存器，后续如执行只读身份核验，应记录 WO14 Checker / WO15 Packer 签名。本轮没有相应 MMIO 证据，没有“零差异”或 DMA PASS 结论。

## 结果文件与分支保持

完整结果目录：`{OUT}`。

主要文件：`summary.json`、`authorization-rebind.json`、`boot-constant-only.diff`、`local-boundary-qa.json`、`initial-snapshot.json`、`postflight-snapshot.json`、`initial-dmesg.txt`、`postflight-dmesg.txt`、`controller-error.txt`、`events.jsonl`、`request-001-stdout.txt`、`command-templates.json`、`evidence-sha256.json`。

原目录 HEAD：`{heads['main']}`；worktree HEAD：`{heads['worktree']}`。两处 tracked diff 为空，diff --check 通过；未提交、未合并、未暂存。本次新增结果目录并更新原本未跟踪的申请单，保留原有工作。旧 `wo0015-first-board-20261015` 中 32 个已索引证据文件逐一复核，摘要未变。

本轮停止于部署错误。后续若重新授权继续，应先使用权限一致的部署方式安装预先审查的三个配置文件，再核对完整 manifest/工件身份；不得直接运行当前板端旧 boot 绑定的执行器。此次不会自行重新执行任何步骤。
'''
(OUT/'verification-report.md').write_text(report,encoding='utf-8')
request=ROOT/'docs/verification/wo-0015-first-board-request-20261015.md'
text=request.read_text(encoding='utf-8')
marker='## retry1 执行结果'
if marker not in text:
    with request.open('a',encoding='utf-8') as f:f.write('\n'+marker+'\n\n新 boot 准入与 BIT/HWH 板端摘要通过；授权配置 SFTP 覆盖遇到 root 文件权限拒绝，已 FAIL_STOPPED，未执行 Overlay 或 DMA。完整证据见 `data/results/wo0015-first-board-retry1-20261015/verification-report.md`。\n')
index={str(p.relative_to(OUT)):sha(p) for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='evidence-sha256.json' and '__pycache__' not in p.parts}
(OUT/'evidence-sha256.json').write_text(json.dumps(index,indent=2),encoding='utf-8')
print(json.dumps({'status':'FAIL_STOPPED','report':str(OUT/'verification-report.md'),'cma_before_kib':before['meminfo']['CmaFree'],'cma_after_kib':after['meminfo']['CmaFree'],'files_hashed':len(index)},indent=2))
