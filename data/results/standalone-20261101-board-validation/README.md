# PS/PL 候选板端验证

状态：G2 ABI 2 候选 v3 已加载；修正 beat 口径后的 300 帧板端 smoke 正常 `STOPPED`，输入/输出传输计数一致，DMA/VDMA 及相机、缓冲资源正常释放。v2 曾因每帧 beat 期望多 8 而进入 `INTEGRATED_FAIL_HOLD`，后经用户授权的一次 Linux 板端重启释放。验证范围不构成完整 HDMI IO 时序收敛或产品验收。

## 候选身份

- 硬件构建源码：`3af7b29ecd7cacb804447d26a16a8f7999ef61ed`，分支 `feat/优化PS与PL分工`；v3 软件包的校验修正来源 commit object 见下方 v3 记录。
- Routed 构建：`standalone-20261101-163250-g2-integration`；Vivado 退出码 0，WNS +0.273 ns、TNS 0、WHS +0.022 ns、THS 0。
- BIT SHA-256：`4fcca329d763b42d8dc667ff3a9d86c9c82a414395c505247d369296184ab847`。
- HWH SHA-256：`610a79f773c1bf605705424a5f864dfccc26e29bfce27078a6d5082eab8ff253`。
- 本次 manifest SHA-256：`ab9980230d08c01206c982e3776e7f43c9772df2a5a2eda87a0e088558eb9704`。
- 离线候选包：`C:\Users\14173\Documents\material-counter\.runtime\candidate-packages\ps-pl-offload-20261101`，deployment index SHA-256：`9880eb69aa1b44db6163aa6824e0e39639352f81e0f01dc03f0b3708d18823b8`。
- 组包器报告 `OFFLINE_CANDIDATE_BUILT`；索引含 62 个文件，46 个源码模块与指定 Git commit 对应，文件完整性验证 PASS。此项没有访问硬件。

## 重启前只读预检

目标 `192.168.2.99` 使用本机已记录的 SSH host key；SSH 登录后 hostname 为 `pynq`，系统为 PYNQ Linux / Ubuntu 22.04 / ARMv7。板端时钟显示 2025-05-09，与项目日期不符，不用作本次证据时间。

- Boot ID：`ec622fd1-01ad-4118-99f5-66e8b12ee666`；uptime 约 58,806 秒。
- `material-counter-integrated.service` 已 disabled，但状态为 `deactivating / stop-sigterm`，MainPID `7123`，`TimeoutStopSec=infinity`、`SendSIGKILL=no`。
- 服务进程进入 `COUNT_STOPPED_DISPLAY_FAIL_HOLD`，journal 报告 `DISPLAY_RESOURCES_RETAINED`；CMA 总量 131,072 KiB，采样空闲量 26,724–27,272 KiB。
- `/dev/v4l/by-id` 和 `/dev/video*` 当时均不存在。
- 预检未访问 MMIO、DMA 寄存器或 Overlay。旧 handoff 曾记录对 `0x43C20000` 的 post-run MMIO 读回触发 Linux external abort，根因未明；本次不得把该读回当作首个上板步骤。

## 手动重启后的状态与候选暂存（2026-11-01）

- 新 Boot ID：`d7beae0b-6281-4b8c-9f94-4cc6343b51a0`，与重启前不同。
- `material-counter-integrated.service` 为 `disabled / inactive / dead`，MainPID 为 0；旧 PID 已不在。
- CMA 总量 131,072 KiB，空闲 81,396 KiB。`/dev/video0`、`/dev/video1` 和 by-id 节点已出现；当前相机 by-id 为 `usb-DECXIN_CAMERA_DECXIN_CAMERA_01.00.00-video-index0`，与候选 `boot-config.json` 固定的 HSK 路径不同。
- FPGA manager 报告 `operating`，但没有给出可核验的当前 BIT SHA-256；这不能作为已加载候选 BIT 的身份依据。
- 候选包已复制至独立只读路径 `/home/xilinx/standalone-g2-smoke-20261101-163250-3af7b29/package/`，没有覆盖 `/opt/material-counter/current`，没有修改 systemd 配置。
- 传输 ZIP SHA-256：`93f73f6c312013ff550e715b96ae5925c9ca061a2a28aa5cbcc05295ba8ea289`；板端 `deployment-index.json` SHA-256 与预期一致：`9880eb69aa1b44db6163aa6824e0e39639352f81e0f01dc03f0b3708d18823b8`。
- 板端逐文件校验 62/62 PASS，包完整性工具报告 63 files checked、`hardware_actions=0`、PASS；package 目录权限为 550。校验明细见 [board-package-integrity.txt](board-package-integrity.txt)。
- 只读运行环境准入检查失败：候选期望 kernel release `6.6.10-xilinx-v2024.1`，板端实际为 `6.6.10-xilinx-v2024.1-g3c0eca68c652`。因此当前候选不能通过入口自身的环境门禁。
- 未实例化 `Overlay`、未加载 BIT、未访问 MMIO/DMA/VDMA。原因是旧记录中 `0x43C20000` 读回曾触发 Linux external abort；此外尚无本轮可确认的 DMA idle、VDMA halted、无错误和资源 owner 释放证据。`boot_integrated.py` 会执行该读回并继续启动 7200 秒相机流程，不适合作为本次短测入口。

