# L1.5 路径收敛与 L3 拆分消融验收报告

日期：2026-10-26。基线为本地已验收的 `algorithm-slimming-scale-20261026.md` 工作树：481 passed、3 skipped，B2-03 220/220逐帧全等。不是仅以Git HEAD作为基线。

## 1. 验收结论

- **通过并接入：TX私有scratch收敛。** 每个运行实例拥有独立76,800B scratch；生产者串行复用，两个TX slot各自保有完整packet。真实B2-03全部220个230,432B packet逐字节一致，输入Gray8哈希不变。
- **通过并交付：仅离线CPU阈值helper、真实数据全量消融脚本。** 默认板端仍走PL；没有新增板端CPU切换开关。
- **不合入生产算法：raw、filled及shared-filled距离峰候选。** raw已误拆真实单颗M6螺母，且破坏B2逐帧状态；filled类在当前真实主域没有新增拆分收益，旧录像额外分区又缺乏逐实体真值，不能把“总数相同”当作“0误拆”。全部候选源码、合成样本与结果留在仓库外。
- **完整活动套件：502 passed、3 skipped（102.07s）。** 既有481项继续通过，既有测试源码与开工快照逐字节一致。B2-03仍为220/220完整语义全等，总数5，事件保持 `(79,5),(79,8),(79,9),(81,7),(81,10)`。
- **当前数据未发现max_area/max_bbox前置误拒证据。** 主域最大原始组件453px、最大bbox30×31，全部低于540/85；不放宽边界。

没有修改RTL/bitstream、没有连接板卡或启动相机/DMA，没有修改或删除旧测试、实板证据，没有commit、merge或push。pytest中的设备/HTTP验证为mock或本机loopback。既有未提交修改保留。

## 2. 交付与可追溯产物

### 工作树改动

本轮仅修改两个既有Python源文件：

- [lighting_profile.py](C:/fpga/material-counter/scripts/wo0016/lighting_profile.py:171)：新增 `make_packet_encoders()`。
- [run_stream_counter.py](C:/fpga/material-counter/scripts/wo0016/run_stream_counter.py:384)：`packet_encoders()`改为按run创建scratch工厂；live入口复用这个工厂选择函数。

`packet_encoder.py`的payload直写、校验和与封包契约已经可复用，因此保持不变。`stream_counter.py`、`split_touching.py`、`scale_parameters.py`本轮均未改动，没有把L3候选加入生产代码。

新增工具与测试：

| 文件 | 用途 |
|---|---|
| [offline_threshold.py](C:/fpga/material-counter/scripts/wo0016/offline_threshold.py) | 纯离线 `software_binary8(gray, threshold, output, roi)` |
| [benchmark_tx_scratch.py](C:/fpga/material-counter/scripts/wo0016/benchmark_tx_scratch.py) | 真实B2输入的旧/新 `encode_into` 字节对照与交替基准 |
| [ablate_split_datasets.py](C:/fpga/material-counter/scripts/wo0016/ablate_split_datasets.py) | 指定数据全量baseline、可选外部candidate、严格状态差分、前置过滤/CPU对照 |
| [test_tx_scratch.py](C:/fpga/material-counter/scripts/wo0016/test_tx_scratch.py) | packet契约、256个T、全部Gray强度、frame ID、非连续输入、别名拒绝与owner隔离 |
| [test_tx_factory_slots.py](C:/fpga/material-counter/scripts/wo0016/test_tx_factory_slots.py) | 同factory scratch指针复用、两slot互不改写、无每帧NumPy全帧申请 |
| [test_offline_threshold.py](C:/fpga/material-counter/scripts/wo0016/test_offline_threshold.py) | CPU mask契约、输入不变、strided输入、别名/参数拒绝 |
| [test_ablation_output_safety.py](C:/fpga/material-counter/scripts/wo0016/test_ablation_output_safety.py) | 输出必须位于仓库外且必须是新目录，阻止覆盖证据 |

