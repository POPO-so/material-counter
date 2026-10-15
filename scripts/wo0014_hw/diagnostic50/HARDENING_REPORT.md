# WO-0014 diagnostic50 离线交付与验收

结论：`OFFLINE_STAGE_READY`。独立阶段的本地代码、Mock 和依赖闭包检查通过；这不是当前板卡认证、部署或硬件 PASS。完整自动入口因原独立外部健康通道尚未安全适配而保持禁用。已停止离线工作，下一轮需独立授权。

## 修改文件

所有代码和说明均在 `scripts/wo0014_hw/diagnostic50/`。该目录当前整体为 Git untracked；下面是本轮相对起始内容的修改清单，不将历史未跟踪文件误称为本轮新增。

修改：`core.py`, `child.py`, `supervisor.py`, `preflight.py`, `README.md`, `test_deployment.py`, `executor-manifest.json`。

新增：`authentication.py`, `deadline.py`, `session.py`, `test_authentication.py`, `test_deadline.py`, `test_dispatch_bounds.py`, `test_session.py`, `test_stages.py`, `HARDENING_PLAN.md`, `HARDENING_REPORT.md`。

原 `pynq_image_once.py`、保存基线、生产 RTL、受保护文档、G1/Route/ADR 哈希核验未变。没有 commit/push，没有连接、上传、板端执行或真实硬件访问。历史证据目录未删除；最终验收使用独立 run 目录。

## 认证适配

独立安全终端原生 SSH/SFTP（严格已核实 known_hosts、一次提示、不依赖私钥）和交互式 sudo。密码只在原生隐藏提示中输入；执行器不接收、保存、传输或记录密码。未知/变化指纹、登录或 sudo 失败立即停止，不回退。没有修改板端 SSH、sudoers 或密码。人工命令和可信带外 host key 核验步骤见 README。

Mock 认证测试验证命令选项和失败后不派发的契约，没有声称已验证 Windows/板卡的真实交互提示。自动 BatchMode/sudo-n 运行入口拒绝；不能安全认证时只保留人工步骤。

## 阶段和授权

| 入口 | 安全语义 |
|---|---|
| health-check-only | OS/boot/dmesg/FPGA 状态文件及 loopback/已有 sshd 会话；没有 PYNQ/child/guard |
| deploy-verify-only | 全部 bundle、BIT/HWH/包 SHA；没有硬件子进程 |
| overlay-only | ENV/LOAD/HEALTH 后退出；无 MMIO 或 DMA |
| mmio-read-only | 已核实同 boot/BIT 的 Overlay 凭据；只读 Checker magic，不 LOAD/allocate/写寄存器 |
| dma-once | Overlay + MMIO 成功凭据、固定 SEND guard；单次发送，不 LOAD |
| full | 原状态机 Mock 正常路径保留；真实 CLI 继续拒绝，未削弱原独立工作站健康门 |

硬件入口同时要求 execute、准确的 authorize-stage 和审查清单 SHA；不因 PASS 自动推进。固定 guard、不可重用 consumed 标记、terminal-fail、完整监督 consumed/pass 记录和 child flock 阻止重启/改上传目录重试。空或部分 PASS 文件不算成功。没有删除/重置 guard 的 API。

板内健康检查明确不能证明工作站到板卡的 Ping/SSH 连通性。独立阶段未来也必须先由操作者完成人工外部只读检查和该次授权；未知当前 Overlay 身份须由 ENV 确认，不能从历史记录推断。

## 期限覆盖和残余风险

独立监督进程从日志、claim 与派发之前计时；覆盖 Popen、write、flush、等待、raw 日志 I/O、协议解析、结果与终态收集、健康门。阻塞线程和 child 引用保留，不 kill/reset/reload/free/retry。并发派发撤销延迟 writer。board-local 同时钟域绝对期限在接收端及每次硬件访问前/返回后检查。

已进入内核的 pipe write 可在超时后完成，但已过期命令不能继续硬件访问。磁盘阻塞时完整持久日志无法保证；内存终态、受阻 raw line、固定标记及 best-effort 落盘/stderr 明确保留，不把它们宣称为完整日志。软件不能解除 AXI 挂起；SIGKILL/OOM/掉电、存储故障、调度停顿和外部不遵守协议的硬件访问仍属残余风险。

