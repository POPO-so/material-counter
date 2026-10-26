# Level 1 等价瘦身与 Level 2 尺度解耦：本地验收

日期：2026-10-26。依据用户两阶段指令执行；阶段一通过后才修改阶段二。

本次未修改RTL/bitstream，未访问板卡、摄像头或DMA，未修改/删除已有测试或实板证据，未提交或push。保留开工时既有未提交更改。pytest中的HTTP/相机/DMA相关验证为本机mock或loopback，不是实板验收。

## 1. 独立阶段产物

基线与两个阶段的源码快照、独立差分和完整逐帧输出均保存在仓库外：

[algorithm-refactor目录](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/algorithm-refactor)

| 关卡 | 源码/差分 | 机器可读结果 | 验收 |
|---|---|---|---|
| 开工基线 | before/、before-sources.json | [baseline.json](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/algorithm-refactor/baseline.json) | 原始工作树，不使用HEAD代替用户既有修改 |
| Level 1 | level1-sources/、[level1.patch](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/algorithm-refactor/level1.patch) | [level1.json](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/algorithm-refactor/level1.json) | 425 passed / 3 skipped；220/220逐项全等 |
| Level 2最终 | level2-sources/、level2-sources.json、[level2.patch](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/algorithm-refactor/level2.patch) | [level2-verified.json](C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/algorithm-refactor/level2-verified.json) | 481 passed / 3 skipped；220/220逐项全等 |

中间的level2.json、level2-final.json保留为较早检查记录，最终以level2-verified.json为准；未挑选最快的一次替代最终结果。

## 2. 阶段一修改与证据

完成六项限定改动：

1. `DoubleBufferStream`：capture_gray已保存该帧时，preview使用同一slot快照，不再再次copyto。新增 `test_capture_and_preview_share_one_gray_snapshot_write` 在旧实现失败（2次写入），修改后通过（1次）。
2. `execute_board_stream`：启动时取得并验证frozen_counting_config，证据、counter、preview和逐帧metadata复用；callback只复制小型配置字典，不再构造临时MaskCounter。
3. 关联歧义：用Counter一次统计track/detection频数；edges生成、排序与tie顺序保持不变。
4. neck投影：以maximum.accumulate取得前/后缀峰值；cut、评分、浮点比较和候选顺序不变。
5. 孔门限：默认cap16时，min_hole_area>=16直接返回int；小于16保留旧公式。
6. 选中原图：`np.array(image, copy=True, order="C")`一次创建独立连续副本，避免非连续通道视图ascontiguousarray后再copy。

`test_split_touching_optimizations.py`使用旧公式/旧投影实现作oracle，对阈值边界、随机/粘连形状及真实夹具逐mask比较。已有预取/slot配对和原图快照测试同时通过。阶段一独立只读审阅未发现P1/P2问题。

## 3. 阶段二接口与默认兼容

新增 [scale_parameters.py](C:/fpga/material-counter/scripts/wo0016/scale_parameters.py)。

- 参考面积 `A_ref=111.0`，面积因子 `A_ref/111`，长度因子 `sqrt(A_ref/111)`。
- 所有整数门限使用正数half-up，即floor(x+0.5)，至少1；hysteresis取整后保持float类型。面积计算以输入数值的有理表示实现同一half-up规则，避免浮点半值向下误差。
- 面积门限层级严格要求 min_area < split_child < single_intact < recursive_split < max_area；过小参考面积导致取整层级坍缩时拒绝。
- 默认profile字典与B2-03记录逐字段相等。新增孔距离/孔cap参数在默认10/16时沿用API默认，不向旧字典添加无效字段。
- 源净轮廓面积60同样按面积因子缩放，但与输出CCL像素面积仍是不同度量，不宣称二者严格等价。

| 输出参数 | A_ref=75 | A_ref=111 | A_ref=160 |
|---|---:|---:|---:|
| min_area | 10 | 15 | 22 |
| min_split_child_area | 32 | 48 | 69 |
| single_intact_area | 43 | 64 | 92 |
| split_recursive_item_area | 54 | 80 | 115 |
| max_area/max_split_area/max_merge_area | 365 | 540 | 778 |
| split_min_hole_area | 11 | 16 | 23 |
| 孔面积相对项cap | 11 | 16 | 23 |
| split_min_hole_distance | 8 | 10 | 12 |
| max_merged_span | 39 | 48 | 58 |
| max_bbox_width/height | 70 | 85 | 102 |
| hysteresis | 2.0 | 2.0 | 2.0 |
| source_min_net_area | 41 | 60 | 86 |

迟滞的整数化是阶梯变化，例如A_ref=222时为3.0；75/160下仍为2.0符合统一half-up规则。

孔面积cap随尺度传播，避免A_ref=75、孔下限11时，旧的未缩放16px封顶又将有效门限抬回16。低于16的历史直接调用仍保留默认cap16和旧公式兼容性。

CLI增加 `--reference-area` 与 `--lighting-threshold`，适用于lighting-calibrated。明确输入的 `--max-bbox-width`、`--max-bbox-height`、`--hysteresis` 是输出像素覆盖值，优先于尺度推导；即使显式输入值恰好等于旧默认也能识别。未显式覆盖时由统一函数推导。

未改变计数线位置、ROI几何、形状比例先验、最多3件/递归深度、默认max_step48及横向10/backward4等运动基准，也未将真实物料身份分类加入算法。此阶段不是新相机的物理标定或Level 3拆分算法优化。

