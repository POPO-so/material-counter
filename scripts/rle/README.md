# 640×400 原分辨率 RLE 计数链

PS 读取 MJPEG 并直接解码为 640×400 Gray8，不缩放；PL 完成 ROI、饱和灰度偏移、阈值和可关闭的 3×3 腐蚀／膨胀，返回行游程与投影；PS 用 C++ 原生模块进行 8 连通游程合并、候选筛选、局部粘连拆分、跟踪和越线计数。正常路径不重建整帧二值图。

这是独立候选运行入口，不自动替换桌面 GUI 的已验软件相机入口。正式 Route／接口冻结状态不因实现改变。完整 ABI 见 [设计](../../docs/superpowers/specs/2026-10-28-rle-design.md)。

联合 JPEG 硬解候选现由同一入口的 `--mode camera` 默认启用：PS 将 V4L2 协商为 640×400 MJPG，把压缩字节送入 PL；PL 完成 JPEG→Gray8→RLE/FRG，PS 跟踪计数后发布匹配帧的二值 mask、计数线、框和累计计数字形。运行器启动 1280×720 Gray8 HDMI VideoOut/VDMA，并用 PL 输出监视器逐帧核对 session/frame 标记。相机最多容忍 3 个连续不完整 MJPEG 包，统计后跳过；超限即失败。`--jpeg-pl` 保留为兼容别名；仅传 `--software-decode` 才会选择旧的 PS OpenCV 解码 + PL-RLE 路径。`--mode self-test` 仍保留原硬件自测分支。JPEG runner 若未给 `--native-lib` 且未设置 `RLE_NATIVE_LIB`，会在触碰板端硬件前用包内 C++ 源码编译源码哈希绑定的 `.so` 到报告目录；因此板端需要已有 RLE 原生库或 `g++`，构建失败会在 PL 初始化前退出。

2026-11-02 板端单次验收：匹配候选 BIT/HWH 完成 120/120 帧，11.31 fps；每帧 HDMI 像素流身份匹配，监视器 invalid/partial 为 0，最终 DMA/VDMA 均安全停止。报告见 [board-run.json](../../data/results/jpeg-pl-board-20261102/board-run.json)。该样本无检测前景，`total_count=0` 不代表计数准确率；尚无外接显示器肉眼观察。本模式仍要求匹配的 BIT/HWH 哈希、`ROUTED_NOT_BOARD_VERIFIED` 构建状态、boot ID 和 `--execute`。