## 结论与下一步

## 修正候选 v2 的加载与短时运行（2026-11-01）

- 为匹配板端实测，v2 将 `board-runtime.json` kernel release 更新为 `6.6.10-xilinx-v2024.1-g3c0eca68c652`，`boot-config.json` 相机路径更新为 `/dev/v4l/by-id/usb-DECXIN_CAMERA_DECXIN_CAMERA_01.00.00-video-index0`。新 deployment index SHA-256：`4cda43c59e0eb5d8dc015562f4813bc58d3c11483cd5eb84a31d02af0ed31509`。
- v2 板端包路径：`/home/xilinx/standalone-g2-smoke-20261101-163250-3af7b29-v2/package/`。62 个索引文件、包完整性、运行环境准入及 camera by-id 存在性均 PASS；package 目录只读。旧 v1 暂存目录保留。
- 一次性加载报告在板端 `...-v2/overlay-load-smoke.json`，本地副本为 [overlay-load-smoke.json](overlay-load-smoke.json)。PYNQ 全局状态登记的 BIT SHA-256 为 `4fcca329d763b42d8dc667ff3a9d86c9c82a414395c505247d369296184ab847`，与候选一致；FCLK 读回 `[50.0, 100.0, 142.857143]` MHz。systemd 未修改。
- 15 秒 runner 命令请求 300 帧；用户确认 HDMI 已显示相机/图形画面。运行约 12.82 秒后触发 `ValueError('input_beats total mismatch')` 并进入 `INTEGRATED_FAIL_HOLD`。报告观察到 `input_beats=19,200,000`，即 64,000/frame；当前软件校验按 `INPUT_BYTES=256,032` 计算 64,008/frame，300 帧预期 19,202,400，差 2,400 beat（8/frame）。输出 beat 与收到字节总数相符。详细板端报告位于 `...-v2/runtime/board-smoke-15s.json`。
- 当前 `integrated_run` 进程仍存活并保留硬件 owner；systemd service 本身仍 inactive。已停止继续启动其他板端进程；按 DEPLOYMENT.md 的 FAIL_HOLD 规则保留现场。

因此，候选 v2 的 BIT 身份、时钟和 HDMI 显示路径已核实，但其 15 秒 smoke 因输入 beat 校验口径错误进入 FAIL_HOLD。源码已将 RAW1 beat 口径改为 64,000/frame，并增加 300 帧累计用例，见 [pressure.py](../../../scripts/standalone/pressure.py) 与 [test_pressure.py](../../../scripts/standalone/test_pressure.py)。pytest 未运行：本机 Python 环境缺少 pytest。

## 修正候选 v3 复位部署与短测（2026-11-01）

- 用户明确授权对 v2 的 FAIL_HOLD 做一次硬件复位。复位后新 Boot ID 为 `6ca5a640-c3f0-4fd8-b3cf-883066534609`；旧 runner 已退出，集成 service 保持 inactive。没有修改 `/opt/material-counter/current` 或 systemd。
- v3 包使用修正后的 `pressure.py`，包内源码与工作区修正文件 SHA-256 一致。板端路径为 `/home/xilinx/standalone-g2-smoke-20261101-163250-3af7b29-v3/package/`，索引 SHA-256 `91251c59fef9e9436eaaed9505bdcb932cc7881330cfb7e40a460a49f5bf34cf`；板端 63 项完整性检查通过。可重新部署的归档为 [candidate-v3.zip](candidate-v3/candidate-v3.zip)，包来源 commit object 为 `542f1fd57302771e171811b6048a7081df608f6d`。
- 一次性加载报告为 [overlay-load-smoke.json](candidate-v3/overlay-load-smoke.json)：状态 `BIT_HASH_AND_CLOCKS_PASS`，BIT SHA-256 `4fcca329d763b42d8dc667ff3a9d86c9c82a414395c505247d369296184ab847`，时钟 `[50.0, 100.0, 142.857143] MHz`。
- [board-smoke-15s.json](candidate-v3/board-smoke-15s.json) 记录 300 帧运行以 `STOPPED` 结束，进程退出码 0，运行 11.57 秒。传输一致性有效：输入 beat 预期/实测均为 `19,200,000`，输出 beat 均为 `165,600`，接收字节为 `662,400`。owner 检查无发现；DMA idle、VDMA halted、无 pipeline error，camera、buffers、RLE buffers 和 report writer 均释放；没有再次进入 FAIL_HOLD。
- 本次只验证短时功能链路。`material_accuracy=NOT_MEASURED`，`total_count=0`；末次有限 CPU 样本的 runner process_total 为 91.48%（双核整机 0–100% 口径），不能据此评估长期 CPU 降幅。短测结束后 runner 已停止，systemd 仍 inactive；未验证自启动、持续运行或完整 HDMI IO timing closure。
