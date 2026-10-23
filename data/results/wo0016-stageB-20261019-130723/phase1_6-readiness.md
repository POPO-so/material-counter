# WO-0016 Stage B Phase 1.6 就绪报告

- 执行时间：2026-10-19（本次板端包标识 `130723`）
- 工作分支：`codex/wo0016-stageb-phase1-5`，基线 `5926820`
- 授权冷启动：`58e6af6c-6660-4d44-b0c5-e18f4a564202`
- 总状态：**部分完成 / 冒烟被 CMA 门禁拦截；未进入 DMA；8080 未启动**

## 软件修复与离线验证

已在工作分支实现本阶段所需的板端执行器修复：

1. 特权板端操作通过已传输脚本和 `sudo -n bash <file>` 执行，消除嵌套 `sudo -S -i bash -c` 引号路径。
2. 板端 root 执行器显式加载可读的 `/etc/profile.d/*.sh`，去掉可能阻塞的独立 `xbutil` 探测；设备门禁使用 `len(Device.devices) > 0` 与 FPGA Manager `operating`。
3. 相机门禁以 V4L2 实测 MJPG 640×400、曝光 50、防闪烁 1、Gamma 100，以及 `ExecMainStatus=0` 判定；oneshot 服务显示 `inactive` 时仍按成功退出状态处理。
4. 首次打包遗漏的 `prepare_vectors.py` 已加入运行包与哈希清单，并增加包内容回归测试。

执行结果：`pytest scripts/wo0015 scripts/wo0016` 为 **290 passed, 1 skipped**；`bash -n scripts/wo0016/phase16_board_entry.sh` 与 `git diff --check` 通过。当前工作树保留此前 Phase 1.5 变更及本轮产物，未清理、重置或提交。

板端包位于 `board-runtime-final.zip`，SHA-256：`67b73180d883497a97eae51b7fdb1d30c4baba6ca85d6e272758a6a00f67b539`。包清单 SHA-256：`984460bae2f0b2ae474c36f55195579ec2448d74421677f2cf7c055ffd5d2ea1`；Stage B 运行清单 SHA-256：`8192aac863e8f23d422cfbe39d7ff68dede830dbafc13f7c05259e62c333104f`。最终包传输后 24 个清单文件的板端 SHA-256 校验通过。

## 板端 Stage 1 回执

当前 boot 与授权值严格匹配。Stage 1 Overlay 加载和身份核验**通过**：`overlay-only-pass.json` 状态为 `OVERLAY_LOAD_PASS`，FPGA Manager 为 `operating`（flags `0`），PYNQ 设备枚举为 1 台 `Pynq-Z2`。AFI1 HP1 32-bit 同步通过；Checker/Packer 签名分别为 `0x574f3134` / `0x574f3135`。BIT、HWH 哈希与授权基线一致；本次绑定的 WO-0015 manifest 哈希为 `e07316b9398166c6fc7a459faaca271eaf32170c18b4deaa4c16208bcaa933f2`。

相机 V4L2 回读通过：`/dev/video0`、MJPG 640×400、`exposure_time_absolute=50`、`power_line_frequency=1`、`gamma=100`；`ov9281-init.service` 的 `ExecMainStatus=0`，其 `inactive` 状态符合 oneshot 成功退出语义。

Stage 1 门禁读取的 `CmaFree` 为 `33516 KiB`，高于 32768 KiB 门槛。**这只证明 Overlay 阶段门禁通过，不代表 Tier 2 DMA 地址已分配或验证。** 本阶段没有分配 MM2S/S2MM 缓冲区，因此没有可报告的物理地址，也没有 `DMASR` 读数。

回执和核验原件已复制到 `board-evidence/`：`overlay-evidence.json`、`overlay-only-pass.json`、`overlay-only-consumed.json`。回执 JSON SHA-256 为 `2c71a8562c37781db422489054060517ca87cf97547df38488f41b58e27532db`。

## 3 帧静态冒烟结果

**冒烟未运行，0/3 帧完成。** 修复后的运行包在冒烟入口预检时读到 `CmaFree=12976 KiB`，低于要求的 `32768 KiB`，执行器在 StageB guard 消费和 DMA 启动之前 fail-closed。随后只读诊断读到 `15168 KiB`（外层快照 `15112 KiB`），仍不达标。当前证据不支持推断 CMA 下降原因，也不构成 DMA 成败或泄漏结论。

最终只读状态核对确认：Stage 1 guard 已消费、PASS 回执存在；没有 terminal failure 标记；StageB smoke/batch guard 均不存在；冒烟分析与证据文件不存在。因此未发生重试、Overlay 重载、DMA 操作或 guard 消费。`ss -ltn` 也未发现 8080 监听。

## 当前停点

8080 MJPEG 预览**未启动**，`http://192.168.2.99:8080/` 当前不可用。物理光源与传送带请继续保持关闭；本轮没有达到提醒开启它们的条件。请先恢复并确认板端在 Overlay 后稳定满足 `CmaFree >= 32768 KiB`，再针对届时有效的 boot 重新核验授权、manifest 与一次性 guard；若发生重启，必须使用新 `boot_id` 重建门禁。完成并通过新的 3 帧静态冒烟后，才能启动预览并请你开启光源和传送带。

## 证据文件

- `board-final-state.txt`：最终 boot、guard 和监听端口只读快照。
- `post-stage1-readonly-diagnostic.txt`：CMA、FPGA Manager、进程所有权与相机只读诊断。
- `smoke3-disposition.json`：冒烟门禁结果结构化记录。
- `board-evidence/`：Stage 1 回执、消费标记与完整证据。
- `board-runtime-final.zip`：最终离线执行包及其校验清单。
