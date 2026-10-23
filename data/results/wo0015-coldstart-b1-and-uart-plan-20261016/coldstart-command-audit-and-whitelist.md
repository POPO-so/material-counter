# WO-0015 冷启动命令审计与白名单 v1

**NOT_APPROVED_DO_NOT_EXECUTE** · **STOPPED_PRECONDITION_UNVERIFIED**

本轮仅主机离线代码、内存 mock、合成日志与历史清单核验。无 SSH、串口枚举/打开、上电、B1/B2、MMIO/AFI、复位、Overlay 或 DMA。没有主机地址、串口名、连接命令、凭据、真实 OS 适配器或自动执行器。所有文件均为指定新目录中的增量；旧 v2 模板与历史证据保持不变。

## B1-coldstart 固定顺序

1. 未来远端包装器首先检查 `os.geteuid()`；root 非零退出，不打开采集文件，不使用 sudo。Python 启动器自身加载文件属于解释器启动行为，不能把本门控解释为整个 Python 进程从诞生起没有文件系统访问；门控约束的是采集代码及其打开操作。
2. 首项仅 `/proc/sys/kernel/random/boot_id`：`os.open(path, os.O_RDONLY | os.O_NOFOLLOW)`；要求 regular file；最多接受 `MAX_PROC_READ_BYTES = 65536` 字节，短读继续至 EOF，限额处仅增加一个溢出探测字节。错误即停，无重试或回退。
3. 仅移除单个末尾 LF 后，严格 `fullmatch` 小写 UUID：`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`。CRLF、双 LF、空格、非 ASCII、大小写不符均拒绝。
4. 调用方显式传入 `RETIRED_BOOT_IDS = ("822a9c29-37c0-438c-81c4-da998d57073b",)`，核心也锁死该集合，不能借传空集合绕过。退役 ID、预期模式不是 `coldstart_register_candidate`、显式预期 ID 不匹配，均在首项后短路。合法新 ID 仅登记候选。
5. 按顺序读取下面五项，同样 flags、上限、EOF 和异常规则。任何 EACCES/ELOOP/I/O/解码异常都使采集无效，描述符在 finally 关闭。

| 顺序 | 唯一路径 |
|---|---|
| 2 | `/proc/sys/kernel/osrelease` |
| 3 | `/proc/version` |
| 4 | `/proc/cmdline` |
| 5 | `/proc/uptime` |
| 6 | `/proc/self/mountinfo` |

6. 再读相同 boot_id 路径，以相同 flags/限额校验格式及首尾字节/UUID 一致。最多七次 open、六个不同文件。失败输出 `valid=false`；冻结进程包装器转换为非零退出。
7. 成功输出 `status=CANDIDATE_BOOT_ID_UNAPPROVED`、`state=STOPPED_CANDIDATE_BOOT_UNAPPROVED`、`hardware_baseline_approved=false`、`b2_executed=false`、`afi_authorized=false`。这不是新硬件授权。

`coldstart_b1.py` 只有注入接口；`frozen-b1-coldstart.NOT_APPROVED.txt` 嵌入同一核心，root 门后仍有未批准即退出门。本轮仅 AST 检查和哈希；不运行冻结文本。未来审批不能由测试通过或修改状态字段代替，必须另行审阅冻结执行文本。

`host_mountinfo.py` 仅主机静态解析：输出 `/boot` 是否存在、文件系统类型、mount/super options。唯一 `/boot` 且 mount options 含 ro、不含 rw 时才标为只读前提“被观察到”；畸形、重复叠加挂载、无 `/boot`、rw 均不放行。superblock rw 与单挂载 ro 分别报告；文本观察不能替代现场 `fstatvfs`。

## B2 独立审批和旧模板复用边界

用户与 Gemini 必须先审阅 B1 候选 ID 与 mountinfo；用户随后显式批准该新 ID 作为当前会话基线。仅此批准仍不执行 B2。需 mountinfo 表明 `/boot` 为只读，并在另行批准的 B2 内通过现场 `fstatvfs` 确证 `ST_RDONLY`，才允许读文件内容。任何重启使本会话基线失效，需重新登记/审阅；不自动重试。

旧封板 `proposals/remote_template_v2.py` 的 B2 核心门控已只读核对，**仍固定旧 boot_id，不能直接用于新会话**。本轮不修改、不调用、不热替换其常量。未来须在另一个增量提案中绑定新批准 ID，冻结源与哈希后单独申请 B2；本轮没有已绑定新 ID 的 B2 执行模板。

必须保留的 B2 合约：

- 非 root 门在任何打开之前；先读严格匹配新批准 boot_id，最后再读核对。禁 sudo。
- `dir_fd = os.open("/boot", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)`。
- `fd = os.open("BOOT.BIN", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)`。
- 内容读取前 `os.fstatvfs(fd).f_flag & os.ST_RDONLY` 必须真；`stat.S_ISREG(st_before.st_mode)`；`0 < st_size <= 32 * 1024 * 1024`；`dir_st.st_dev == file_st.st_dev`。
- 保留旧模板的 `os.stat("/", follow_symlinks=False)` 仅元数据，记录 `boot_dev_differs_from_root`，不读取根目录内容；该值不能单独证明独立分区。
- 固定 fd 有界读至 EOF，内容长度等于 st_size；读取后再次 `fstatvfs` 要求 `ST_RDONLY`，再次 fstat 的 `(st_dev, st_ino, st_size, st_mtime_ns)` 与前值一致。关闭两个 fd。
- 可写、符号链接、FIFO/设备/非 regular、零/超大文件、跨设备、短读、stat 变化、boot 变化、权限/读取/关闭异常全部终止，无重试、remount、替代路径、提权或 O_NOATIME 回退。

目标挂载点只读不等于整张 SD 卡无写入；SSH 登录、Linux 正常启动可能产生系统写入。本轮未连接，实际板端写入无本任务引发的操作。未来 UART 取证不能据软件封装单独宣称整卡零写入。

## SSH 固定约束（仅选项数据，无连接器）

`-T`；`ControlMaster=no`；`ControlPath=none`；`ClearAllForwardings=yes`；`ForwardAgent=no`；`ForwardX11=no`；`StrictHostKeyChecking=yes`；固定 `UserKnownHostsFile=C:/Users/POP/.ssh/wo0015-known_hosts`；固定解释器 `/usr/bin/python3 -I -S -B`。

不检查或创建 known_hosts，不自动信任新 host key，不使用 accept-new/ssh-keyscan；未知 key、解释器身份未核验或连接失败即停，不回退。未来 SSH 本身也必须单独获批。

## UART 白名单

仅未来经批准的 RX 字节读取；固定 115200、8N1、rtscts=False、xonxoff=False、dsrdtr=False。封装拦截 write/writelines/send_break 与 DTR/RTS/其他控制设置，固定抛 `PermissionError("UART TX is strictly prohibited in read-only capture mode")`。本轮只注入 mock，不导入串口库，不枚举设备，不提供串口 opener。

硬件层尚未验收。pySerial 官方文档明确指出打开时驱动可能自动改变 DTR/RTS 并产生 glitch：[pySerial API / open](https://pyserial.readthedocs.io/en/latest/pyserial_api.html#serial.Serial.open)。因此关闭流控及 Python 拦截只能证明封装 API 不主动发送；物理 Zero-TX 还需经审阅的 RX-only 接线、主机 TX 与所有控制/复位输出隔离、USB 供电/反向供电排查和驱动行为验收。板载 Micro-USB 方案未核验这些条件时不得标为物理零干扰，也不得开始连接。
