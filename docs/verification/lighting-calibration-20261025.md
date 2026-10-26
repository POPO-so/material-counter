# 2026-10-25 相机角度调整后的调光参数接入与验证

本次标定背景是为了适应实际进料与工业应用而调整摄像头角度，所有配置和结论均基于图像证据。

阶段三完成了本地代码接入与离线验证。通过 SSH/SCP 读取用户先后指定的两组 PNG/JSON，共四个服务端文件。本报告中的阶段三证据不包含上板运行、DMA、输送带动态过料或FPS实测；这些数据只有在阶段四完成对应硬件步骤后才会补记。

## 快照证据

原路径：`/home/xilinx/lighting-tuner-20261025/snapshots/lighting_20250509T172731_2856b9c8ba9d4089af9c5bfdac1cb04e.{png,json}`。
副本：`data/calibration/lighting-20261025/`；SHA-256 见 `data/results/lighting-calibration-20261025/verification.json`。
指定快照 timestamp 为 **2025-05-09T17:27:31.463296+00:00**，与本次工作日期不同；保留原时间，不把文件名当作真实标定日期。

JSON 参数：TH100、overexp255、ROI[20,380)、min_area60、exposure50/gamma100/gain0。`hardware_applied=null`，故快照不证明硬件已设置。
实际 JSON 指标为 BG28.3346、FG215.9246、左/中/右16.5044/46.7288/22.3840，过曝0.0998264%（PASS）。这与用户摘要27.29/211.82/0.02%不同，采用原始JSON作为该帧证据。均匀性为UNEVEN，all_pass=false；不能写成所有光学指标通过。

| 同帧零件 | 源图净轮廓面积 | 外轮廓面积 | 孔轮廓面积 | 缩放后前景像素数 |
|---|---:|---:|---:|---:|
| 小 |269.5|367.5|98|80|
| 中 |390.5|532.5|142|111|
| 大 |548.5|728.5|180|150|

用户随后确认：小到大依次为M6平垫圈、M6螺母、M8螺母。配置已将三种实物名称绑定到相应面积提示；由于每种仍只有一个单件标定样本，区间在相机角度改变或边缘遮挡下仍标为暂定。

## 代码审计与生效范围

| 位置 | 旧值或行为 | 本次处理 |
|---|---|---|
| `scripts/wo0016/run_live_monitor.py:parser` | 默认 full-width-mixed | 默认改 lighting-calibrated，旧配置保留为显式选项 |
| `scripts/wo0016/run_stream_counter.py:counting_configuration` | full-width：min12、single95、merge260、split900、gap14、全高ROI | 增加新配置；历史配置数值保留 |
| `scripts/wo0016/stream_counter.py:MaskCounter` | lane半高24、min12、max900、single95、merge260；ROI只有full/lane | 增加显式ROI边界与独立split child下限；底层兼容默认保留 |
| `scripts/wo0016/live_calibration.py` | stretch/letterbox；软件TH128，ROI源全图→输出[20,220) | 增加lighting映射，源净面积过滤与TH100；旧映射仍128 |
| `scripts/wo0016/live_calibration.py:optical_metrics`、`camera_only_preview.py` | 光学诊断及旧预览TH128、Over-128文案 | 历史相机预览入口保留；新监控不调用该诊断链，不能用于本配置验收 |
| `scripts/wo0016/wo0017_capture_profile.py`、`run_stream_counter.py:STAGEB2_THRESHOLD` | 正式历史身份TH128 | 保留；新profile采集metadata用effective_threshold100，formal身份额外绑定实际阈值/映射 |
| `scripts/wo0015/prepare_vectors.py`、实验golden、`python/refmodel/ps_reference` | PL黄金阈值128；参考模型min16等 | 独立历史/合成参考，不是实时监控配置，不替换历史证据 |
| `fpga/rtl/axis_gray2binary.v`、`fpga/experimental/wo0014/axis_dma_image_pipeline.v`、`fpga/experimental/wo0015/axis_dma_s2mm_pipeline_bd.v` | 综合参数THRESHOLD128、比较>=；无动态阈值寄存器 | 不伪造MMIO阈值写入；采用新profile专用TX补偿，位流不变 |
| `scripts/live_lighting_tuner.py:DEFAULT_PARAMS` | binary95、overexp245；ROI20/380、min60 | 默认改100/255，其余保留；该脚本原已存在但尚未跟踪 |
| 相机启动与旧只读gate | exposure50/brightness0；未要求gamma/gain | 新profile格式协商后写入并严格读回四项必需值：manual exposure1、exposure50、brightness0、gamma100；独立尝试gain=0。仅`unknown control 'gain'`记录为未验证例外，其他set/readback失败仍阻断；warmup后再次核对并将结果写入监控JSON |

