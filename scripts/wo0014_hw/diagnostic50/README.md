# WO-0014 diagnostic50 独立阶段与安全监督

本轮仅 Windows 本地开发和 Mock。没有连接、上传、板端执行、Overlay、MMIO、DMA 或缓冲区分配。历史板测状态不作为本轮活体证明。原 `../pynq_image_once.py` 与保存基线均不修改。

## 认证方案

Python 不收集 SSH/sudo 密码，不通过参数、环境、文件、日志、askpass、stdin 或自动化键入传递凭据。工作站旧 `supervisor.py --execute --approve-once` 继续核验授权但拒绝网络执行；`SSHHealth` 和默认 `verify_remote_files` 也拒绝。旧哈希解析测试使用显式注入的 Mock runner。

未来单独授权后，在无录屏、转录或输入捕获的独立终端中使用原生 OpenSSH。先以可信带外方式核实板卡 host key，把已核实公钥准备到明确的 known_hosts 文件；不得单凭 ssh-keyscan 输出信任公钥。未知/变化指纹立即停止，不删除旧条目或改成 accept-new/no。

以下是未来人工步骤，不是本轮授权或已经执行的命令：

```powershell
ssh -tt -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o UserKnownHostsFile=C:/verified/known_hosts -o GlobalKnownHostsFile=C:/verified/known_hosts -o NumberOfPasswordPrompts=1 -o BatchMode=no -o PreferredAuthentications=keyboard-interactive,password -o PubkeyAuthentication=no -o ForwardAgent=no -o ClearAllForwardings=yes xilinx@192.168.2.99
```

`authentication.terminal_argv()` 生成同等 SSH/SFTP 参数但从不执行。SFTP 也须严格核验 host key、手动隐藏输入并检查退出码；失败立即停止，无自动认证回退。它不自行上传文件。

登录成功后，在同一安全终端直接使用交互式 `sudo` 启动板内 `session.py`，sudo 从终端读取隐藏凭据，Python 开始之前即完成鉴权。无需私钥、BatchMode 或 sudo -n；sudo 被拒绝则 Python 不会启动。不要 sudo -S、管道密码、echo 密码或修改 sshd/sudoers/用户密码。若终端不支持这些原生提示，则停止并保留人工限制。

## 阶段入口

`session.py` 默认仅输出 OFFLINE_ONLY。所有实际入口都需要 `--execute`；硬件入口另需 `--authorize-stage` 与 mode 完全相同，以及工作站审查的新 `--executor-manifest-sha`。授权参数记录操作者已取得的独立授权，不能自行产生授权。

| mode | 允许行为 | 固定安全语义 |
|---|---|---|
| health-check-only | OS/dmesg/boot/FPGA manager 文件、loopback ping、sshd 祖先检查 | 不启动硬件子进程、不创建 guard、不导入 PYNQ |
| deploy-verify-only | 本地部署包完整代码 SHA、50 MHz BIT/HWH/合法包校验 | 无 Overlay/MMIO/DMA，无子进程 |
| overlay-only | ENV → LOAD → HEALTH | 成功后退出；无 MMIO、allocate、DMA |
| mmio-read-only | ENV → HEALTH → MIN_READ | 不 LOAD；唯一 Checker magic 只读检查；不建立 DMA MMIO、allocate 或写寄存器 |
| dma-once | ENV → HEALTH → MIN_READ → INIT → SEND → VERIFY → RELEASE → POST | 不 LOAD；要求之前 Overlay 和 MMIO 成功凭据；LENGTH 只尝试一次 |
| full | 原完整状态机保留并做 Mock 回归 | 实际 CLI 拒绝：板内健康检查不能替代原独立工作站健康门 |

下一轮未来命令形式（按获批阶段替换 mode、SHA 和证据目录）：

```sh
sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -u /home/xilinx/NEW_REVIEWED_DEPLOY/session.py --mode overlay-only --execute --authorize-stage overlay-only --executor-manifest-sha REVIEWED_SHA --evidence-dir /home/xilinx/NEW_EVIDENCE_DIR
```

每个命令运行前，人工独立核实工作站到板卡 Ping、严格 host-key SSH、Linux、boot ID、kernel 增量及 FPGA 占用。板内 `LocalHealth` 的 ping 明确是 **loopback**，ssh 是现有认证 sshd 祖先，不宣称它们完成工作站网络检查。当前 Overlay 身份未知时，ENV 必须确认现有 PYNQ 文件身份、shutdown IP、RAM 和独占占用，无法确认即拒绝 LOAD。不要用猜测或旧记录补这个门。

