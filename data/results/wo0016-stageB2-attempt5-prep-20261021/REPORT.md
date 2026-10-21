# WO-0016 Stage B-2 Attempt #5 离线修复与预备报告

日期：2026-10-21。工作目录：`C:/Users/POP/.codex/worktrees/wo0016-stageb2-no-consume/material-counter`。

**结论：Attempt #4 的 165 帧合格 CCL 回放通过：三段各一条轨迹、一次计数，合计 3 次。Attempt #5 未执行、未部署、未签发可执行许可：板卡已重启，缺少当前启动的 Stage 1 / manifest / READY 凭据。** 本轮仅离线修改和两次不同内容的只读 SSH 查询，没有相机采样、DMA、MMIO、Overlay 加载、清缓存、传送带操作或服务重启。

## 1. Attempt #4 逐帧诊断

[完整逐帧表](diagnosis.md) 覆盖 42–52、93–104、150–160；[CSV](qualified-ccl.csv) 含所有保存的组件，字段为质心、面积及含端点 bbox。[选定帧原始记录](selected-original-frames.json) 保留前景数量、行列投影和原事件。

- **A：几何支持近邻碎片重复计数。** f47/f48 两分量联合框分别为 19×18、19×17 px；f155/f156 为 22×23、22×21 px，质心差约 14 px，符合单颗螺母约 18–25 px 的大小。f45/46/49 还包含第三个邻近分量。没有视频或二值掩膜，不能证明内孔/反光的像素成因，也不能以 CCL 单独确认物理实体身份。
- **B：关键是四帧缺测，不是 dy=10 太严。** f96→97 的 dy=0.076 px；f97→102 的 dy=2.208 px。f98/99 总前景仅 9/10 px，低于 min_area=12，CCL 被跳过；f100/101 总前景 17/16 px，执行 CCL 但没有面积达 12 的组件。所有中段有效框顶在 ROI 上沿 y=96，支持边缘裁剪/弱前景；原 max_missing=1 无法跨过四帧空窗。没有保存小于 12 的组件几何，不能补造“所有原始连通域”或离线扩大 ROI。
- **C：165 帧是当前 no-consume Stage B-2 配置合同的固定值。** Attempt #4 实测 22.6726 FPS、7.27749 秒；这个耗时不保证下一轮相同，也不证明最后一颗在窗口内。改变帧数需要另行评审并重新绑定合同/许可，本轮保留 165。

## 2. 实现及范围

所有计数增强均由显式参数开启，并写入 `counting_config`，继续受 runner/counter SHA-256 和许可身份严格校验。旧默认行为不启用这些增强。

| 修改文件（相对 worktree） | 本轮变更 |
|---|---|
| `scripts/wo0016/stream_counter.py` | CCL 合格组件之后、关联之前，按每对质心 dx≤24、dy≤12 做有界近邻聚类，面积相加、质心面积加权、bbox 取并集；禁止无限传递链合并。新增 3 帧、Y 差≤15 px 的冷却去重；抑制的轨迹不会过期后再补计。保存聚类前合格 CCL 和抑制事件供审计。 |
| 同上 | 仅对已确认、已在线前武装、未计数且触及 ROI 边界的 +X/-X 轨迹提供最多 4 个缺帧的保持；重连要求同边界、Y 差≤18、方向/位移门、已有速度预测残差≤一帧 max_step、面积比、框尺寸及双向唯一性。缺帧穿线事件标记 `inferred_gap=true`。 |
| 同上 | f42→43 入口裁剪片面积 59→273 超过原 4 倍门限。仅在启用聚类、相邻帧、进图边缘及新框≤27 px 的条件下比较 bbox 占用率，避免把同颗入口片断成第二条轨迹。 |
| `scripts/wo0016/run_stream_counter.py` | 新增聚类/冷却/边缘保持 CLI 参数，先校验再进入一次性许可消费流程；Attempt #5 采用 dy=18，min_area=12、X=160、THRESHOLD=128 均保持。 |
| `scripts/wo0016/test_run_stream_counter.py` | 新增分裂双瓣、加权几何、传递链拒绝、分离目标、冷却边界/同帧抑制、边缘缺帧恢复/歧义/过期/运动不一致拒绝、入口片及回放原始 CCL 优先等回归。 |
| `scripts/wo0016/operator_gate_launcher.py` | 投料提示改为 0.75 秒/颗；0~3.5 秒投完；皮带纵向正中央、远离 ROI 上下边缘。15 秒准备+3 秒稳姿、点击门禁及 165 帧不变。 |
| `scripts/wo0016/test_operator_gate_launcher.py` | 更新节奏时刻及页面提示断言。 |
| `scripts/wo0016/replay_recorded_components.py` | 新侧录优先回放 `raw_detections`，旧侧录回退 `detections`，避免对已合并观测二次聚类。 |

本轮增量 diff 位于本目录 `*.py.diff`；修改前的已有脏源文件另存 `baseline/`，受测源文件在 `tested-source/`。没有清理、覆盖原主 checkout，没有提交或推送。

## 3. Offline Replay Gate

使用原始 `frame-results.json` 全部 165 帧，先按原配置核验原累计计数和事件逐帧一致，原总数 4；再按修复配置回放。输入 SHA-256：`b17d868aa818fd6e156a9bf729dc24163ca98c604fd18a3151b60430d1bf9622`。