`run_live_monitor.py` 现在默认使用新参数。`run_stream_counter.py` 是带历史工单/Permit的有限实验入口，**无参数时仍为legacy**；使用相机角度调整后的配置必须显式传 `--counting-profile lighting-calibrated`。旧physical-calibration模式仍要求full-width-mixed；不能复用旧READY/Permit作为本次硬件验证。

## 几何与面积口径

相机640×400按现有方式取偶数行/列，输出320×240并加20行letterbox：`x_out=x_source/2`、`y_out=y_source/2+20`。源ROI `[20,380)` 对应输出 **[30,210)**，不先把源图裁成360行再拉伸。MaskCounter只在这些行上计算，CCL/拆分bbox、质心和拒绝轮廓加回ROI top，轨迹、越线事件、可视化均使用完整320×240坐标；计数线X160等价源X320。

**源图净面积60先在640×400上按调光台相同RETR_CCOMP外轮廓减子孔轮廓口径执行。** 缩小后CCL使用15个前景像素作为辅助下限（面积尺度约1/4）；它不被当成与源净轮廓面积完全相同的量。源净面积恰好60的边界样例保留，54的样例被清除。缩放采用最近邻抽样，极细/倾斜轮廓仍需实际样本评估采样损失。

原始源帧在请求保留source_gray时于过滤前复制。TX补偿只作用于私有副本，不把补偿灰度写回预览/Gray8证据。

## 阈值与算法参数

固定PL比较`>=128`，而调光台为`>100`。新profile仅在TX中使用 `min(gray+27,255)`，因此对全部256个Gray8取值有严格等价关系。背景/ROI外的0映射为27，仍输出0。包校验和按实际TX重新计算。该方法不是动态硬件阈值寄存器，也不证明已重新综合TH100位流。

| 新profile参数 | 值 | 根据与限制 |
|---|---:|---|
| single_intact_area |64|0.8×最小单件实测80；作为候选拆分/合并保护基准 |
| split_min_hole_area |16|B-2观测合格孔17–34像素；相对0.06项封顶16，旧配置显式下限30仍保留 |
| min_split_child_area |48|0.6×最小单件80；板端真实分区58/59像素需保留；与CCL下限15分开 |
| split_recursive_item_area |80|最小物料面积下界；限定三件容量及过大子区递归，不代表M6螺母分类面积 |
| max_merge_area/max_split_area/max_area |540|3×最大单件150×1.2；工程暂定容差 |
| max_merged_span |48|最大单件16像素包围尺寸×3 |
| component_cluster_gap |0|关闭旧14像素邻近分组，降低不同物料被并成一件的风险 |
| max_missing/prediction_max_missing |3/3|暂无新速度数据，保留当前短时恢复预算；已补漏帧回归 |

原先没有正式的物料面积分类器。本次新增事件/API面积尺寸提示：small60–95、medium96–130、large131–188（输出前景像素）。95/130来自80/111/150间的中点，188为150×1.25向上取整；超区间unknown。`area_hints_provisional=true`，提示不参与物料身份判定，也不作为硬性拆分结果标签；粘连两小件与一大件可能面积重叠，不能仅靠面积区别。

## 验证

最终命令：`.venv/Scripts/python.exe -m pytest -q scripts/wo0016 scripts/test_live_lighting_tuner.py`。

阶段三checkpoint命令：`.venv/Scripts/python.exe -m pytest -q scripts/wo0016 scripts/test_live_lighting_tuner.py`，结果 **424 passed, 3 skipped**（91.63s），完整输出保存在 `data/results/lighting-calibration-20261025/pytest-checkpoint.log`。阶段四更新后命令：`.venv/Scripts/python.exe -m pytest -q scripts/wo0016 scripts/wo0015 scripts/test_live_lighting_tuner.py`，结果 **610 passed, 4 skipped**（167.05s）。

