# G3 功能回归进度（部分）

**状态：G3 未放行。** 旧 v2 显示开/关回放已通过；本轮当前源码在同一 G2 制品上重跑 2,400 帧，轨迹、事件、计数与源码身份一致。346 项相关软件回归通过；本地 AREA ramp、常量灰度和二值 nearest 候选比较通过，LINEAR 候选有 640 个像素差异。绘图协作退出恢复和遥测冻结场景已通过。实体按钮消抖/长按、HDMI 热插拔、真实相机有目标输入及物料真值仍待完成。绘图进程创建/启动故障的单元用例和 runner 注入参数已加入当前分支，尚未执行；G3 未放行，G4 未验收。

### 最新代码阶段事实（2026-10-30）

本分支集成绘图从 `LINEAR` 改用 `AREA`。此前固定 ramp 的本地 AREA 比较匹配，但本次代码未测试或板验；灰度正式策略及黄金验收仍未冻结。历史 `346 passed` 属于旧代码版本，不是本分支测试结果。运行报告新增有界原始 GPIO 采样变化、总数和截断字段；实物 GPIO 未测。G3/G4 均未放行。

## 本组结果身份

- 板卡启动周期：`e48ff153-bd85-4dd6-98f6-438bd51da08d`。
- BIT SHA-256：`79609f9a60c3e727b5f2f54a8d639a464a098715fb5eb28d294646a9735948e0`；与本轮 G2、G2.5 候选制品相同。
- 输入：同一 2,400 帧固定 replay，分别以 display=on 与 display=off 顺序运行。
- 结果文件：[开启显示](replay-on-v2.json)、[关闭显示](replay-off-v2.json)、[控制台记录](replay-v2-console.txt)。

两份运行 JSON 记录了相同的 boot ID、BIT、HWH、manifest、native library 和 commands SHA-256，但没有记录 Python runner 的提交号或源码 SHA-256。`failed-track-retention/staging-index.json` 属于更早的失败尝试，不能作为这组 v2 运行的源码身份；未来新增的 `source_identity` 字段只适用于采用该字段后生成的新运行报告，不能追补或反向归属这两份旧 v2 记录。因此以下比较可证明所保存的这组开/关显示输出一致，但不能唯一复原其 Python runner 版本。

旧 v2 报告仍没有 source_identity，不能追补。当前源码运行报告已记录完整 source_identity，每轮均为 25 个项目内 Python 文件，digest 69d2f9996c3bfd4e0ff5fddc610dc355d57b59fd53789fb822efe5eda017815a。该身份只覆盖项目内 Python 源，不代表第三方依赖、ABI 或位流身份。

## 当前源码身份回放与故障场景（2026-10-30）

本轮使用临时 G3 包，源码提交 838f5ebb2a5562f72d7695b80d3c93c8265d9b76，deployment index SHA-256 12e4e93f553270c7a6028dd63e2f461fa5331e04597cf8fb95fa50740d31d70d。该包使用既有 G2 BIT/HWH/manifest/native library；包仅用于本轮验证，没有安装 systemd 服务或改动板端系统部署。板卡 boot ID e48ff153-bd85-4dd6-98f6-438bd51da08d，BIT SHA-256 79609f9a60c3e727b5f2f54a8d639a464a098715fb5eb28d294646a9735948e0。

| 结果 | 关键结果 | 报告 SHA-256 |
|---|---|---|
| [当前源码显示开启回放](replay-on-current.json) | 2,400 帧，STOPPED，计数 158，无效帧 0，时长 104.386 s | 9e7a80f00338479716f58540e301b30e002f72af3cdd8ae4b90b6dfc943b6240 |
| [当前源码显示关闭回放](replay-off-current.json) | 2,400 帧，STOPPED，计数 158，无效帧 0，时长 57.116 s | 29fe217f7a808b3361948572b696f1439c6a2536c21de492256c5df0d8815d5a |
| [协作式绘图退出恢复](drawing-exit-current.json) | 800 帧，STOPPED，在帧 338 记录 drawing exit code 0，计数 67 | d9881640446981451e19f3f02a8d33bf8907ff2357850c9c664940e14dd22aca |
| [遥测冻结](telemetry-freeze-current.json) | 1,200 帧，STOPPED，计数 100；遥测变旧后计数继续 | 99ce896d38b834b3ba4f7d7c91f36886e073afb0f90023b32ff37e7dcab2c3c4 |

开/关回放使用相同命令输入 SHA-256 2d2656815ac70479f8e00eb27bf07684a3557c8a7ee1ebaec66d8b40817f4e95，两轮 trace_sha256 均为 94249f6cbaaf69db09c61bfdfd38720b0d703ade7772204b52c4556c6efb7427，trace_frames=2400、invalid_frames=0、命令事件和最终计数均一致。两轮传输校验均有效：接收 5,452,800 字节、输入 beat 153,619,200、输出 beat 1,363,200。两份报告的源码身份 digest 相同：69d2f9996c3bfd4e0ff5fddc610dc355d57b59fd53789fb822efe5eda017815a，覆盖 25 个已加载项目 Python 文件，complete=true、错误列表为空。显示负载不同导致总周期/时长不同，不要求硬件周期相同。

