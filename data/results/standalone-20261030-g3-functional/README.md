# G3 功能回归进度（部分）

**状态：G3 未放行。** 已有一组固定回放在显示开启/关闭时的逐帧输出比较；同序列结果一致，且本组显示开启回放的近 30 秒窗口处理速率和有效新帧速率均超过 10 FPS。绘图启动失败时计数继续与有界重试的代码已补入，但尚未验证；GPIO/HDMI 实机恢复场景、按钮竞态、缩放黄金图、遥测过期、真实相机有目标运行及其余放行项仍待完成。

## 本组结果身份

- 板卡启动周期：`e48ff153-bd85-4dd6-98f6-438bd51da08d`。
- BIT SHA-256：`79609f9a60c3e727b5f2f54a8d639a464a098715fb5eb28d294646a9735948e0`；与本轮 G2、G2.5 候选制品相同。
- 输入：同一 2,400 帧固定 replay，分别以 display=on 与 display=off 顺序运行。
- 结果文件：[开启显示](replay-on-v2.json)、[关闭显示](replay-off-v2.json)、[控制台记录](replay-v2-console.txt)。

两份运行 JSON 记录了相同的 boot ID、BIT、HWH、manifest、native library 和 commands SHA-256，但没有记录 Python runner 的提交号或源码 SHA-256。`failed-track-retention/staging-index.json` 属于更早的失败尝试，不能作为这组 v2 运行的源码身份。因此以下比较可证明所保存的这组开/关显示输出一致，但不能唯一复原其 Python runner 版本；G3 仍维持部分状态。

## 已完成的比较子项

| 指标 | Display on | Display off |
|---|---:|---:|
| 状态 | `STOPPED` | `STOPPED` |
| 处理帧数 | 2,400 | 2,400 |
| 总时长 | 105.498 s | 56.914 s |
| 累计计数 | 158 | 158 |
| 无效 RLE 帧 | 0 | 0 |
| 显示提交 | 1,807 | 0（预期） |
| 帧轨迹记录数 | 2,400 | 2,400 |
| 帧轨迹 SHA-256 | `94249f6cbaaf69db09c61bfdfd38720b0d703ade7772204b52c4556c6efb7427` | 同左 |
| 轨迹注释截断 | 否 | 否 |
| 相机/显示/RLE 缓冲安全释放标志 | 全部为真 | 全部为真 |

轨迹摘要覆盖每帧检测、轨迹、越线/抑制事件、累计值和轨迹 epoch；两轮 SHA-256 相同，且 `trace_frames=2400`、`trace_annotations_truncated=false`。显示开启时输出监测器的灰度、marker 像素和上下帧配对错误标志均为 false。此证据证明这条固定回放上的显示开关前后结果一致，不证明物料准确率。

显示开启运行的原始采样有 52 个端点；由相邻单调时钟端点计算出的处理 FPS P50/P95/P99 为 22.170/29.599/30.752，有效新帧 FPS 为 18.842/20.481/20.908。以每个端点为 30 秒窗口起点，在终点时间对相邻累计计数线性插值，共得到 37 个窗口；处理 FPS 最低/中位/最高为 21.668/21.879/24.412，有效新帧 FPS 为 13.521/18.974/19.379。总运行平均处理 FPS 为 22.749，监测器识别的新帧增量为 1,807（约 17.128 FPS）。这些速率来自该 replay 记录，不是本轮新运行。

启动阶段约前 10.1 秒尚未提交有效显示帧；输出监测器 invalid 计数在启动段增加 499，之后至运行结束保持不变。显示启动延迟已计入上述 30 秒窗口，最低有效新帧速率仍为 13.521 FPS。末态 gray error、marker pixel error 和 pair mismatch 均为 false；硬件计数是本次开机周期的累计值，不能将其绝对值误作本轮错误数。

## 仍未完成的 G3 项

