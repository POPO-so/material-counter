# WO-0016 Stage B Phase 1 审计 · 2026-10-19

状态：`STOPPED_PRECONDITION_UNVERIFIED`。本次完成环境只读采集；没有执行 PYNQ 导入、Overlay 下载、MMIO、CMA 分配、DMA、相机采流或物料投放。3 帧冒烟、5 秒空转底噪和实时标定均为 `NOT RUN`。

## 宿主机

- 原目录 `C:\fpga\material-counter` 的 4 个已修改文档、1 个未跟踪文档保持原样。
- 复用 `C:\Users\POP\.codex\worktrees\wo0016-attempt6-optimization\material-counter`，分支 `codex/wo0016-counting-20261017`；审计开始时工作树干净，HEAD `59268209148888c135ff7ff5c0aa7dba8ddbaf66`，含 Stage A 交接 `60389df`。当前新增文件均为本轮工具/证据，不代表开始时残留脏文件。
- 限定测试：现有 venv 执行 `python -m pytest -q -p no:cacheprovider scripts/wo0016 scripts/wo0015`，`PYTHONDONTWRITEBYTECODE=1`。结果 `243 passed, 1 skipped, 1 failed`；原始输出见 `pytest-baseline.log`。
- 唯一失败需要 `C:\fpga\material-counter\data\results\wo0014-offline-20261014\vectors\manifest.json`，文件不存在。未生成替代清单，历史 244 passed 不能代替本次结果。
- `verify_delivery.py` 在 `conditions.csv` 的历史清单哈希处失败。本次核查证明当前 LF 内容转 CRLF 后恰为历史 6190 字节、SHA-256 `85ae53eb9af5fb264d5433207298b3f288c019667694d855062e4d886b0b234d`；原始文件未改。这解释该处换行差异，不代表完整验证器后续所有检查通过。见 `delivery-csv-check.json`。

## 板端只读门禁

| 检查 | 本次观测 | 结论 |
|---|---|---|
| Target | SSH `xilinx@192.168.2.99`，使用 BatchMode 和 sudo -n | 连接成功，不使用密码 |
| Boot | `58e6af6c-6660-4d44-b0c5-e18f4a564202` | 匹配本轮从前次采集继承的授权值 |
| CMA | Total 131072 KiB，Free 71684 KiB | 此采样点通过 32768 KiB 门槛；未分配后续缓冲区 |
| 进程 | 系统 Python 服务、Jupyter 和本次只读采集进程；快照未见旧 stream runner 或 Python 僵尸 | 未终止任何进程；快照不能单独证明 DMA 所有权 |
| 相机占用 | `fuser /dev/video0` 无持有者，退出码 1 | 此采样点空闲 |
| V4L2 身份 | uvcvideo，USB GS CAM，serial `20210616103`，USB bus `ci_hdrc.0-1` | 可用于设备绑定；friendly name 不能独立证明传感器型号 |
| 格式 | `/dev/video0`，640×400 MJPG；当前回读 210 FPS | 格式通过；不是应用实际采集 FPS，未改为 60 FPS |
| 参数 | auto_exposure=1，exposure_time_absolute=50，power_line_frequency=1，gamma=100 | 参数回读符合请求；没有修改参数或测量光学曝光效果 |
| 初始化服务 | enabled，Type=oneshot，RemainAfterExit=no，Result=success，ExecMainStatus=0；active 状态为 inactive | 此配置退出后 inactive 属正常语义；不满足先前字面 active 标准，但不能称初始化失败 |
| 固件文件 | BIT `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420`；HWH `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6` | 文件哈希与历史 manifest 匹配；不等于本次已加载身份 |
| 执行器 | 4 个 WO-0016 模块及 board_once/board_preflight 共 6 项，宿主与板端哈希一致 | 代码文件一致；board_preflight 仍硬绑定旧 Boot |
| 同 Boot manifest / Stage 1 | 所读 manifest、overlay-only-pass 回执均非本次 Boot；最新为旧 `f8b24aee-ed7e-430a-b3f3-d26056746bfd` | 未通过；未伪造或覆盖回执 |
| Guard | 活动 stream-counter-consumed.json 属旧 Boot，consumed=true | 保留原位；没有删除、替换或重试 |
| FPGA manager | state=operating | 不能据此判定已加载 WO-0015 的指定 BIT |
| Physical DMA | 请求 MM2S=0x15880000、S2MM=0x15860000 | 未分配/验证；不能复用冷启动前的缓冲区地址 |
| DMASR / AFI1 | 未读取 | 未越过加载身份及同 Boot 门禁去访问 MMIO |
| 8080 | 无监听 | 实机标定服务未启动 |

原始采集见 `board-readonly.json`、`board-service-diagnostic.txt`。后一个诊断的最后 journalctl 命令因 PowerShell→POSIX 输入末尾 CR 而失败；此前各项输出有效，不宣称已取得 journal。没有因此重复硬件操作。原始 JSON 中 `host_time_utc` 实际来自板端 OS 时钟（2025-05-05），不能用于本轮绝对时间排序；本目录使用宿主本轮日期，板端时钟未修改。

## 采样与预览状态

| 项目 | 状态 / 数值 |
|---|---|
| 3 帧实机冒烟 | NOT RUN |
| 5 秒空转底噪 | NOT RUN；observed_duration_seconds=null |
| 实测背景灰度均值 | null，目标 20–45 不充当实测 |
| 实测前景灰度峰值 | null，目标 160–235 不充当实测 |
| 实测前景误触发像素 | null，目标 0 不充当实测 |
| 实机计数 / DMASR | null / null |
| 实机 8080 | 未启动，没有可供当前画面验收的直播 |

现有 run_stream_counter.py 只支持 mock/board，现有预览只叠加 Binary8 计数线及总计数。不能把该入口称为完整灰度标定/批次流水线。离线分析组件的新增能力与集成边界另见 `scripts/wo0016/STAGEB_PHASE1.md`。

本轮新增 `stageb_analysis.py`、`test_stageb_analysis.py` 与 `STAGEB_PHASE1.md`。支持独立 Gray8/Binary8 的全帧灰度指标、连通域面积、ROI/阈值/FPS 标注和带观察时间的批次记录；命令行仅接受显式 `--offline` 配对 PNG 输入，拒绝覆盖现有输出目录。没有接入 live camera / DMA / 8080。Manager 审阅并补充了 5 秒跨度不能冒充连续采样、frame_id 严格递增、32 位状态值及输出保护的回归约束。最终新组件 11 项用例通过；Manager 对完整 `scripts/wo0016` 的最终运行结果为 `77 passed in 5.28s`，见 `pytest-wo0016-final.txt`。这不覆盖或消除 WO-0015 缺失规范输入的基线失败。

## 继续条件

操作员新增约定：实际采图前先提醒用户打开光源；空转采样或动态测试前先提醒用户启动传送带，等待现场就绪反馈后执行。当前没有要求用户开灯或开带，也没有远程控制这些设备。

先恢复/核对规范测试输入及通过有关检查；随后按本次 Boot 正式审阅执行器绑定和 manifest 哈希，建立真实同 Boot 的 Stage 1 加载回执，并依规范保留旧 guard、准备新的单次操作条件。不能通过运行时修改 AUTHORIZED_BOOT_ID、复制旧 PASS、单独改 manifest 或直接使用历史 DMA 地址放行。本轮已授权的 3 帧和 5 秒应各自有界、各自保留证据，任何门禁或执行异常都停止，不自动重试。投料测试继续等待操作员确认物理画面。