2026-11-02 正样本补测：单张 4:2:2 JPEG 经板端 PL 检出一个 40×40 区域；随后五张受支持 JPEG 通过同一入口依次处理，目标越过 x=320 后板端计数从 0 增至 1。可重复 `--input-jpeg <文件>` 并令 `--frames` 与文件数相同，所有 JPEG 会在硬件访问前预检，总 fixture 上限 32 MiB。样本、哈希和报告见 [JPEG PL 板测证据](../../data/results/jpeg-pl-board-20261102/README.md)。HDMI monitor 核验帧标记与扫描，不检查 mask/框/计数字形像素；外接显示器和真实物料准确率仍未验收。新默认入口及匹配的 v8 运行包需由用户完成下一次板端实跑；包和执行命令见 [v8 runtime release](../../data/results/jpeg-pl-board-20261102/README.md#v8-默认入口运行包)。
## 文件

- `protocol.py`：Gray8 输入包、RLE 编码黄金模型及严格校验，正常解析采用 NumPy 向量运算。
- `native/rle_components.cpp`、`components.py`：原生游程 8 连通并查集、面积和一阶矩；可重建单个候选的局部掩膜。不在导入时自动编译，也不隐式回退到全帧像素 CCL。
- `counter.py`：原生区域统计接入现有聚类、粘连拆分与跟踪；默认面积和距离只是原尺寸标定起点。
- `transport.py`：先启动 S2MM，再启动 PL 和 MM2S；双重核对实际接收长度与帧身份，异常时保留不确定 DMA 所有权的缓冲区。
- `artifacts.py`：BIT/HWH／PS-DMA 拓扑／RTL 快照哈希校验。
- `run_counter.py`：离线、合成板测和相机运行入口；安全完成后解析出错时保存原始回传供诊断。
- `sim_vectors.py`、`run_sim.tcl`、`run_netlist_sim.tcl`：黄金向量、RTL 和综合后功能仿真。
- `benchmark_regions.py`：同机同 ROI 的 PS 区域路径对照；排除采集、输入打包和 DMA，每个样本从空跟踪器开始，不能当端到端帧率。

## 软件验证

使用具备 NumPy、OpenCV、pytest 的 CPython。选择新的证据目录，先建立 pytest basetemp 的父目录，不在源码目录生成缓存。Windows 示例：

```powershell
New-Item -ItemType Directory data/results/rle-check
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider --basetemp data/results/rle-check/tmp scripts/rle scripts/gray8 -q
.venv/Scripts/python.exe -B -m scripts.rle.run_counter --mode offline --frames 12 --output data/results/rle-check/offline.json
```

原生模块用本机 `g++` 编译为 Windows `.dll` 或 Linux `.so`。离线 FPS 包含软件 RLE 编码和黄金掩膜生成，不代表 PL 性能。

默认原生库文件为 `rle_components_v2.dll/.so`；它在 C++ 内先按最小面积筛选，避免为噪点区域创建 Python 对象，同时保留候选局部掩膜所需的游程标签。显式提供 `--native-lib` 时必须用当前 `native/rle_components.cpp` 编译，不复用旧 ABI 的库。

## 构建

1. 设置 `RLE_OUT` 为新的仿真目录；`python -B -m scripts.rle.sim_vectors <该目录>/vectors`，用 Vivado 2026.1 source `run_sim.tcl`，要求 `RLE_XSIM_ALL_PASS cases=8`。
2. 设置 `RLE_OUT` 为独立新目录，用同一 Vivado source `run_netlist_sim.tcl`，要求 `RLE_POST_SYNTHESIS_PASS`。该门禁防止只在 RTL 行为仿真中正确的 RAM 读口描述进入位流。
3. 选择新的 `data/results/gray8-rle-*` 构建目录，把上述两条实测成功标志分别保存为 `xsim-gate.txt`、`netlist-gate.txt`。必须使用同一次验证的源码，不能伪造标志或用旧验证覆盖修改后的代码。
4. 设置 `RLE_OUT` 为构建目录，`RLE_NATIVE_LOG` 为 Vivado batch `-log` 的确切路径，source `build.tcl`。该脚本复用已审计的 PS/DMA/HP 拓扑，验证时序、DRC 和方法学后导出 `export/rle.bit`、`export/rle.hwh`。
5. `python -B -m scripts.rle.artifacts --out <构建目录>` 生成 manifest，不覆盖已有文件。

## 板端运行

部署 `scripts/rle/` 及其引用的 `scripts/wo0015/`、`scripts/wo0016/`、`scripts/gray8/` Python 模块，还有匹配构建的 `export/` 和 `rtl/` 快照，并逐文件核对哈希。PYNQ 使用 `/usr/local/share/pynq-venv/bin/python3`；系统 Python 可能不含 PYNQ。

确认 boot ID、相机／DMA 无其他持有者后，使用 root 与 `XILINX_XRT=/usr`：

```text
sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -B -m scripts.rle.run_counter \
  --mode self-test --frames 12 --execute --load-overlay \
  --boot-id <实时boot-id> --manifest <构建目录>/export/manifest.json \
  --manifest-sha256 <已核验sha256> --output <新结果文件>.json
```

`--mode camera` 使用原分辨率相机；`--verify-pixels` 显式启用完整掩膜重建与黄金比对，性能报告需说明该开关。`--threshold` 使用 >= 比较，`--offset` 默认 0；`--morph 0/1/2` 分别关闭／腐蚀／膨胀，默认关闭。ROI 上下边界、计数线和最小面积可通过命令行配置；真实准确率需要物料真值。

`--counter-config <JSON>` 可传入原 `MaskCounter` 的区域筛选、X/Y 计数轴、跟踪、粘连拆分和聚类设置，并覆盖命令行计数默认值。坐标必须已经换算并标定到 640×400；不会自动套用旧 320×240 面积或距离。配置在板卡访问之前验证。硬件 ROI 与 PS 计数 ROI 自动保持一致；可以用 `count_axis: "x"`、`line_x: 320` 保留横向越线流程。

## 容量和故障

输入 256032 B。输出为 128 B 头尾、4 B/游程及 2080 B 投影；容量上限 67744 B，空帧为 2208 B。压缩率取决于物料和噪声，不承诺固定 2–8 KB。

超限后 PL 继续消费本帧并返回 overflow；PS 跳过计数更新，下一有效帧以帧号间隙处理丢帧。输入错误帧也无效。RLE 保留的是最终二值 ROI 的二维拓扑，不包含阈值前灰度或 ROI 外画面。

`FAIL_HOLD` 表示进程保留可能仍被 DMA 使用的 CMA 缓冲；必须先核实硬件状态再恢复，不能直接退出、自动重试或加载另一位流。
