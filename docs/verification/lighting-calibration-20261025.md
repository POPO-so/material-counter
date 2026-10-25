# 2026-10-25 相机角度调整后的调光参数接入与离线验证

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
| 相机启动与旧只读gate | exposure50/brightness0；未要求gamma/gain | 新profile在格式协商后实际set并get核对manual exposure1、exposure50、brightness0、gamma100、gain0；warmup后再次只读核对 |

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
| split_min_hole_area |16|补充粘连图的有效小孔为20–33像素；旧配置保持30，比例0.06不变 |
| min_split_child_area |60|0.75×最小单件80；与允许小连通域/边缘碎片的15分开 |
| max_merge_area/max_split_area/max_area |540|3×最大单件150×1.2；工程暂定容差 |
| max_merged_span |48|最大单件16像素包围尺寸×3 |
| component_cluster_gap |0|关闭旧14像素邻近分组，降低不同物料被并成一件的风险 |
| max_missing/prediction_max_missing |3/3|暂无新速度数据，保留当前短时恢复预算；已补漏帧回归 |

原先没有正式的物料面积分类器。本次新增事件/API面积尺寸提示：small60–95、medium96–130、large131–188（输出前景像素）。95/130来自80/111/150间的中点，188为150×1.25向上取整；超区间unknown。`area_hints_provisional=true`，提示不参与物料身份判定，也不作为硬性拆分结果标签；粘连两小件与一大件可能面积重叠，不能仅靠面积区别。

## 验证

最终命令：`.venv/Scripts/python.exe -m pytest -q scripts/wo0016 scripts/test_live_lighting_tuner.py`。

阶段四本地checkpoint命令：`.venv/Scripts/python.exe -m pytest -q scripts/wo0016 scripts/test_live_lighting_tuner.py`，结果 **424 passed, 3 skipped**（91.63s），完整输出保存在 `data/results/lighting-calibration-20261025/pytest-checkpoint.log`。

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
- 当前Boot ID为`bb382d01-7bb2-4922-9a85-af6a24ef4d10`。仓库WO-0015门禁的`AUTHORIZED_BOOT_ID`仍为`f8b24aee-ed7e-430a-b3f3-d26056746bfd`；板端可用manifest及Stage 1回执也绑定此前Boot，不能用于当前启动。没有改写门禁、借用旧回执或启动实时链路。
- 因此本轮无相机完整参数锁定、DMA回传、FPS/PS负载、动态投料、物理真值计数或运行前后CMA对比数据；这些指标保持“未测”。传送带未启动，等操作员确认动态轮次安排后再投料。

继续上板前需要解决两项硬件门槛：为该UVC相机提供可读写的gain控制，或由用户确认相机固件已固定增益且允许将其作为明确例外；并为当前Boot ID重新完成审核的WO-0015 manifest与Stage 1回执。获得可用门禁后，再运行计数链路并收集动态轮次真值、FPS、PS及CMA证据。
