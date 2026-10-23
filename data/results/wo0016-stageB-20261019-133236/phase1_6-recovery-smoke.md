# WO-0016 Stage B Phase 1.6：CMA 复核与静态冒烟重试

执行目录：`data/results/wo0016-stageB-20261019-133236/`  
授权 boot_id：`58e6af6c-6660-4d44-b0c5-e18f4a564202`  
物理状态：24V 光源关闭，传送带关闭。

## 结论

本轮状态为 **BLOCKED_PRECHECK**，不是 DMA PASS，也不是 DMA FAIL。板端冷启动身份仍与授权 boot_id 一致；只读预检测得 `CmaFree=127492 KiB`（门槛 `32768 KiB`），进程所有权扫描无发现，FPGA Manager 为 `operating`，相机参数门禁通过。因为 CMA 已高于红线，本轮没有执行全局 `drop_caches`。

受控执行器随后在 12 秒限时的 `pynq.Device.devices` 枚举预检处超时（退出码 124），因此未启动 Stage B runner，未消费 smoke guard，也未启动 DMA。3 帧结果为 **未运行**；没有可报告的物理 DMA 地址或 DMASR。按 fail-closed 门禁，本轮不重试设备枚举、Overlay 或 DMA。

8080 端口只读检查未发现监听者，实时 MJPEG 预览服务**未启动/未就绪**。请继续保持光源与传送带关闭。后续板端尝试需重新授权并先解决 PYNQ 设备枚举超时。

## CMA 回收修复与观测

本轮软件改动加入了 DMA 分配前后的 `gc.collect()`、对部分分配失败的显式释放、完成安全 DMA 后的缓冲区释放，以及有所有权/boot 门禁的单次 CMA 恢复逻辑。若读数低于 `32768 KiB`，只有在进程/设备所有权检查干净且 boot 身份正确时才会尝试一次内核缓存回收；仍不达标则熔断，不杀进程、不自动重试。入口脚本、设备探测与运行器都保持有界执行。

实际重试前读数已是 `127492 KiB`（总 CMA `131072 KiB`）。冷启动身份匹配；设备进程扫描为空。回收策略没有写入 `drop_caches`，也没有必要强制回收。

## Stage 1 与 Stage B 门禁

本 boot 的先前 Stage 1 Overlay 回执仍在板端，FPGA Manager 状态为 `operating`；对应回执与身份证据见 [`overlay-only-pass.json`](../wo0016-stageB-20261019-130149/board-evidence/overlay-only-pass.json) 和 [`overlay-evidence.json`](../wo0016-stageB-20261019-130149/board-evidence/overlay-evidence.json)。本轮最终状态检查显示该 boot 下 smoke3 与 batch guard 均未消费，WO-0015 terminal-failure 标记不存在。由于 PYNQ 设备枚举未通过，本轮未验证 Tier 2 物理 DMA 分配，也未观察 DMASR。

## 代码、包与离线验证

板端执行器已加入显式 profile 加载和限时 PYNQ 枚举，避免无限等待；CMA 恢复、所有权拒绝、DMA 分配回收及包内入口哈希绑定均有离线测试覆盖。本轮全量离线测试：**296 passed, 1 skipped**。`bash -n scripts/wo0016/phase16_board_entry.sh` 与 `git diff --check` 通过。

本轮执行包 SHA-256：`73b3cb2bbdafdede374f8e9333ac8f872382cc8bbc6b45d7187285c57ed564af`。板端包文件哈希校验见 [`board-package-sha256-verify.txt`](board-package-sha256-verify.txt)，24 个包文件与包 manifest 均通过。BIT/HWH 仍与授权哈希一致；包校验不代表板端已运行新执行器或完成 DMA。

## 证据索引

- [`pre-smoke-readonly.txt`](pre-smoke-readonly.txt)：boot、CMA、FPGA Manager、相机门禁与进程所有权只读快照。
- [`board-smoke3-output.txt`](board-smoke3-output.txt)：受控冒烟命令输出；显示 CMA 门禁通过及 `cache_drop_attempted=0`，之后 PYNQ 探测超时。
- [`board-final-state.txt`](board-final-state.txt)：guard 消费标记、分析文件和 8080 监听状态。
- [`post-timeout-readonly-diagnostic.txt`](post-timeout-readonly-diagnostic.txt)：超时后只读诊断，boot/CMA 仍通过，进程扫描无发现。
- [`smoke3-disposition.json`](smoke3-disposition.json)：机器可读停点状态。
- [`package-build.json`](package-build.json)：本地执行包与授权 BIT/HWH 的哈希。