覆盖全部0..255阈值等价、源净面积边界/孔面积、ROI边界和y偏移、三物料快照保留、三件平移越线得到3、最小两件粘连拆分且面积守恒、漏一帧预测恢复得到1、V4L2命令/回读失败、相机格式协商→下发→warmup→回读顺序、旧配置回归和HTTP接口。开发期间调光台HTTP早期拒绝路径重复出现Windows 10053连接中止；定位为未消费正常请求体就关闭连接，已在路由/Origin/Content-Type早拒绝前有界预读1..4096字节、超时3秒，不解析/应用被拒请求。新增重复403/404/415回归并验证控件不变。另补新profile输出缓冲与原始相机帧重叠时必须拒绝的保护测试。

离线回放：`.venv/Scripts/python.exe data/results/lighting-calibration-20261025/replay.py`。
三个历史Gray8录像各1800帧，按旧full-width-mixed配置计数 **2/3/3**，断言通过。新profile在这些旧采集条件录像上的诊断计数为 **0/0/0**；没有把它们判为新配置准确率通过。这些存档只有320×240 Gray8，无法重建源分辨率净面积筛选；相机角度及光学条件也不同。详情和哈希存于同目录`verification.json`。

## 修改/新增文件清单

- 新增：`scripts/wo0016/lighting_profile.py`、`test_lighting_profile.py`、`test_lighting_integration.py`。
- 修改：`scripts/wo0016/run_live_monitor.py`、`run_stream_counter.py`、`live_calibration.py`、`stream_counter.py`、`split_touching.py`、`live_monitor_state.py`、`test_live_monitor.py`。
- 在原有未跟踪文件上同步默认值、说明、受影响断言及本次回归暴露的HTTP早拒绝响应修复：`scripts/live_lighting_tuner.py`、`live_lighting_tuner.md`、`test_live_lighting_tuner.py`。
- 新增证据：`data/calibration/lighting-20261025/`、`data/results/lighting-calibration-20261025/{replay.py,verification.json,pytest-checkpoint.log,mock-lighting.json}`。本报告及`docs/superpowers/plans/2026-10-25-lighting-profile.md`。

## 需要补充的样本

用户已确认单件样本映射关系。为把暂定面积提示变成稳健物料区间，每种建议至少30个独立实例，覆盖左/中/右位置、平放/旋转、正常亮度波动，并由独立方式确认真实类别。调整后的相机角度下还需要：每种单件完整越线、多件混合越线、两件/三件粘连（尤其两小件与一大件面积近似）、ROI上下边缘经过及短时遮挡的连续序列，附逐轮独立实物数量。相机下发和实际PL Binary8回传尚需另行板端验证；源净面积筛选增加PS运算，板端FPS未测。相机角度调整不构成mm/px物理尺度标定，现有速度估计比例未修改。


## 补充粘连快照与相机角度背景更正

用户明确说明：本次调整是为了适应真实进料和工业应用而改变摄像头角度。代码、测试、CLI、计划和本报告均统一命名为 **lighting-calibrated**；不依据安装几何作推断。生成的CLI帮助、mock结果和回放JSON均已重新运行生成。

补充原图与JSON：`lighting_20250509T174438_72fe589d453c4f0c9bdf3e3f265b485d.{png,json}`，保存于同一证据目录。原始JSON记录6个外轮廓组件，净面积分别为535、713、838、1057.5、1195、1642；这不是零件总数。人工查看原图可标出三行，每行左侧3件、右侧2件，共15件。该人工静态图标注不等同于实物动态计数验收。

按实际计数映射得到6个连通域，面积为156、204、240、300、339、453。修改前只得到12个检测：左上组三件拆成两件，左中组三件未拆开。左上孔面积为33/32/29，左中为21/21/20；额外小孔5和1不应作为零件孔。旧孔面积下限30不足以保留这些有效孔。新配置将该下限设为16，同时保持组件比例0.06、孔中心间距10、拆分子件面积下限60等约束，历史路径仍使用30。

修改后该图产生15个检测，每行左3右2，划分前后前景面积均为1692像素，未漏掉或重复分配前景。原独立三件快照仍保持3件。仅修复了证据明确支持的漏分门限，没有把此次快照当作物料分类定标：测地分配的子件面积可能不均，例如204/147/102，不能直接当成每个物料的真实独立面积，也不能据此放宽分类区间。

补充帧BG=26.5533、FG=218.0677；过曝比例0.584635%且status_overexp=OVEREXP，均匀性UNEVEN，all_pass=false；hardware_applied仍为null。以上原始光学状态完整保留，未调整告警门槛来使其通过。该帧timestamp仍为2025-05-09，原时间戳保持不变。

