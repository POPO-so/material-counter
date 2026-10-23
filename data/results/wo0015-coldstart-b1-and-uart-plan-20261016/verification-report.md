# WO-0015 冷启动增量离线核验报告

**STOPPED_PRECONDITION_UNVERIFIED** · **NOT_APPROVED_DO_NOT_EXECUTE**

## 本轮交付

新增目录：`C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-coldstart-b1-and-uart-plan-20261016`。全部变更在此目录；未改旧 v2、原开发源码或任何封板文件，未提交、未合并。

- `coldstart_b1.py`：只接受注入 POSIX API，非 root 最前置门；boot_id 首尾读取，严格 UUID、退役集合与显式模式排他门；五项 /proc 限额只读，无重试、回退或硬件链路。
- `host_mountinfo.py`：主机静态 /boot 挂载解析，区分缺失/rw/ro/畸形/叠加挂载。文本不代替现场 fstatvfs。
- `frozen-b1-coldstart.NOT_APPROVED.txt`：嵌入已测核心，AST 与源哈希校验，root 与未批准退出门。未执行该文本。
- `uart_readonly.py`：接收传输对象注入、固定 115200 8N1 与三项 False 流控合约，软件 TX/控制操作拦截；合成串口日志观察解析。无串口打开器。普通私有 backend 属性访问被拒绝，返回长度超过请求亦拒绝；同进程恶意反射或调用方原始注入句柄不在 Python 封装隔离能力内。
- 白名单、审批草案、UART 设计说明、测试日志、审阅记录、完整性快照、summary 和证据清单。

## 验证结果

本轮显式文件 pytest **77 passed**，失败/错误/跳过均为 0；分文件计数 `{"test_coldstart": 35, "test_frozen_contract": 4, "test_uart_readonly": 38}`。JUnit 原始记录在 `targeted-tests.xml`，命令与输出在 `targeted-tests.txt`。运行器为原开发目录既有 `.venv/Scripts/python.exe`，`-B`、`PYTHONDONTWRITEBYTECODE=1`、`-p no:cacheprovider`、`--confcutdir` 指定新目录；没有递归收集历史 rootfs。

B1 原始 RED 29 failed → GREEN 29 passed；独立审阅发现一次性 retired iterable 被耗尽和 euid 错误记录问题，新增回归 RED 2 failed/33 passed → 修复后 B1+冻结检查 GREEN 38 passed。首轮 UART 原始 RED/GREEN 保留；Manager 复核纠正流控配置与测试同错，补充配置回归 RED/GREEN；再次独立审阅补齐普通 backend 访问拒绝与超量返回拒绝，日志在 uart-boundary-red/green.txt。均以最终三文件联合运行作为验收依据，旧通过计数不叠加。

初次调用系统 Python 时未安装 pytest，尚未收集测试；随后使用项目既有虚拟环境，无软件安装或全局配置变更。最终有新鲜联合测试证据。

跨目录 before/after 全部相等：**364/364 封板清单项、320/320 历史索引项、389/389 历史非敏感项**，BIT/HWH 锁定哈希匹配；封板清单文件自身 SHA-256 为 `26b2a21dcb68dae34164e6f3dcb79d209637ced195ed7de9a56f38635910bf8d`。这些集合有重叠，不能相加为独立文件总数。使用显式历史清单路径逐项哈希，没有遍历 rootfs/restricted-raw/REVISION。364 项中已有离线提取文件按清单只读哈希，没有重读 8.33GB 镜像或解析 FSBL。

两个 checkout HEAD、tracked diff、staged diff 哈希前后不变（见 integrity-before/after.json）。新增目录为未提交离线交付；没有 stage、commit、merge 或清理。

## 证据边界与后续门控

全局状态未改变。未来成功 B1 仅输出 `CANDIDATE_BOOT_ID_UNAPPROVED`，停在 `STOPPED_CANDIDATE_BOOT_UNAPPROVED`。本轮无真实候选 ID；测试 UUID 不能用于授权。用户与 Gemini 审阅候选及 mountinfo 后，仍需用户显式批准会话基线；B2 再单独申请并保留 POSIX 全部门控。旧 B2 固定退役 ID，不可直接用于新会话，本轮未新建可执行 B2。

无板卡连接、SSH、串口扫描/打开、上电、B1/B2、MMIO/AFI、复位、Overlay、DMA；没有硬件通过结论。软件 TX 拦截只由内存 mock 验证。物理 Zero-TX 尚未验收；[pySerial 官方 open 文档](https://pyserial.readthedocs.io/en/latest/pyserial_api.html#serial.Serial.open)说明驱动打开时可能改变 RTS/DTR。必须先审阅 RX-only 与控制/供电隔离，不能从软件 Guard 推导物理零脉冲或整张 SD 零写入。Linux 正常启动和 SSH 本身可能产生系统写入。

UART 的 `OBSERVED_IN_UART_LOG` 只表示提供字节中出现相应文字；root=UUID 是启动参数观察，不是实际挂载身份证明；fpga_manager/base.bit 文字也不等于成功加载或硬件正确。缺失表示 NOT_OBSERVED，不能反证启动阶段不存在。

`evidence-sha256.json` 覆盖新目录所有交付文件，排除自身，避免自哈希循环。该清单生成后进行独立逐项复算。