仓库外总目录：[l15-l3](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/l15-l3)。开工源码为 `before/`，SHA清单为 `before-sources.json`。本轮结果与历史实板证据分开存储。

核心结果：

- [tx-b2-scratch-benchmark.json](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/l15-l3/tx-b2-scratch-benchmark.json)
- [cpu-threshold-benchmark.json](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/l15-l3/cpu-threshold-benchmark.json)
- [replay-after-tx.json](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/l15-l3/replay-after-tx.json)
- [Manager独立全量baseline摘要](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/l15-l3/results/manager-baseline-verified/summary.json)
- [raw全量消融](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/l15-l3/results/final-rerun-pure-raw-20261026/summary.json)
- [filled全量消融](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/l15-l3/results/final-rerun-pure-filled-20261026/summary.json)
- [shared-filled全量消融](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/l15-l3/results/final-rerun-shared-filled-20261026/summary.json)

早期synthetic TX基准、早期候选结果均保留为中间记录。本报告TX性能仅引用真实B2基准；静态单件采用 `corrected-v2`，不采用早期误把整图parts数用于单组件GT的字段。

## 3. TX路径收敛：算法与所有权

旧路径：原Gray → `prepare_gray.copy()` → ROI外Gray置0 → LUT高级索引生成第二份Gray → RGB payload写入调用方TX。

新运行路径：原Gray只读 → factory私有scratch先填 `lut[0]` → 仅active ROI执行 `cv2.LUT(..., dst=scratch[30:210])` → 原packet writer直写调用方TX。

**ROI外字节必须保留 `lut[0]`。** 旧路径先清零再LUT，所以T=100时TX外ROI应为27，不能改成0。这个区别虽然不改变PL Binary8，却会改变整包内容及校验和；新工厂按旧字节契约保留。

| 项目 | 旧运行编码 | 新运行编码 |
|---|---:|---:|
| 每帧可见的变换全帧临时数组 | 2 | 0 |
| 每帧上述临时数组字节数 | 153,600 | 0 |
| 每factory常驻私有scratch | 无 | 76,800B普通RAM |
| 调用方TX packet | 230,432B | 230,432B |
| DMA TX/RX slot个数 | 原四块 | 不变 |

这里统计Python层可见的全帧数组物化，不宣称OpenCV内部没有任何临时分配。非连续输入的内部处理成本另由库实现决定。

Factory只承诺单生产者串行调用；当前DoubleBufferStream符合该模型。scratch不传给DMA、不返回为packet视图、不与Gray或TX slot共享存储；封包完成后每个slot独立持有全部header/payload/trailer。输入与TX输出重叠会在写入前拒绝。两个run的factory互不共享scratch。

旧公开 `encode/encode_into/prepare_gray` 保持兼容，已有monkeypatch及快照测试没有改写。只有明确选择新factory的运行路径复用scratch。`formal_runtime_sha256()`原本已覆盖 `lighting_profile.py`、`packet_encoder.py` 和runner，新工厂因此自动受源码身份绑定；未改写任何既有许可或部署配置。

### 真实B2逐字节与计时

输入：B2-03 raw_gray8，220×240×320 uint8，frame IDs0..219。NPZ SHA256为 `9ffd23907e9698932844c953f563ed2aa8ed6c6c93831cd96833de9c70c60363`；Gray数组SHA为 `4835261abd5e402bcf72fa5f850fe84fd4c1c9e86f8f6c07294dbf7ceec5d39e`，运行前后相同。

220/220包逐字节assert通过，共50,695,040B；两侧packet聚合SHA均为 `3b5b459b069c4455dac988a85c8d7fca2530488d9c1e602ac033faeafe5a1cd6`。header、frame ID、RGB payload、checksum和trailer全部包含在比较内。

本机Python3.12.10 / NumPy2.5.3 / OpenCV5.0.0；一次全帧warmup，15轮AB/BA交替，旧/新均复用两块预分配普通RAM TX buffer，测 `encode_into`，不把packet申请混入一侧；不含PYNQ CMA与缓存同步开销。