新增回归：真实粘连快照15件、三行分组3/2、面积守恒、孔门限非法值拒绝。离线静态结果不能证明输送速度下的身份连续性、实际板端FPS或物理计数准确率；仍需相机角度调整后的连续运动样本及独立实物数量。

## 阶段四板端准备记录（2026-10-25）

- 本地阶段三checkpoint：commit `b55c53c073166f1dfbf7b72d3d54c71d7ece617a`。
- 已将22个运行时代码文件同步到隔离目录 `/home/xilinx/lighting-calibrated-20261025-b55c53c0/`，没有覆盖原 WO-0015/WO-0016 目录或现有服务文件。板端 `SOURCE_SHA256SUMS.txt` SHA-256 为 `c30ff6a084c60af6dd4b3bd03f50059a7943512729c4215352331c3d32ffafb4`；远端 `sha256sum -c` 全部通过，本地源与远端22个文件逐一比对一致。摘要副本为 `data/results/lighting-calibration-20261025/deployment-SOURCE_SHA256SUMS.txt`。
- 在板端PYNQ Python环境执行新部署的`run_live_monitor.py --help`成功，确认入口及其导入链可加载；这不启动相机、Overlay或DMA。
- 只读检查发现 `/home/xilinx/lighting-tuner-20261025/live_lighting_tuner.py` 的实例占用 `/dev/video0` 和8080。按用户要求对确认的调光台进程正常发送TERM后，摄像头无占用，8080/8081均无监听。`CmaFree` 从停止前18,020 KiB升至38,056 KiB；最近复读38,124 KiB，满足32,768 KiB门槛。
- 相机为 `USB Camera: USB GS CAM`（`uvcvideo`）。在摄像头无进程占用时，实际写入并回读确认`auto_exposure=1`、`exposure_time_absolute=50`、`brightness=0`、`gamma=100`。驱动列出的控制中没有`gain`，查询`gain`返回`unknown control`，因此无法证明或锁定`gain=0`。没有启动实时监控或写入PL/DMA。
- 用户随后明确授权当前Boot ID。`scripts/wo0015/board_preflight.py`已将批准值更新为`bb382d01-7bb2-4922-9a85-af6a24ef4d10`；当前源manifest仍绑定`2cc2ab91-2e3f-450e-b9e7-361ec38e2815`，旧Stage 1回执绑定`d9f65950-d59b-4ea4-bee4-b28656cb176f`，均不复用。为本启动生成新manifest与Stage 1回执仍待执行。
- 用户确认本型号的UVC固件不暴露gain。`v4l2-ctl -d /dev/video0 --list-ctrls-menu`完整输出保存在`data/calibration/lighting-20261025/v4l2-controls-20261025.txt`（SHA-256 `7356e89dd717084668849b5eade446618990a87b6efe075f07e9204a565aac8f`）；结构化预检记录为`data/results/lighting-calibration-20261025/camera-controls-preflight.json`。输出包含brightness、gamma、auto_exposure、exposure_time_absolute及其菜单/范围；无gain。代码只豁免精确unknown-control错误，四项必需值保持严格门禁，并在运行JSON中记录用户指定的中文未验证说明。
- 当前只读复查的CmaFree为34,828 KiB，仍满足门槛；摄像头无占用，8080/8081无监听。旧活动Stage 1 consumed guard SHA-256为`03cc64f337f54bf26ce9f8ea4f2cae2b99793a8526ab131c4c740e854f82ee3d`，旧PASS receipt SHA-256为`8c79ff081bca5a40cf7bacaa5ab095bdfc0382e16e05604e45b448e6814fd745`；两者均标记Boot d9f65950。旧terminal-failure marker不存在。将按授权流程原样归档这两份文件，再尝试一次当前Boot的Stage 1。
- 实时监控、Overlay/DMA、空载误触发、FPS/PS、动态投料、物理真值计数及运行前后CMA变化仍未测。传送带未启动。

以上状态记录截至当前Boot的Stage 1执行之前。Stage 1及实时空带基准的后续结果见下节。

## 阶段四 Stage 1 与空带实时基准续记

本节记录当前启动下实际完成的板端步骤。任务日期按客户端日期 **2026-10-25** 记录；板端系统时间仍返回 **2025-05-09**，因此原始JSON保留板端时间戳，不能把它当作本次任务日期。

### CMA恢复与Stage 1