## 新鲜验收结果

| 套件 | 结果 |
|---|---|
| diagnostic | 110 passed in 27.69s |
| legacy-image-once | 11 passed, 3 subtests passed in 1.38s |
| wo0014-vectors | 7 passed in 3.76s |
| wo0013-ddr | 10 passed, 16 subtests passed in 0.03s |

所有四个 pytest 命令退出码 0。新诊断故障覆盖认证失败/host key/sudo 拒绝、权限越界、哈希不一致、日志/write/flush/健康等待阻塞、进程退出/终止、Overlay/MMIO/DMA 异常、永久读阻塞、consumed guard、并发派发冲突和完整 Mock 合法路径。真实 child flock 的 Linux 行为未在 Windows 上进行板端验证。

AST 依赖闭包 PASS：七个本地运行文件完整纳入 bundle；外部仅标准库和原有 PYNQ/NumPy（未来板端仍需实测）。原始脚本、生产/受保护文件 SHA PASS。`git diff --check` PASS；另对未跟踪文件做 no-index whitespace 检查。独立 reviewer 的部分闭包发现已修复，其完整审查因资源限制未完成；Manager 已复核调用链、修改和新鲜回归，不宣称额外独立签字。

最终证据：`data/results/wo0014-diagnostic50-hardening-20261014/run-20261014-214628-281629`（precheck.json、各套件 stdout/stderr、dependency-closure.json、source-sha256.json、fixtures 中阶段/raw/guard/终态）。早期 RED/中间失败与修复在本轮工具输出和保留的早期目录中；不把它们当最终 PASS。

## 执行器 SHA-256 和部署增量

清单 SHA-256（授权参数）: `cb9d1fe1368f8a92627fc22789e96f87fbe00b3ba697c3bcafb6c5e48583ef46`。

| 文件 | SHA-256 |
|---|---|
| baseline_pynq_image_once.py | `b1d2a56fd3d87c5e3331ae2b45a14e867fbccebe00df8a5d44f2ce93940375e2` |
| core.py | `d3c5879805a8c7ed13192815a31ff4d86a6d151f7a27394ba1ddf7cca428baeb` |
| child.py | `9e8af146d2b648f96de8a191aa056683cfab833ff12ac993b21afc3f66f53d68` |
| supervisor.py | `115379587dcbc282862717d33b92f4a4e210ed1bd109871b974d0dc9a03b8f0a` |
| deadline.py | `1d112fece7c971a129361fe70c135eb81ea43c4dd4bdb4f7b104c70aae27e669` |
| session.py | `ffaca7367a95537135bedabb14035491641613c2223df4636c7aaedfd737a851` |
| authentication.py | `b63e1c41220ca078a0536f1665ea65af0439033aefe2157417a7d669035874a8` |
| executor-manifest.json | `cb9d1fe1368f8a92627fc22789e96f87fbe00b3ba697c3bcafb6c5e48583ef46` |

下一次仅在授权后部署这些增量：`core.py`, `child.py`, `supervisor.py`, `deadline.py`, `session.py`, `authentication.py`, `executor-manifest.json`。保存基线与四个 BIT/HWH/packet/pair-manifest 文件身份不变；新隔离目录部署时仍须带齐完整 bundle 与这些四个文件。测试、Mock、preflight 和说明不在板端执行。旧 `/home/xilinx/wo0014_50mhz_deploy_20261014_205640` 未改动，不能与新清单混用。

## 下一次板级实验条件

已达到独立阶段的离线软件/文件准备条件；下一步建议在独立授权下部署增量，然后只执行 health-check-only 和 deploy-verify-only。Overlay/MMIO/DMA 分别等待新授权，活体检查不能省略。完整自动板测未达到运行条件，仍拒绝，原因是安全交互认证没有提供原工作站独立健康通道的自动替代。新 SHA 和 Mock PASS 不构成板测授权或板测结论。