### 光度绑定

唯一默认T=100、固定PL门限128由scale_parameters集中定义。源图去噪、软件预览、运行metadata、预览标签及两种TX编码器使用同一运行阈值。编码器以partial绑定实例参数，不修改进程全局阈值。

映射为 `clip(gray + 128 - (T+1), 0, 255)`，使用有符号中间值；支持T=0..255，T>127时偏移为负。默认T=100逐字节保持旧+27映射。测试覆盖多个阈值边界下全部256个Gray8输入强度、原图不变、编码器一致性和缓存命中后的非法类型拒绝。

新增尺度源码已纳入formal_runtime_sha256身份绑定；旧源码/许可证据未被改写或复用。此项未进行板端部署。

### 可选Δt模式

新增 `--motion-timebase-s <名义帧周期秒数>`，默认None，默认关联行为保持原按帧模式。

开启后，MaskCounter要求正整数且严格递增的capture_monotonic_ns。DoubleBuffer按FrameInput/slot传递时间戳，即使未启用录制也保留配对。位移门限按 `elapsed_seconds / motion_timebase_s` 缩放；缺失时的速度预测使用独立px/s值。既有轨迹快照的forward_velocity仍是px/处理帧，避免改变Web遥测单位。

确认次数、max_missing等生命周期仍按观测帧计；不会只凭预测生成越线事件。规则时间间隔下与frame模式逐项一致，双倍时间间隔可关联相应双倍位移。该模式是受控、可关闭的候选，未据此宣称解决真实摄像头掉帧或通过实板精度验收；host-read时间仍非传感器曝光时间。

## 4. 220帧逐项对照

数据为真实B2-03 `frames_gray8_binary8.npz`，工具会核验manifest、frame-records和Binary8数组哈希。dataset完整路径和SHA记录在结果JSON中。所有版本均在相同本机环境运行：Python3.12.10、NumPy2.5.3、OpenCV5.0.0。

比较内容包含每帧FrameResult全部语义字段（detections、raw_detections、track IDs及轨迹状态、events、suppressed_events、投影/ROI/拒绝统计）和last_associations；只剔除实际耗时roi_subtimings_ms。最终使用当前CLI默认profile推导配置，并验证与录制配置完全相等。

两个阶段均为 **220/220帧100%逐项全等**，总计数5，事件：

`(79,5), (79,8), (79,9), (81,7), (81,10)`。

板端历史OpenCV4.5.4记录中track9为帧80，本机原/新均为帧79；本次严格对照本机修改前结果，未伪造跨库版本逐帧等价。

## 5. 计时

先warmup，再重复15次；图像加载、JSON序列化和磁盘输出不在计时段。整段回放含220次实际counter.update。下表单位ms。

| 版本 | 整段mean | 整段p50 | 整段p95 | 单帧mean | 单帧p50 | 单帧p95 |
|---|---:|---:|---:|---:|---:|---:|
| 修改前 | 36.586 | 36.073 | 39.181 | 0.16600 | 0.0647 | 1.06397 |
| Level 1 | 35.240 | 35.514 | 36.328 | 0.15989 | 0.0649 | 1.05852 |
| Level 2最终 | 35.563 | 33.851 | 41.099 | 0.16135 | 0.0690 | 0.97286 |

Level 1本次整段mean下降约3.7%；单帧p50基本不变。Level 2最终整段p95高于基线，独立运行存在调度波动，因此不声称统计显著加速或所有分位都改善。单帧分位使用3300个样本，包含大量空帧。这里不含相机、TX准备、DMA或JPEG，无法测出capture复制去重带来的整机收益，也不能推断板端FPS。

## 6. 回归与复现

阶段一：重点相关165 passed；完整425 passed、3 skipped（105.73s）；逐帧回放通过后才开始阶段二。

阶段二：新增尺度/光度/Δt及集成用例先确认失败后补实现；独立审阅指出的mock摘要、显式默认覆盖问题经回归修复；最终完整 **481 passed、3 skipped（89.84s）**。新增阶段二测试包括运行身份绑定、单调尺度与阈值边界、规则间隔等价、时间戳先验拒绝及双slot时间配对。

已有测试文件与开工快照的SHA256逐一核对一致；没有删除或编辑原测试。git diff --check通过，fpga路径无diff。

离线测试：

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider scripts/wo0016
```

离线逐帧对照（output必须是不存在的新文件）：

```powershell
.\.venv\Scripts\python.exe -B scripts/wo0016/verify_algorithm_replay.py `
  --source-dir current --use-profile-defaults `
  --reference C:/Users/POP/.codex/visualizations/2026/10/26/01a176ab-6e3a-7761-b3ca-7fb7c103dafe/algorithm-refactor/baseline.json `
  --output <新的JSON路径> --repeats 15
```

要核查独立阶段一，用 `--source-dir` 指向仓库外level1-sources目录；验证原始基线则指向before目录。所有产物保留，未覆盖旧验收结果。

仅验证CLI参数与合成链路的示例：

```powershell
.\.venv\Scripts\python.exe -B scripts/wo0016/run_stream_counter.py `
  --mode mock --counting-profile lighting-calibrated `
  --reference-area 75 --lighting-threshold 180
```

此命令不证明真实物料计数或新镜头效果。更换摄像头后仍需重新测量平放孤立M6螺母的参考面积和光度阈值，再以独立实物真值做动态验收。