绘图退出轮处理帧数和传输计数完整，buffers_released=true、rle_buffers_released=true；遥测冻结轮在样本中观测到 performance、resources、xadc 均转为 stale，处理到第 1,200 帧且传输校验有效。所有板端运行报告均为 STOPPED，之后没有 integrated_run 或预览进程。

### 软件与缩放比较

项目相关软件回归使用 CPython 3.12.10，命令 `python -B -m pytest -p no:cacheprovider --basetemp <系统临时目录> scripts/standalone scripts/rle scripts/gray8 -q`，结果 **346 passed**。Windows 默认 python 指向 LibreOffice 内嵌 Python 3.13 且无 pytest；本轮改用项目记录的 CPython 3.12。

[缩放比较报告](scaling-comparison/comparison.json)绑定 OpenCV 4.10.0 和本地 source identity digest d169e6841cf14935f2fdb378460921ee8c7d57a0554734dc77e501ce9b3204a8。常量灰度在 AREA/LINEAR 下以及 AREA ramp 的 207,360 个像素均匹配；nearest 比较排除计数线遮盖的 7 列，其余 204,840 个像素均匹配。LINEAR ramp 候选有 640 个像素不匹配，差异出现在目标列 0、6、9、12、15；本轮只记录差异，不推断 OpenCV 内部舍入原因，也不冻结插值策略。实际输出 raw 和 PNG 位于 scaling-comparison/。

### 尚未完成

- 实体 GPIO 按钮的消抖、浮点、长按边界和真实帧边界竞态。
- HDMI 实际拔插；协作退出不代表 HDMI/VDMA 故障或热插拔。Process.start 异常路径也未被实机注入。
- 有目标真实相机输入和人工物料真值准确率。
- LINEAR 候选需独立解释或替换候选规则；灰度插值策略仍未冻结。
- G3 全部放行条件汇总，以及后续 G4 冷启动/有目标长时运行。

板端系统时钟早于本机归档时间，解压时出现未来时间戳提示。本轮以 boot ID、源码/制品哈希和 monotonic 计时为准，不使用板端文件时间推断运行先后。

## 已完成的比较子项（旧 v2 运行；当前源码回放见下节）

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

## G3 回归场景准备（2026-10-30；以下为运行前状态记录）

### 帧边界命令回放输入

两份 v2 运行 JSON 都记录了相同的 commands_sha256（556fb541b8592700f800a1d3ae3c0fa74c8a32cec608128e506ad9705599f00a）及七个命令事件：帧索引 80 pause、101 clear、160 pause、170 background、190 page、500 clear+page、501 background。对应记录中的计数依次为 7、0、0、1、3、0、0。

已从这些输出事件整理出新的[帧边界命令场景](replay-command-schedule-prepared.json)，SHA-256 为 2D2656815AC70479F8E00EB27BF07684A3557C8A7EE1EBAEC66D8B40817F4E95。该文件按 runner 的零起始循环帧索引组织，适合后续作为 --commands 输入重放。它是根据输出事件重建的新输入文件，不是 v2 原始命令文件；原始命令文件未保存在本目录，不能把新文件哈希当作原运行 commands_sha256。

这组记录可追溯地说明两种显示模式保存了相同命令摘要、相同命令事件和对应计数状态；它不能单独证明物理 GPIO 消抖、按压时长或按钮与帧边界的真实采样竞态。G3 状态仍未放行。

### 缩放黄金向量准备

已形成[缩放黄金向量准备稿](scaling-golden-preparation.md)，并将候选规则及期望样点机器化记录在[缩放候选向量 JSON](scaling-candidate-vectors.json)（schema `material-counter.scaling-candidate-vectors.v1`，当前状态 `comparison_complete_candidate_only`）。内容包括常量灰度、横向 ramp 的 `INTER_AREA`/`INTER_LINEAR` 数学候选及二值 nearest 的矩形/边界坐标；比较细节见下文，灰度策略仍未冻结。已用[标准库生成器](generate_scaling_candidate_sources.py)生成三份固定 raw 源输入，JSON 记录其相对路径、C row-major uint8 布局、`[400,640]` 形状、字节数和 SHA-256：

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

四份数组仍是独立数学候选，比较结果记录在 [scaling-comparison/comparison.json](scaling-comparison/comparison.json)。OpenCV 4.10.0 下，常量灰度两种插值、AREA ramp 及 nearest 未覆盖计数线的 204840 个像素均匹配；LINEAR ramp 有 640/207360 个差异，集中在输出列 0、6、9、12、15，候选未接受为运行时黄金。五份实际区域 raw/PNG 已保存；比较绑定的本地 source identity digest 为 d169e6841cf14935f2fdb378460921ee8c7d57a0554734dc77e501ce9b3204a8。灰度策略仍未冻结；G3 未放行。

