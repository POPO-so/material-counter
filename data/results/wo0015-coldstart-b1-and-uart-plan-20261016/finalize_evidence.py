"""Host-only reporting/manifest generation. Run after explicit tests and integrity."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

OUT=Path(__file__).resolve().parent

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def finalize():
    suites=ET.parse(OUT/'targeted-tests.xml').getroot()
    cases=suites.findall('.//testcase')
    assert cases and not suites.findall('.//failure') and not suites.findall('.//error')
    assert not suites.findall('.//skipped')
    integrity=json.loads((OUT/'integrity-after.json').read_bytes())
    assert integrity==json.loads((OUT/'integrity-before.json').read_bytes())
    counts={name:sum(c.attrib.get('classname','').endswith(name) for c in cases)
            for name in ('test_coldstart','test_frozen_contract','test_uart_readonly')}
    summary={
        'status':'STOPPED_PRECONDITION_UNVERIFIED',
        'execution_status':'NOT_APPROVED_DO_NOT_EXECUTE',
        'scope':'PURE_OFFLINE_HOST_ONLY',
        'candidate_success_state':'STOPPED_CANDIDATE_BOOT_UNAPPROVED',
        'candidate_success_status':'CANDIDATE_BOOT_ID_UNAPPROVED',
        'approved_boot_id':None, 'real_candidate_boot_id':None,
        'retired_boot_ids':['822a9c29-37c0-438c-81c4-da998d57073b'],
        'board_connections':0,'ssh_attempts':0,'serial_enumerations':0,'serial_opens':0,
        'b1_b2_executions':0,'mmio_afi_reads_or_writes':0,'reset_operations':0,
        'overlay_loads':0,'dma_runs':0,'power_operations':0,'commits_or_merges':0,
        'board_accessed':False,'afi_authorized':False,'b2_authorized':False,
        'tests':{'passed':len(cases),'by_file':counts,'historical_counts_added':False,
                 'scope':'three explicitly named NEW files; in-memory fakes/synthetic bytes/static AST'},
        'integrity':integrity,
        'sealed_v2_changed':False,
        'b2_new_baseline_template':'NOT_CREATED; requires separately approved new ID and separate freeze',
        'uart_api_zero_tx':'MOCK_VERIFIED',
        'mock_transport_tx_bytes':0,
        'uart_physical_zero_tx':'UNVERIFIED_REQUIRES_PHYSICAL_ISOLATION_REVIEW',
        'whole_sd_zero_write':'NOT_ESTABLISHED_BY_UART_OR_SSH',
        'restricted_raw':'NOT_TRAVERSED_NOT_READ_NOT_COPIED', 'revision':'NOT_READ_NOT_COPIED',
        'large_image_read':False,'fsbl_reparsed':False,
        'manifest_policy':'all new regular files excluding evidence-sha256.json itself; no self hash',
    }
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    report=f'''# WO-0015 冷启动增量离线核验报告

**STOPPED_PRECONDITION_UNVERIFIED** · **NOT_APPROVED_DO_NOT_EXECUTE**

## 本轮交付

新增目录：`{OUT.as_posix()}`。全部变更在此目录；未改旧 v2、原开发源码或任何封板文件，未提交、未合并。

- `coldstart_b1.py`：只接受注入 POSIX API，非 root 最前置门；boot_id 首尾读取，严格 UUID、退役集合与显式模式排他门；五项 /proc 限额只读，无重试、回退或硬件链路。
- `host_mountinfo.py`：主机静态 /boot 挂载解析，区分缺失/rw/ro/畸形/叠加挂载。文本不代替现场 fstatvfs。
- `frozen-b1-coldstart.NOT_APPROVED.txt`：嵌入已测核心，AST 与源哈希校验，root 与未批准退出门。未执行该文本。
- `uart_readonly.py`：接收传输对象注入、固定 115200 8N1 与三项 False 流控合约，软件 TX/控制操作拦截；合成串口日志观察解析。无串口打开器。普通私有 backend 属性访问被拒绝，返回长度超过请求亦拒绝；同进程恶意反射或调用方原始注入句柄不在 Python 封装隔离能力内。
- 白名单、审批草案、UART 设计说明、测试日志、审阅记录、完整性快照、summary 和证据清单。

## 验证结果

本轮显式文件 pytest **{len(cases)} passed**，失败/错误/跳过均为 0；分文件计数 `{json.dumps(counts)}`。JUnit 原始记录在 `targeted-tests.xml`，命令与输出在 `targeted-tests.txt`。运行器为原开发目录既有 `.venv/Scripts/python.exe`，`-B`、`PYTHONDONTWRITEBYTECODE=1`、`-p no:cacheprovider`、`--confcutdir` 指定新目录；没有递归收集历史 rootfs。

B1 原始 RED 29 failed → GREEN 29 passed；独立审阅发现一次性 retired iterable 被耗尽和 euid 错误记录问题，新增回归 RED 2 failed/33 passed → 修复后 B1+冻结检查 GREEN 38 passed。首轮 UART 原始 RED/GREEN 保留；Manager 复核纠正流控配置与测试同错，补充配置回归 RED/GREEN；再次独立审阅补齐普通 backend 访问拒绝与超量返回拒绝，日志在 uart-boundary-red/green.txt。均以最终三文件联合运行作为验收依据，旧通过计数不叠加。

初次调用系统 Python 时未安装 pytest，尚未收集测试；随后使用项目既有虚拟环境，无软件安装或全局配置变更。最终有新鲜联合测试证据。

跨目录 before/after 全部相等：**364/364 封板清单项、320/320 历史索引项、389/389 历史非敏感项**，BIT/HWH 锁定哈希匹配；封板清单文件自身 SHA-256 为 `{integrity['sealed_manifest_sha256']}`。这些集合有重叠，不能相加为独立文件总数。使用显式历史清单路径逐项哈希，没有遍历 rootfs/restricted-raw/REVISION。364 项中已有离线提取文件按清单只读哈希，没有重读 8.33GB 镜像或解析 FSBL。

两个 checkout HEAD、tracked diff、staged diff 哈希前后不变（见 integrity-before/after.json）。新增目录为未提交离线交付；没有 stage、commit、merge 或清理。

## 证据边界与后续门控

全局状态未改变。未来成功 B1 仅输出 `CANDIDATE_BOOT_ID_UNAPPROVED`，停在 `STOPPED_CANDIDATE_BOOT_UNAPPROVED`。本轮无真实候选 ID；测试 UUID 不能用于授权。用户与 Gemini 审阅候选及 mountinfo 后，仍需用户显式批准会话基线；B2 再单独申请并保留 POSIX 全部门控。旧 B2 固定退役 ID，不可直接用于新会话，本轮未新建可执行 B2。

无板卡连接、SSH、串口扫描/打开、上电、B1/B2、MMIO/AFI、复位、Overlay、DMA；没有硬件通过结论。软件 TX 拦截只由内存 mock 验证。物理 Zero-TX 尚未验收；[pySerial 官方 open 文档](https://pyserial.readthedocs.io/en/latest/pyserial_api.html#serial.Serial.open)说明驱动打开时可能改变 RTS/DTR。必须先审阅 RX-only 与控制/供电隔离，不能从软件 Guard 推导物理零脉冲或整张 SD 零写入。Linux 正常启动和 SSH 本身可能产生系统写入。

UART 的 `OBSERVED_IN_UART_LOG` 只表示提供字节中出现相应文字；root=UUID 是启动参数观察，不是实际挂载身份证明；fpga_manager/base.bit 文字也不等于成功加载或硬件正确。缺失表示 NOT_OBSERVED，不能反证启动阶段不存在。

`evidence-sha256.json` 覆盖新目录所有交付文件，排除自身，避免自哈希循环。该清单生成后进行独立逐项复算。
'''
    (OUT/'verification-report.md').write_text(report,encoding='utf-8')
    brief=f'''# WO-0015 coldstart v1 审阅摘要

**NOT_APPROVED_DO_NOT_EXECUTE** · **STOPPED_PRECONDITION_UNVERIFIED**

本轮仅增量离线预案、注入核心与定向测试。联合 **{len(cases)} passed**；364 封板、320 历史索引、389 非敏感历史与 BIT/HWH 哈希均未变；两 checkout 的 HEAD/tracked/staged 差异基线未变。无提交、合并或板端接触。

请审阅 `coldstart-command-audit-and-whitelist.md`、`coldstart-approval-request.md`、`uart-design.md` 和冻结 B1 文本。新 B1 合法新 UUID 仅标候选，退役 ID 不允许；首尾一致且只读 /proc 成功仍停在 `STOPPED_CANDIDATE_BOOT_UNAPPROVED`。没有自动 B2 或硬件授权。

旧 B2 安全门可作为未来提案的合约参考，但固定旧 boot_id，不能直接执行；新基线须用户在用户/Gemini 审阅后显式批准，再单独申请绑定新 ID 的 B2。mountinfo 与现场 ST_RDONLY 双门，任何失败即停。

Zero-TX 分两层：软件接口拦截已 mock 验证；USB-UART 驱动/硬件开口瞬态未验证。固定流控 False 不能证明物理零脉冲，需物理 RX-only/控制线/供电隔离方案及独立批准。不得承诺整卡零写入。

真实板端 boot_id、挂载状态、启动日志与身份对应关系仍未知。下一步仍是审阅本离线提案；本文件没有发起任何执行。
'''
    (OUT/'review-brief-coldstart-v1.md').write_text(brief,encoding='utf-8')
    index={p.relative_to(OUT).as_posix():digest(p) for p in sorted(OUT.rglob('*'))
           if p.is_file() and p.name!='evidence-sha256.json'}
    (OUT/'evidence-sha256.json').write_text(json.dumps(index,indent=2)+'\n',encoding='utf-8')
    for n,h in index.items():
        assert digest(OUT/n)==h,n
    print(json.dumps({'tests_passed':len(cases),'by_file':counts,'new_manifest_files':len(index),'integrity':'PASS'}))

if __name__=='__main__':
    finalize()