- 当前Boot ID保持为 `bb382d01-7bb2-4922-9a85-af6a24ef4d10`，没有重启。
- 按已授权流程执行 `sync` 与 `drop_caches=3` 后，`CmaFree` 从 **28,756 KiB** 回升至约 **109,868 KiB**。该内核没有 `/proc/sys/vm/compact_memory`，所以未能执行内存规整。
- 当前Boot manifest SHA-256：`cc6cf803175972d9841f493e85e3f5f8a1ee80ea9113c15f1e39780daf8f960b`。Stage 1 `OVERLAY_LOAD_PASS` 回执SHA-256：`53f992770558ff20a5ee5fad70cc1e5cd3e7a7bd44e38d4fbba31f19dd3d8195`；Checker/Packer签名分别为 `1464807732` / `1464807733`，位流哈希匹配manifest。结果见 `data/results/lighting-calibration-20261025/board-approval/stage1-overlay-only-result.json`。
- 完整V4L2控件清单保存在 `data/results/lighting-calibration-20261025/board-approval/v4l2-controls-stage1.txt`。暖机后 `auto_exposure=1`、`exposure_time_absolute=50`、`brightness=0`、`gamma=100` 均通过硬件回读；`gain`返回 `unknown control 'gain'`，按授权记为 **“UVC 驱动未暴露 gain 控件，未能通过 V4L2 验证”**，不宣称已锁定。

### 实时空带观测

- 阶段三性能优化已提交：`02dbb683`。粘连分割优先用OpenCV距离变换，若Voronoi子区不连通则回退向量化测地扩张。定向计数/光照集成测试64项通过，完整 `scripts/wo0016` 套件 **412 passed, 3 skipped**。本次代码及测试修改为 `scripts/wo0016/split_touching.py` 和 `scripts/wo0016/test_stream_counter.py`。
- 已验证的空带运行采用 `--mode board --counting-profile lighting-calibrated --lighting-area-filter output-roi`，由 `/usr/local/share/pynq-venv/bin/python` 从原PYNQ加载路径启动，服务地址 `http://192.168.2.99:8080/`。服务通过PL AXI DMA Binary8回传；7个样本覆盖约60秒，结果全通过，原始采样见 `data/results/lighting-calibration-20261025/board-approval/empty-belt-baseline-output-roi.json`。

| 指标 | 空带观测结果 |
|---|---:|
| 完成帧 FPS | 最低20.43，最高25.05，均值23.95 |
| 误检测 / 计数 | 每个样本均为0 / 0 |
| MM2S / S2MM状态 | 每个样本均为 `0x00001002` / `0x00001002` |
| `CmaFree` | 45,720–46,300 KiB；观测期间未触发32,768 KiB保护线 |
| 进程RSS | 143,268–144,004 KiB |
| 进程CPU | 单逻辑核百分比110.4%–163.1%，均值153.1% |
| 相机必需控件 | 四项回读通过；gain为未暴露豁免状态 |

- 空带预览截图：`data/results/lighting-calibration-20261025/board-approval/empty-belt-preview.jpg`。更早的一帧曾看到留在画面中的15个静止物料；该轮6.4 FPS观察不作为空带基准，现场清空后重新完成了上表的60秒观测，清空前截图保留为 `scene-check-raw.png`。
- `output-roi`模式跳过640×400源图 `RETR_CCOMP` 净面积60筛选，使用320×240输出中的ROI `[30,210)` 和15像素连通域下限；因此该次基准只验证空带实时链路与快速模式，不证明源图净面积路径或动态粘连场景达到同一FPS。默认源净面积模式在另一次空带启动中曾因运行时 `CmaFree` 低于32,768 KiB保护线而安全退出；未放宽保护线。恢复缓存后使用上方快速模式取得持续通过的基准。
- 空带基准完成后服务曾在8080运行，操作者随后报告第一轮动态过料；对应状态快照与预览留存在 `data/results/lighting-calibration-20261025/board-approval/dynamic-round-01-status.json` 和 `dynamic-round-01-preview.jpg`。后续轮次与最终归档见本节续记。

### 第一轮动态过料记录