| 每包耗时ms | p50 | p95 | mean |
|---|---:|---:|---:|
| 开工快照旧encode_into | 0.4752 | 3.3560 | 0.8996 |
| 新factory encode_into | 0.3382 | 2.6026 | 0.6704 |

本次mean下降约25.5%，但主机p95抖动明显。板端历史encode=19.037ms并未重测；不能把上述比例乘到ARM板端，也不能宣称整机FPS已提升。报告SHA为 `eb9cfd84663dbcd0f888d498719b858f58bb9a0166acad6ba92c58311ae3edbd`。

## 4. CPU阈值与source/output-roi对照

`software_binary8()`默认处理准备后的320×240 Gray8、T=100、ROI行[30,210)，外部行写背景；可传入预分配output。只在离线工具导入，不进入board runner，不改变PL>=128或原来的TX光度映射。

| 成对数据 | 帧数 | 像素数 | CPU/PL差异 | 完整语义结果 |
|---|---:|---:|---:|---|
| B2-03 | 220 | 16,896,000 | 0 | 220/220一致，5次事件 |
| incomplete | 186 | 14,284,800 | 0 | 186/186一致，当前配置离线计5 |

CPU helper本机3300次调用：p50=0.0058ms、p95=0.0068ms、mean=0.005873ms。比较对象是存档PL mask，**没有测到同期活DMA/PL延迟**；这不是CPU/PL端到端加速比。

source vs output-roi只用原始640×400 PNG，未从320×240录像重建“源图”。两种模式在这两张图上mask差异均0、检测数均为3与15：

| 静态图 | source p50/p95/mean ms | output-roi p50/p95/mean ms |
|---|---|---|
| 三孤立件172731 | 0.20190 / 0.24458 / 0.20836 | 0.02090 / 0.02376 / 0.02170 |
| 15件粘连174438 | 0.25885 / 0.32337 / 0.27152 | 0.02100 / 0.02242 / 0.02113 |

每模式50次。这只表明两张静态图未从source净面积筛选获得检测变化，不能据此删除生产source去噪或外推噪声/动态场景。

## 5. 真实数据全量baseline与消融

严格比较每帧 `asdict(FrameResult)`（仅去除roi_subtimings_ms）以及deepcopy后的last_associations，包含detections、raw_detections、轨迹ID和状态、events、拒绝统计。不是只比较最后总数。

### 5.1 当前主攻域：10月25日

使用已验收lighting配置 A_ref111、T100、child48；没有把incomplete的旧child60参数当作当前baseline。

| 数据 | 全量规模 | 当前baseline | raw | filled / shared-filled |
|---|---|---|---|---|
| B2-03成对原始包 | 220帧 | 5事件；逐帧拆分后检测累计89 | 仍计5，但新增2次fallback、检测累计92，150帧完整状态不同：淘汰 | 5事件；新增fallback0；0/220状态差异 |
| incomplete成对NPY | 186帧 | 当前48配置计5；检测累计62 | fallback0、状态差异0 | fallback0、状态差异0/186 |
| 真实失败mask夹具 | 15帧 | 逐帧分割后检测累计62 | 无变化 | fallback0、状态差异0/15 |
| 三孤立件PNG | 1张、3物体；source/output各一视图 | 3检测，分别80/111/150px | 111px M6螺母被拆2件：1/3物体误拆 | 3检测；3个控制件均未新增拆分 |
| 静态15件PNG | 1张、6个粘连组、共15可见件 | 15检测 | 无变化 | 15检测；fallback0 |

“检测累计”是跨帧观测数量，不是物料实物总数。fixture按逐帧分割控制使用，不以其短窗口虚构独立越线准确率。两种预处理视图是同一物体的重复观察，三孤立件负控分母是3个物体，不是6个独立样本。