- 真实相机有目标输入：本目录的 `camera-console.txt` 记录 `processed=0`，不构成相机计数验收。
- 灰度/二值区域几何、缩放黄金图和输出帧身份的独立核验。
- 四按钮消抖、长按边界、清零/暂停与帧边界事件顺序。
- 遥测过期提示；绘图进程退出/恢复和 HDMI 热插拔期间计数不停止、不重复。
- G3 完整功能集在同一制品身份上的汇总验收。
- 真实物料人工真值准确率；当前明确为未验收。

早期尝试及其修复依据保存在 `failed-track-retention/`：首轮 coordinator 读到尚未完成写入的 JSON，未启动 off 轮；随后为流式淘汰已完成轨迹并限制逐帧证据缓冲，才得到本页记录的 on/off 成对结果。失败记录保留，不把首轮描述成通过。

本次继续整理进度时未运行测试或板端任务；以上数字来自已保存的原始 JSON 和控制台日志。

## G3 回归场景准备（2026-10-30；未执行）

### 帧边界命令回放输入

两份 v2 运行 JSON 都记录了相同的 commands_sha256（556fb541b8592700f800a1d3ae3c0fa74c8a32cec608128e506ad9705599f00a）及七个命令事件：帧索引 80 pause、101 clear、160 pause、170 background、190 page、500 clear+page、501 background。对应记录中的计数依次为 7、0、0、1、3、0、0。

已从这些输出事件整理出新的[帧边界命令场景](replay-command-schedule-prepared.json)，SHA-256 为 2D2656815AC70479F8E00EB27BF07684A3557C8A7EE1EBAEC66D8B40817F4E95。该文件按 runner 的零起始循环帧索引组织，适合后续作为 --commands 输入重放。它是根据输出事件重建的新输入文件，不是 v2 原始命令文件；原始命令文件未保存在本目录，不能把新文件哈希当作原运行 commands_sha256。

这组记录可追溯地说明两种显示模式保存了相同命令摘要、相同命令事件和对应计数状态；它不能单独证明物理 GPIO 消抖、按压时长或按钮与帧边界的真实采样竞态。G3 状态仍未放行。

### 缩放黄金向量准备

已形成[缩放黄金向量准备稿](scaling-golden-preparation.md)，并将候选规则及期望样点机器化记录在[缩放候选向量 JSON](scaling-candidate-vectors.json)（schema `material-counter.scaling-candidate-vectors.v1`，状态 `candidate_unexecuted`）。内容包括常量灰度、横向 ramp 的 `INTER_AREA`/`INTER_LINEAR` 手算候选值及二值 nearest 的矩形/边界坐标；灰度策略仍未冻结。已用[标准库生成器](generate_scaling_candidate_sources.py)生成三份固定 raw 源输入，JSON 记录其相对路径、C row-major uint8 布局、`[400,640]` 形状、字节数和 SHA-256：

| 输入源 | SHA-256 |
|---|---|
| `scaling-inputs/constant-gray-73.raw` | `aa33e56924504144d0ea31c71ad74281064ba96c7572c2b303325091eea954af` |
| `scaling-inputs/gray-ramp-12.raw` | `87d33b08d68a971d3d0c86ddf7ce671f5059a2b6af19599cde9c42cb81476354` |
| `scaling-inputs/binary-nearest-rect-points.raw` | `467bf9002e72f182da0c3a65dfb5454d6c04a15dc5647c02f373d4c0e6bc5263` |

表中三份 raw 文件仅是输入材料。JSON 不是黄金图或通过证据。测试源码的二值输入夹具此前虽按坐标关系修正，但未运行。

现有四份候选 expected raw 均由标准库生成，C row-major uint8，shape `[360,576]`，各 207360 bytes：