- 操作者报告本轮物理基数为 **54 件**；实时看板总计数为 **46**。最后另行随手放入的 **2 件**不属于该测试，因此测试段系统计数为 **44 件**，相对54件物理基数少计 **10 件**（计数比44/54=81.5%）。此比例仅描述本轮数量结果，不作为跨场景准确率结论。
- 操作者未注明本轮物料组合及是否包含粘连件，故本记录不将其归类为单件连续、多件混合或粘连专项轮次。
- 板端状态快照显示 `total_count=46`、`processing_fps=21.456`、预览输出FPS `5.514`、`frames_processed=17069`、DMA MM2S/S2MM 均为 `0x00001002`、状态接口 `CmaFree=46516 KiB`；四项必需相机控件回读仍通过。相邻SSH资源采样（板端时间 `2025-05-09T20:05:54+00:00`）为CPU `154%`、RSS `146344 KiB`、`CmaFree=46412 KiB`。这些均是采样时值，不是整轮平均值。实时预览帧图显示看板计数046，文件为 `dynamic-round-01-preview.jpg`。
- 运行状态与操作者真值合并留档于 `dynamic-round-01-status.json`。板端时钟仍采用其系统时间；日期按客户端任务日期记录。

### 后续动态复测与显示诊断

- 操作者补充：有 **2 轮各50件**曾准确计数；之后的轮次无论快慢均出现漏记，但未提供这些后续轮次的物理总数和系统计数。另在独立粘连试验中，**3个M6螺母连在一起多次被计为1件**；**2个M6螺母粘连且连接轴平行于输送方向**时只有少数轮次成功。该补充记录于 `data/results/lighting-calibration-20261025/board-approval/dynamic-followup-operator-report.json`，不与前述54件/44件轮次合并计算。
- 代码显示当前分割器只会在识别出2或3个满足面积与间距条件的内部孔时执行孔中心分配；否则才尝试满足狭颈、投影平衡等条件的二分，条件不满足会保留单个连通域。因此三只M6螺母出现“合为1件”与当前保守拒分路径相符，但没有失败时的原始Gray8输入和PL Binary8掩膜，尚不能确定是孔在二值化后消失/连通、面积门限拒绝，还是其他形态门限导致。当前可用的历史静态粘连快照不是这组M6失败样本。
- 预览中重叠线条来自绘制设定：红色实线是计数轴 `x=160`；黄色虚线是迟滞边界 `x=158` 和 `x=162`（中心线两侧各2个输出像素）。本次将红色中心线显示为1像素，并在下方加3像素深色描边，令每侧留出1像素暗色间隔；`X=160` 标签改为较大白字和深色底框。迟滞边界仍显示真实的±2像素，不改变计数算法。JPEG质量70回归确认红线与黄色边界可辨，`test_mjpeg_preview.py` 12项通过；`pytest scripts/wo0016 -q` 最终414 passed、3 skipped。另修复HTTP接口对超限JSON体未做有界排空导致Windows回环连接重置的问题，拒绝请求体排空上限4096字节，定向用例和全套测试均通过。
- 最新退出日志确认 `run_live_monitor.py` 在板端时间 `2025-05-09 21:19:38.809+00:00` 因 `CmaFree is below 32768 KiB` 安全退出，属于CMA门槛失败，不是超时。原始144字节日志已保存为 `data/results/lighting-calibration-20261025/board-approval/live-monitor-cma-exit.log`；同期dmesg仅见zocl客户端PID 17066销毁，没有OOM/segfault证据。此后板端Boot ID仍为 `bb382d01-7bb2-4922-9a85-af6a24ef4d10`。本次只读复查先见 `CmaFree=32924 KiB`，随后再次检查为32,556 KiB；按用户先前授权执行 `sync` 和 `drop_caches=3` 后升至109,052 KiB，没有重启，`compact_memory`接口不存在。8080/8081无监听，监控和计数进程均未运行。
- 虽然本轮已获Stage B-2限帧原始采集授权，且清理缓存后CMA恢复至109,052 KiB，但当前Boot下缺少WO-0017 Stage 1专用回执、Stage B-2 READY guard和attempt permit；旧Boot记录不能复用。本次没有启动相机/DMA或采集，也没有生成灰度/掩膜样本。现有日志、历史静态快照不足以判定该组M6失败形态具体命中了哪个拒分条件。
- 对`split_touching.py`的源码审阅给出待验证假设：已标注单件M6螺母输出净面积约111 px；三件简单相加约333 px时，`ceil(0.06*area)`约为20 px，高于当前16 px孔面积底限，实际重叠后应按失败掩膜量孔确认。若三件粘连只保留两个合格孔，孔分配路径只生成两个子区；其中包含两件的长子区若长宽比超过1.8会使整次孔拆分拒绝，未超过时也只返回两区。投影路径的面积比约0.333低于0.35，但只有在候选狭颈条`runs != 1`时才会因`elongated_pair`条件被拒。
- 掩膜到手后的针对性方案：测量失败与成功M6样本的内部孔面积分布，将孔阈值改成由单件拓扑校准的绝对范围或有上限的面积比例；对“三件面积可容纳但只有两个孔”的组件，在拒绝1.8长宽比前尝试对过大的子区递归寻找第二个有效颈部/孔，并保持子件面积区间、连通性和前景并集守恒；仅当狭颈证据成立时再评估把投影子区比例扩至约0.25–0.75，不能单独放宽比例。当前没有应用算法阈值修改，需用本轮失败Gray8/PL Binary8样本写回归后再实施。
- 后续定位需要保存失败轮次的逐帧Gray8输入与对应PL Binary8原始掩膜，并独立确认物理数量；仅凭JPEG预览无法区分二值化、分割拒绝及轨迹漏记。成功50件的两轮与已报告的漏记轮次都保留为不同观察，不外推一般准确率。