## 固定 guard 与监督确认

板端固定 `/var/lib/wo0014-diagnostic50/`；跨部署目录和进程重启保持。子进程持有 flock。`trial-consumed`、`overlay-attempt`、`mmio-read-only-consumed`、`dma-once-consumed`、`send-attempt` 都排他建立并同步，空/部分标记也消耗机会。SEND 旧标记在 DMA 模式创建 MMIO/allocate 前即拒绝。

Overlay/MMIO 成功凭据绑定 mode、boot ID、BIT SHA；后续阶段重新核对当前加载身份。`terminal-fail` 永久拒绝后续阶段。板内控制器另写固定 `supervision-MODE-consumed/pass`：如果监督未确认 PASS，后续启动拒绝，即使子进程曾写出阶段凭据。子进程要求当前审查清单对应的 consumed 授权并拒绝此前未确认的其他阶段。没有清理/重置标记、重试、跨目录绕过或自动推进命令。

## 期限和故障处置

`deadline.py` 的独立 spawn 看门进程拥有绝对 monotonic 期限；阻塞操作运行于保留的 daemon 线程。监督范围包括启动 claim/log/Popen、派发前日志、write、flush、raw stream 落盘/管道等待、JSON 解析、阶段结果、健康门、最终 wait/终态写入与控制器成功凭据。请求期限从这些 I/O **之前**开始，heartbeat 不延时。并发派发会撤销 writer，不发第二条命令。

新有效运输只有 **同一板内** supervisor/child，命令强制携带同一时钟域 `deadline_monotonic_s`；子进程拒绝过期消息，每次硬件访问之前和返回后均检查期限。已进入内核的阻塞 pipe write 无法被 Python 撤回，稍后可能完成，但其过期内容不能产生后续硬件访问。不得将 Windows monotonic 值与板内 Linux 时钟直接比较；旧远程执行已禁用。

超时不 kill/reset/reload/reconnect，不释放不确定的 DMA 缓冲区。保留 child、管道、操作线程/看门引用、原始值、固定标记和失败终态。生产代码没有终止硬件 child 的操作，Mock 测试仅清理假子进程。异常后保持失败终端/session 存活，等待独立人工处置。

完整落盘证据依赖文件系统可写且响应；磁盘/管道阻塞时，不可能保证完整日志。终态先存内存，受阻 raw line 保留内存，落盘及 stderr 是有期限的 best effort；不把内存记录称为完整持久日志。SIGKILL/OOM/掉电、调度停顿、文件系统故障和不遵守协议的外部硬件进程仍属残余风险。软件期限不能中止正在挂起的 AXI 事务，也不能证明总线恢复。

## 离线验收与下一轮部署

运行 `preflight.py`，结果写入新的 `data/results/wo0014-diagnostic50-hardening-20261014/run-.../`；每次独立目录，保留旧日志和失败证据。回归覆盖诊断、新故障注入、legacy image、WO0014 vectors、WO0013 DDR，另做 AST 文件依赖闭包、基线/受保护哈希及 `git diff --check`。

新板内 bundle：`baseline_pynq_image_once.py`, `core.py`, `child.py`, `supervisor.py`, `deadline.py`, `session.py`, `authentication.py`, `executor-manifest.json`。BIT/HWH、rgb_packet.bin、pair-manifest.json 身份未变。实际增量文件与 SHA 以 HARDENING_REPORT.md 为准；不部署测试/Mock/preflight。现有 `/home/xilinx/wo0014_50mhz_deploy_20261014_205640` 是旧部署，不能声称已经包含本轮变化。

下一步仅可在新的独立授权下部署增量并先做 health-check-only / deploy-verify-only；硬件阶段仍需当次独立授权和活体门。full 自动板测尚未适配独立工作站健康通道，继续禁用。离线准备通过不等于认证、部署、当前 Overlay 或板级实验通过。

参考：[OpenSSH ssh](https://man.openbsd.org/ssh)、[ssh_config](https://man.openbsd.org/ssh_config)、[sudo 官方手册源码](https://github.com/sudo-project/sudo/blob/main/docs/sudo.man.in)。