incomplete旧child60配置在当前OpenCV5主机诊断同样回放为5。历史板端OpenCV4.5.4曾得到3及58px子区被60拒绝的记录保持不变；本次不把库差异重解释成新增实板通过，也不把已完成的48px修复算本轮L3收益。

B2/incomplete仍缺少完整逐实体时序GT；三孤立件为标定控制，非独立留出验证集。当前真实主域没有观察到filled类新增救回。

### 5.2 旧高度/旧光照域：仅只读守卫

三个NPZ均完整处理1800帧；使用历史Gray>128及full-width配置（min12、single95、merge260、split900、gap14、span24），**没有反改当前lighting默认值**。活动窗依据 [旧诊断README](C:/fpga/material-counter/data/results/wo0017-touching-diagnosis-20261024/README.md)。

| 录像 | 活动窗 | baseline窗内计数 | raw窗内计数 | filled / shared窗内计数 | filled新增fallback：窗内/窗外 | filled全段状态不同帧 |
|---|---|---:|---:|---:|---:|---:|
| pair_motion_03 | [0,901) | 2 | 2 | 2 | 2 / 21 | 924 |
| three_motion_02 | [0,100) | 3 | 3 | 3 | 1 / 0 | 1798 |
| three_motion_04 | [0,80) | 3 | 3 | 3 | 4 / 0 | 1799 |

三段窗外计数均0；窗外后期近镜/干扰不能并入目标准确率。filled实际改变分割的组件次数分别23、1、4；924/1798/1799是包含ID传播和历史轨迹状态的差异帧数，**不是误拆次数**。

候选旧域A_ref111并非旧光学实测标定，旧域候选输出只作诊断。历史回放配置的child下限12被保留，未将当前48强套旧域。因为缺乏逐实体单件标签，额外分区不能认证“0单件误拆”；2/3/3总数没有替代这个要求。故本报告不把旧录像称为已完成精确尺度标定或候选准确率验收。

## 6. L3候选机制、合成结果与取舍

所有代码只在外部 [candidate目录](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/l15-l3/candidate)。

触发门：现有孔种子/窄颈拆分最终仍返回完整单块，且父面积至少max(single_intact,2×min_child)=96。寻找plateau局部峰，执行突出度筛选、间距 `beta_d*sqrt(A_ref)`（本轮beta=1），再作前景种子分区。所有子区须>=48、单连通、互不重叠、前景并集与父一致，总件数仅2或3。

- raw：原始含孔前景的距离图。真实M6螺母环形前景有多个距离峰，产生误拆；已淘汰。
- filled：仅为种子定位填闭孔；实际分区仍使用原始前景，所以不把孔填充计入物料像素。纯版本使用向量化测地扩张。
- shared-filled：同类峰策略，通过外部adapter复用仓库 `_geodesic_hole_assignments` 的原生距离分配/断开时测地回退；没有复制一个新生产跟踪器。该版本突出度以A_ref111下0.75px为基准按sqrt比例缩放。

纯filled实验的突出度0.75px保持为当前参考域值；没有拿它在未经标定的旧域结果反调当前门限。纯/共享分区即使件数相同也不保证子mask相同，已有15例对照仅2例子mask逐项相同，因此仍以严格轨迹差分而非件数判等。

### 当前尺度合成

[current_scale_synthetic_evaluation.json](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/l15-l3/candidate/current_scale_synthetic_evaluation.json)使用单圆半径6、实际圆心距11–12px的2/3件无孔组件，六个方向；全部先经过当前540面积/85bbox和48子区门限，未绕过前置过滤。

| 控制 | baseline | filled | shared-filled |
|---|---:|---:|---:|
| 24个无孔2/3件正例，件数精确正确 | 12/24 | 22/24 | 22/24 |
| 新救回基线错例 | — | 10 | 10 |
| 24个单件圆/环/方/细长件负控误拆 | 0/24 | 0/24 | 0/24 |
| 无效分区 | 0 | 0 | 0 |