## Stage B-2 真实掩膜诊断与拆分修复（2026-10-25）

- 单次采集 `b2-m6touch-220-20261025-01` 在第188帧提交时出现 `bounded capture queue overflow`，目标220帧，落盘186组成对Gray8/PL Binary8；186条逐帧记录及372个数组全部通过哈希、尺寸和Binary8数值校验，状态保留为INCOMPLETE。板端在线累计3件。完整归档SHA-256：`cf9a0365464445764807475e56e3bd97ae7b929d3690516bccf01b4f48b05920`。
- 活动物料位于帧48–62。板端Python 3.10.4 / OpenCV 4.5.4 / NumPy 1.21.5纯软件重放复现3件；第57帧三件组面积234 px，孔面积20/22/24 px，种子分区为64/58/112 px，58 px低于旧子区下限60导致整组拒分。本机OpenCV 5分区略有不同，旧配置重放可计5件，不能用本机重放替代板端图像库验证；源码和旧缓存字节码一致，排除旧缓存内容不匹配。
- `lighting-calibrated`拆分子区下限改为48；孔面积相对项封顶16，仍服从显式孔下限；狭颈投影面积比例扩为0.25–0.75，并继续要求狭颈及连通性。递归仅由该配置显式启用，容量下界80取自最小物料输出面积，用于限制三件容量和过大子区重试，不是M6螺母实物分类基准。名称映射仍为80=M6平垫圈、111=M6螺母、150=M8螺母。现有静态15件与旧粘连双件回归均保持通过。
- 正式B-2改用已有普通RAM数组保存采样期间帧与元数据，采样结束、DMA空闲后延迟写盘，避免采样期有界队列溢出；没有增加CMA帧缓冲。实时监控只在准入和四块DMA缓冲分配期间执行32768 KiB水位门禁，`arm_frame`复用缓冲时不再按CmaFree短时下降中止，DMA/Packer故障门禁保留。
- 修复代码已同步至隔离目录 `/home/xilinx/lighting-calibrated-20261025-fa688f04/` 并核对SHA-256，旧源码及字节码备份于 `board-approval/pre-b2-fix-20261025-4dd7002a/`。板端OpenCV 4.5.4对同15帧纯离线重放恢复5件；该结论不等同于新一轮动态验收。上轮全量测试421 passed、3 skipped；回归掩膜夹具 `scripts/wo0016/fixtures/lighting_b2_m6_touch_masks.npz`。
- 原始归档、拓扑测量、板端前后重放数据与Gray8/Binary8图集：`data/results/lighting-calibration-20261025/board-approval/stage1-currentboot/stageb2-currentboot-incomplete-capture/`。


## Stage B-2 attempt-03 正式动态采集（2026-10-25）