| 候选输出 | 推导 | SHA-256 |
|---|---|---|
| [常量灰度](scaling-expected-candidates/constant-gray-576x360.raw) | 所有目标像素保持 73；对 `INTER_AREA` 和 `INTER_LINEAR` 均为候选不变量 | `ace13c7b9c44a6d95fe0724be529c495d044513433d558e51bac5ab199653ea2` |
| [二值 nearest](scaling-expected-candidates/binary-inter-nearest-576x360.raw) | 按 `src_index=floor(10*dst_index/9)` 从固定二值源逐行逐列采样 | `35264c92c0c52e311d0c63a5aea1ed662a78ee4ba9347f5ee2e89ab3588e792b` |
| [灰度 ramp INTER_AREA](scaling-expected-candidates/gray-ramp-inter-area-576x360.raw) | 精确 overlap 加权平均，源覆盖 `[10*j/9,10*(j+1)/9)`；Fraction 精确计算，round-to-nearest ties-to-even | `81e1988ffe0c929f88a500c88993e77cffb2e46e0b00f4f2c107e14ac92ae3f8` |
| [灰度 ramp INTER_LINEAR](scaling-expected-candidates/gray-ramp-inter-linear-576x360.raw) | 坐标 `(20*j+1)/18` 线性插值；Fraction 精确计算，round-to-nearest ties-to-even | `56f24d8548a06030abd22ea9f6a6c875a24014979b63496507a230b3dc6c1b1c` |

四份数组均为未执行的候选参考数据，不是 compositor 输出、实测比较或黄金证据。INTER_LINEAR 在目标列 `j=19` 有唯一精确值 `252.5`，本候选生成舍入约定取 `252`；尚未与 OpenCV 比较，不冻结运行时行为。INTER_AREA 576 列没有精确 half tie。PNG 未生成，没有 compositor comparison 或 runner hash 绑定；灰度策略未冻结，状态仍为 `candidate_unexecuted`，G3 未放行。

### 后续注入场景及边界

| 场景 | runner 已有入口与后续要记录的结果 | 覆盖边界 |
|---|---|---|
| 帧边界控制命令 | 用本页 JSON 作为 --commands 输入；记录命令摘要、逐帧事件和计数轨迹 | 只验证指定帧上的软件命令应用；不验证 GPIO 电平、约 30 ms 消抖或 BTN1 两秒长按边界 |
| 遥测过期提示 | 通过 --freeze-telemetry-at-frame N 停止周期遥测采集；观察遥测年龄超过 2 × 采样周期 + 1 s 后的过期状态，同时核对计数和安全检查仍运行 | 只模拟采集停止后缓存变旧，不模拟传感器、驱动或整条运行循环故障 |
| 绘图子进程恢复 | 显示开启时用 --inject-drawing-exit N 请求协作退出；记录恢复事件、进程身份、帧/计数连续性和共享帧 lease 回收 | 是协作式退出，不是强制崩溃、HDMI/VDMA 失败或 HDMI 热插拔 |
| 物理按钮与 HDMI 热插拔 | 在同一 G2 制品上实际操作 GPIO 按钮及 HDMI 连接，保存按键/显示状态和计数记录 | --commands、display=off 与绘图退出注入都不能替代物理输入或热插拔 |
| 缩放黄金图 | 固定源帧、预期输出图、缩放策略与像素比较结果并保存哈希 | 目前缺少可追溯的 G3 黄金图结果 |

上述场景仅用于排定后续 G3 执行内容。本次只准备了命令输入并梳理现有运行记录，没有执行回放、按钮、遥测或显示故障场景；没有新增 G3 通过项。

## 绘图启动失败恢复代码（2026-10-30；未验证）

- 初始启动与后续恢复启动均捕获子进程创建或 `process.start()` 异常，记录 `drawing_start_failed`、帧号、错误和 monotonic 时间；确认没有子进程启动时保持 `process=None`，每次失败后设置 5 秒重试间隔。
- 主循环支持从 `process=None` 重试恢复。只有确认旧子进程已死亡后才 join/reclaim 该子进程持有的共享帧 lease；没有活绘图子进程时跳过显示发布，RLE transfer 与 `advance_count` 继续按原流程执行。
- 记录启动错误后仍沿既有生命周期停止 VideoPump 并清理 HDMI 资源。此代码改动未运行任何检查或测试；GPIO/HDMI 实机恢复场景仍待后续统一验证，G3 未放行。