剩余两例是三圆45°：baseline已给出2片，按“baseline未拆才fallback”门控不触发；本轮没有为这两例增加特例或放开递归。另有当前面积223–231px的斜接双环：16px孔门限只留下一个合格孔，五个baseline未拆姿态被filled救回，均为2个连通子区、每子区95–132px。

这些是二值几何合成，不能作为真实竖立/半躺M8或垫圈重叠精度。早期700/1000px大圆簇只保留为几何函数诊断，不计入当前profile救回成绩。

当前尺度JSON SHA256为 `1d1c194e2b538c4488faf8a7df3213bde4932a0522e24c702b2931483e99e8fe`。真实单件采用 [corrected-v2](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/l15-l3/candidate/real_static_single_evaluation.corrected-v2.json)，SHA为 `10982f36bb5936dd3949f844b2265c999044c11d3936c80c79fe489804bf8587`。

### 是否合入

| 门槛 | raw | filled类 |
|---|---|---|
| 当前三真实单件0误拆 | 失败，M6螺母误拆 | 三件控制通过，泛化未知 |
| B2-03 220/220完整状态全等 | 失败，150帧不同 | 通过 |
| 当前真实失败样本出现新增救回 | 未证明 | 未观察到，0新增fallback |
| 旧录像0单件误拆可认证 | 未通过 | 无逐实体标签，不能认证 |
| 合入生产路径 | 否 | 否，继续仓库外候选 |

本轮不增加一个没有真实主域收益证明的生产开关，也不为“让旧录像通过”反调当前默认。下一次定型需要当前域、可独立确认身份的无孔/斜接/M8密集或垫圈重叠失败片段；候选接口和现成消融脚本可直接重跑，不必再次搭建实验链路。

## 7. M8前置过滤边界

严格在split之前统计原始CCL，而不是只看通过过滤的detections：

| 当前域数据 | 原始组件观测数 | 达min_area观测数 | 最大area | 最大宽/高 | 超540或85次数 |
|---|---:|---:|---:|---|---:|
| B2-03 | 425 | 49 | 245 | 22 / 23 | 0 |
| incomplete | 705 | 36 | 249 | 22 / 22 | 0 |
| fixture（派生，非独立实物） | 82 | 36 | 249 | 22 / 22 | 0 |
| 三孤立件静态 | 3 | 3 | 150 | 16 / 16 | 0 |
| 15件静态组 | 6 | 6 | 453 | 30 / 31 | 0 |

名义三颗150px M8面积约450，当前540上限有约20%工程容差；静态组最大453也在范围内。本轮没有动态M8独立真值，不能声称排除了所有M8工况的前置误拒。

旧three02有一个902px组件超过其历史max900；这是旧域诊断，不能拿来放宽当前540。当前尺度变化应通过已交付A_ref入口表达；不把旧高度更大的像素面积当当前默认门限缺陷。

因此本轮保留max_area540、bbox85及拒绝顺序。若以后真实已标注三件组确实在split前被拒，优先核验A_ref/ROI/光度域；确有必要时再试独立有界split-admission上限，并逐子区检查、限制最多3件及前景守恒，不能取消上限或用面积整除补数。

## 8. 回归、复现与本地Git归档建议

完整活动目录：

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider scripts/wo0016
```

结果502 passed、3 skipped。新增关键测试先验证缺失功能或输出保护失败，再实现；所有既有测试字节保持开工状态。一次全仓pytest尝试被历史归档副本的缺失导入/重复测试模块在collection阶段阻断；未为它改动旧归档。验收范围为上述活动目录，包含全部已验收481项及新增项。

只读全量baseline（必须指定仓库外、尚不存在的目录）：

```powershell
.\.venv\Scripts\python.exe -B scripts/wo0016/ablate_split_datasets.py `
  --output-dir <新的仓库外目录>
```

候选诊断另加 `--candidate-module <仓库外模块路径> --candidate-strategy filled`。旧高度活动窗/历史配置由脚本固定区分。脚本拒绝仓库内输出和任何已存在目录，保证不会覆盖数据或旧运行结果。