### 后续注入场景及边界

| 场景 | runner 已有入口与后续要记录的结果 | 覆盖边界 |
|---|---|---|
| 帧边界控制命令 | 用本页 JSON 作为 --commands 输入；记录命令摘要、逐帧事件和计数轨迹 | 只验证指定帧上的软件命令应用；不验证 GPIO 电平、约 30 ms 消抖或 BTN1 两秒长按边界 |
| 遥测过期提示 | 通过 --freeze-telemetry-at-frame N 停止周期遥测采集；观察遥测年龄超过 2 × 采样周期 + 1 s 后的过期状态，同时核对计数和安全检查仍运行 | 只模拟采集停止后缓存变旧，不模拟传感器、驱动或整条运行循环故障 |
| 绘图子进程恢复 | 显示开启时用 --inject-drawing-exit N 请求协作退出；记录恢复事件、进程身份、帧/计数连续性和共享帧 lease 回收 | 是协作式退出，不是强制崩溃、HDMI/VDMA 失败或 HDMI 热插拔 |
| 绘图进程创建/启动异常 | 当前 runner 提供 `--inject-drawing-start-failure {create,start,after-start}`；运行后记录 `drawing_start_failed`、主链帧/计数连续性、重试时间及残留子进程清理 | 参数和用例已写，尚未执行；after-start 是进程成功创建后人为抛错，不能证明操作系统自然产生的迟发异常 |
| 物理按钮与 HDMI 热插拔 | 在同一 G2 制品上实际操作 GPIO 按钮及 HDMI 连接，保存按键/显示状态和计数记录 | --commands、display=off 与绘图退出注入都不能替代物理输入或热插拔 |
| 缩放黄金图 | 固定源帧、预期输出图、缩放策略与像素比较结果并保存哈希 | 目前缺少可追溯的 G3 黄金图结果 |

上表记录的是本轮执行前准备状态。当前源码回放、遥测冻结和协作式绘图退出的结果见本页“当前源码身份回放与故障场景”及新报告链接；物理 GPIO 与 HDMI 热插拔仍未执行。

## 绘图启动失败恢复代码与协作退出验证（2026-10-30）

- 初始启动与后续恢复启动均捕获子进程创建或 `process.start()` 异常，记录 `drawing_start_failed`、帧号、错误和 monotonic 时间；确认没有子进程启动时保持 `process=None`，每次失败后设置 5 秒重试间隔。
- 主循环支持从 `process=None` 重试恢复。只有确认旧子进程已死亡后才 join/reclaim 该子进程持有的共享帧 lease；没有活绘图子进程时跳过显示发布，RLE transfer 与 `advance_count` 继续按原流程执行。
- 本轮板端协作退出注入在处理帧 338 触发 drawing exit code 0；800 帧仍全部处理，计数为 67，传输校验有效，DMA/VDMA 和 RLE/显示缓冲释放标志均为真。该场景没有注入 Process.start 创建/启动异常，也不代表 HDMI 热插拔。当前分支新增了 `--inject-drawing-start-failure` 三种模式和对应单元用例，尚未执行；物理 GPIO/HDMI 场景仍待执行，G3 未放行。

## 真实相机显示首帧问题（2026-10-30）

- 用户需要的是板端真实相机实时画面和计数，回放不能替代。此前一次集成启动未显示图像，报告为 `processed=0`、`submitted=0`、`total_count=0`，相机反复报“no fresh real-camera Gray8 frame”；随后已安全停止，DMA/VDMA 和缓冲释放标志为安全状态。
- 板端 V4L2 能读到 USB 摄像头 MJPEG；OpenCV `VideoCapture`、项目 `camera_frames()` 解码以及 `LatestCamera.read()` 单独运行均得到 640×400 Gray8 帧。因此设备路径和单独解码链可用。
- 根因定位到集成主循环对相机**首帧也只等待 250ms**。板端相机初始化/丢弃前两帧可能需要数秒；已有相同设备运行曾出现约 12 秒首帧延迟，250ms 会在取到首帧前触发关闭和重试。
- 修复已提交在 `fix/修复真实相机首帧等待`：首次取帧（包括重连）最多等 15 秒，稳定运行后仍以 250ms 检测新帧。源码提交 `18d04b57929e78b53e75aae0d51b728f0b07dfc9`；G4 候选包源码副本已刷新，索引尚未重建。
- 本轮没有运行测试、构建或仿真。修复后已在板端启动真实相机 + HDMI 连续运行；快照 `/tmp/material-counter-user-display-20261030-fixed.snapshot-0.json` 记录 `processed=1532`、`submitted=1267`、`total_count=0`、`drawing_pid=841`，相机恢复错误列表为空。运行器主 PID 为 795，当前保持运行供用户测试。此记录证明帧处理及显示发布持续发生，尚无人工确认屏幕可见性，也没有真实物料计数真值；G3 仍未放行。