| 活动段 | 修复轨迹 | 事件帧 | 计数 | 证据性质 |
|---|---|---:|---:|---|
| 42–52（原 T2/T3 等碎片） | T1，一条 | 48 | 1 | 有连续线前/线后观测 |
| 93–104（原 T6/T7） | T2，一条 | 102 | 1 | **推断**：f97→f102，中间四帧无合格观测 |
| 150–160（原 T8/T9） | T3，一条 | 156 | 1 | 有连续线前/线后观测 |
| 合计 | 3 条 | — | **3** | 两次观测跨线，一次缺帧推断；没有重复计数事件 |

消融结果：仅把 dy 放宽为 18 仍计 4；聚类后计 2；聚类+冷却仍计 2；再启用受限边缘恢复计 3。完整配置、每帧聚类前后组件及事件见 [配置](counting-config.json)、[观测审计](merged-observations.json)、[回放](replay-attempt5.json)、[门结果](offline-gate.json)。本例聚类已消除双计，冷却抑制数量为 0；冷却兜底另由单元测试验证。

**唯一的本轮复现入口**（从 worktree 根目录执行）：

```powershell
C:/fpga/material-counter/.venv/Scripts/python.exe data/results/wo0016-stageB2-attempt5-prep-20261021/verify_offline_gate.py
```

该入口导入实际生产计数逻辑，并从 CLI 参数生成配置，验证三段各一轨一事件、历史文件未变及归档全部字节。通用 `replay_recorded_components.py` 的 CLI 仍默认旧配置，不应把它的默认调用当作 Attempt #5 复现入口。

这是合格 CCL 的离线算法验证，**不是五颗实物验收、也不是三颗实物身份已被图像证明**。推断重连仍可能把满足约束的其他目标认作原目标；聚类及冷却也可能合并极近的独立目标。它们适用于本轮分开投料的 M6 螺母配置，不能据此声称通用密集目标准确率。

## 4. 测试与归档

- 最终联合运行：`C:/fpga/material-counter/.venv/Scripts/python.exe -m pytest scripts/wo0016 scripts/wo0015 -q --junitxml=.../pytest-final.xml`。
- **WO-0016：109 passed，0 failed，0 skipped。** 核心 `test_run_stream_counter.py` 为 56 passed。新增行为经过先失败、再实现、再通过的回归过程。
- 联合总结果：**286 passed、1 skipped、1 failed**。唯一失败为 WO-0015 `VectorContractTests.test_prepare_a_validates_source_manifest_and_records_frame_hashes`：原 WO-0014 H3 输入 `data/results/wo0014-offline-20261014/vectors/manifest.json` 在隔离目录和原目录均缺失；没有重建来源清单或跳过失败来宣称全通过。详见 [原始日志](pytest-final.log)、[结构化结果](test-verification.json)。
- Attempt #4 全部原文件 SHA-256 与修改前快照一致；本地完整归档 `attempt4-preserved.tar.gz` 已逐文件比对。原 FAIL 报告、许可和 consumed marker 均保留，未被回放结论覆盖。见 [归档校验](archive-verification.json)。板端归档/部署未进行，原因见下一节。

## 5. 当前板端门禁与 Attempt #5

只读 SSH（`BatchMode=yes`、`StrictHostKeyChecking=yes`、`sudo -n`）核验地址 `xilinx@192.168.2.99`。读取结果只是核验时刻的快照，正式执行前必须重查。

| 检查项 | 实测 |
|---|---|
| 当前 Boot ID | `d9f65950-d59b-4ea4-bee4-b28656cb176f` |
| Attempt #4 / 既有 Stage 1 / manifest 的 Boot ID | `6f13f0f0-4433-445f-a023-fa403685aeee`，已非当前启动 |
| 当前启动 READY Guard | **不存在**；不能报告 consumed=false |
| CMA | CmaTotal=131072 KiB，CmaFree=72080 KiB，>32768 |
| `/dev/video0`、`/dev/video1`、`/dev/mem` | fuser returncode=1，无占用 |
| 8080/8081 | 均未监听 |
| FPGA Manager | operating / flags=0；不代表旧 Stage 1 适用于本次启动 |
| Attempt #5 许可 | **未签发** |
| 板端代码部署 | **未执行**，仍保留既有包 |
| 硬件采样 | **未启动** |

原始 SSH 回执见 `board-readonly-ssh.json`、`board-provenance-readonly.json`。本地状态为 `BLOCKED_BOOT_CHANGED_NO_SAME_BOOT_READY`；[许可准备草案](ATTEMPT5-PERMIT-NOT-ISSUED.json) 明确不可执行，没有 `AUTHORIZED` 许可，没有借用旧 Guard、改写 Boot ID 或重置 spent marker。

下一步必须先经授权完成本次启动的 Stage 1/manifest/READY 凭据，再归档板端旧证据、部署受测代码并校验源码、签发新的一次性 Attempt #5 许可；随后仍需操作者明确物理就绪才能启动。不能仅凭“物理就绪”忽略当前 Boot 门禁。

## 6. 操作者投料卡（待门禁与许可有效后使用）

固定 165 帧。点击后仍为 15 秒准备+3 秒稳姿，看到 **`FRAME_0_CAPTURE_START_FEED_NOW` / 立即投料** 才开始。按约 **0.7~0.8 秒/颗**依次投放，建议时刻为 **0、0.75、1.50、2.25、3.00 秒**，**0~3.5 秒内投完 5 颗**。放在皮带**纵向正中央**，远离 ROI 上下边缘，保持光源亮度、相机位置和拍摄物不变。

投料位置到视野的运输延迟必须留在采样窗口内；上述时序只是操作计划，不能保证五颗都入镜。当前保持无采样状态，等待新启动门禁准备及后续明确物理就绪指令。