真实TX基准：

```powershell
.\.venv\Scripts\python.exe -B scripts/wo0016/benchmark_tx_scratch.py `
  --snapshot C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/l15-l3/before `
  --report <新的仓库外JSON路径>
```

本地归档建议：当前工作树同时包含此前阶段的未提交修改，按 `before→after` 本轮差分交互式分块归档。先独立保留此前已验收基线，再分别归档TX生产路径/测试、CPU与消融工具/测试/本报告；在本地提交说明中记录对应验收JSON和源码SHA。拒绝的L3模块与评估保留在仓库外candidate目录，用哈希清单归档，不放入生产运行源码或把它标成已验收算法。实板原始包继续保持原路径与哈希，不用新结果覆盖历史结果。没有自动暂存、提交或推送任何内容。

## 9. 实板稳态帧率与阶段计时验证（run-03）

2026-10-26 在已通过 Stage 1 的 PYNQ-Z2 上，以 Boot `9552f3cf-5fb6-4b97-b5c3-5640e708a598`、`lighting-calibrated + output-roi` 运行 90 秒。首个实板帧后暖机 15 秒，随后记录 1,977 个稳态帧；状态接口每 3 秒采样，得到 26 个稳态 FPS 滑窗样本。测试无 MJPEG 预览客户端，运行门槛保持 `CmaFree >= 32768 KiB`。

计时由运行目录内的临时观测包装器收集 `DoubleBufferStream._record_timing` 和 `FrameResult.roi_subtimings_ms`；包装器调用原方法，不修改仓库算法、门禁或运行入口。`encode` 为 TX packet 编码，`dma` 包含 `arm_frame` 准备与 MM2S/S2MM 等待；`roi_count` 是一次完整 `counter.update`，不与子计时相加。

| 指标 | mean | p50 | p95 | min–max |
|---|---:|---:|---:|---:|
| 稳态滑窗 FPS | 26.35 | — | — | 25.643–26.973 |
| 完成帧折算 FPS | 26.28 | — | — | — |
| TX `encode`（1,977 样本） | 14.403 ms | 13.427 ms | 20.099 ms | 12.061–37.075 ms |
| `dma`（1,977 样本） | 10.844 ms | 10.208 ms | 15.966 ms | 6.769–44.521 ms |
| `roi_count`（1,977 样本） | 24.426 ms | 26.494 ms | 35.653 ms | 6.427–70.307 ms |

TX `encode` 相对 10/25 B2-03 历史均值 19.037 ms 低 4.634 ms（24.34%）。ROI 子计时均值为 validate 5.915 ms、projection 2.526 ms、track 0.243 ms；1,977 个稳态帧中 `ccl_invocations=0`，所以本次没有 CCL 耗时样本，不能从空带结果推断有前景物料时的 CCL 时间。

| 资源指标 | 稳态结果 |
|---|---:|
| `CmaFree` | mean 47,306 KiB；范围 47,048–47,688 KiB |
| 监控进程 CPU | mean 165.2%（单核按 100% 计） |
| 监控进程 RSS | mean 143,495 KiB；范围 142,896–144,208 KiB |

正式窗口前执行授权的 `sync && echo 3 > /proc/sys/vm/drop_caches`：`CmaTotal/CmaFree` 从 `131072/78372 KiB` 恢复到 `131072/112736 KiB`，2 秒复核稳定。收尾后 `CmaFree=77520 KiB`，运行进程、`/dev/video0` 占用及 8080/8081 监听均释放，FPGA 管理器状态 `operating`、flags `0`。板端墙钟仍显示 2025-05-10；本节 FPS 和阶段耗时取自单调时钟窗口。

原始 run-03 日志位于板端 `/home/xilinx/pynq-z2-runtime-efeb4957/runs/fps-20261026-run-03/`，包含 `performance-report.json`、`timing-summary.json`、`live-samples.jsonl` 与 `console.log`。本节是空带稳态吞吐基准，不替代有物料动态准确率验收。