- 操作者重新摆放并确认物理真值：3只粘连M6螺母与2只沿输送方向平行粘连M6螺母，共5件。当前Boot `bb382d01-7bb2-4922-9a85-af6a24ef4d10`；新READY `ready-b2-m6touch-fix03.json` SHA-256 `6067f4c07d586ebe796f240cb4b8fbd8d25b314fbf6650160e7d2cbf8064bba8`；单次permit SHA-256 `61cebd86d59a4937abfa225a231cbad337a6dc89776f20f8777b88f23429208e`，已正常消耗。
- attempt-02因启动命令误用系统Python，在加载PYNQ前报 `ModuleNotFoundError`，0帧、未打开相机和启动DMA；保留失败结果和许可，不覆盖。attempt-03改用 `/usr/local/share/pynq-venv/bin/python3`，启动前无硬件访问的运行环境审计通过，PYNQ 3.1.1 / pydantic 1.9.1 / Python 3.10.4。
- 正式结果 `BOARD_STREAM_COMPLETE`，进程正常退出0，220/220帧，accepted=220、dropped=0、missing=0，在线5/5，5个计数事件位于帧79、79、80、81、81。deferred_memory延迟写盘完成，四块CMA缓冲已释放。保存的是320×240 FPGA输入Gray8和对应PL Binary8，完整640×400相机源帧不在本轮B-2归档范围。
- 独立核验440个NPY数组、220条记录、完整NPZ与嵌入数组哈希一致。逐帧Packer和MM2S/S2MM状态220/220通过；全部16,896,000像素的PL掩膜与Gray8 `>100`比较差异0，验证TX饱和+27与固定PL128门限在本轮等价。暖机后auto_exposure=1、exposure_time_absolute=50、brightness=0、gamma=100回读通过；gain为not_exposed/not_verified，按UVC例外记录。
- 有原始归档的正式采集吞吐12.24796 FPS，220帧流窗口17.96217秒；17个1秒资源样本CPU均值126.11%（21.65%–144.75%），RSS145,080–180,988 KiB。ROI CCL 29/220帧执行，调用时均值38.094 ms；计入全部帧CCL均值5.154 ms。此数值包含正式采集开销，持续实时模式需另测。
- 本轮CmaFree：准入63,408 KiB，流窗口采样14,328–32,076 KiB，缓冲释放即时8,104 KiB；正常关闭后按既有授权sync/drop_caches恢复108,204 KiB。运行期只复用已分配DMA缓冲，准入和四次分配水位仍保留。
- 本地证据：`data/results/lighting-calibration-20261025/board-approval/b2-attempt-03/`；归档SHA-256 `71a02fc58bedb5763451141bee472ab20a172aa2569e6d12787c9f66d68cdb9b`；原始NPZ SHA-256 `9ffd23907e9698932844c953f563ed2aa8ed6c6c93831cd96833de9c70c60363`；核验摘要`validation-summary.json`、资源摘要`resource-summary.json`。本轮证明指定5件形态通过一次完整动态采集，不外推多轮50件准确率。


## 正式采集后的实时监控服务（2026-10-25）

- B-2完成并释放设备后，使用正确PYNQ虚拟环境、`lighting-calibrated + output-roi`、无帧数上限和`--no-auth`启动 `run_live_monitor.py`。实时入口仍严格核对WO-0015 Overlay PASS回执；首次传入WO-0017专用回执被类型检查拒绝，未打开相机/DMA。随后以同Boot、同manifest已有的 `/var/lib/wo0015-s2mm/overlay-only-pass.json` 验证通过，SHA-256 `53f992770558ff20a5ee5fad70cc1e5cd3e7a7bd44e38d4fbba31f19dd3d8195`，未改写回执或降低门禁。
- 看板 `http://192.168.2.99:8080/`，后台Python PID30008。25.13秒6个状态样本全部running、非stale，完成帧由690递增到1312，采样时均无检测、计数0；处理FPS 24.12–25.15，均值24.81；CmaFree 42,612–42,792 KiB。相机必需四项回读均通过；MM2S/S2MM持续0x00001002。
- 单次ps资源快照CPU139%（进程生存期累计口径）、RSS146,152 KiB；后续CmaFree快照40,896 KiB。该CPU值不作为25秒区间平均。保留后台服务供操作者连续50件及粘连件复测，每轮需要独立清零和物理数量记录。
- 状态序列、摘要和实际MJPEG预览帧分别为 `b2-attempt-03/live-status-observations.json`、`live-runtime-summary.json`、`live-preview-after-b2-03.jpg`；`capture-contact-sheet.png`为正式采集原始Gray8/PL Binary8生成的帧图集，所标计数取自采集元数据。所有文件均位于上述board-approval证据目录。

- 收尾前再次只读核验：服务仍running且非stale，已处理3,388帧，计数0，最新滑动处理FPS23.25、CmaFree41,824 KiB，MM2S/S2MM均0x00001002；完整状态保存在 `b2-attempt-03/live-final-status.json`。
