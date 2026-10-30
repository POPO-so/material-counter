# 缩放黄金向量准备稿

> **状态：三份固定源输入及两份候选 expected arrays 已生成，缩放候选仍未执行。** [候选规则与期望样点 JSON](scaling-candidate-vectors.json) 使用 schema `material-counter.scaling-candidate-vectors.v1`、状态 `candidate_unexecuted`。常量灰度数组在 `INTER_AREA`/`INTER_LINEAR` 候选下均保持 73；二值数组按候选坐标映射生成。两者都不是 compositor 输出、实测比较或黄金证据。灰度 ramp 的 expected outputs 和 PNG 均未生成；没有运行比较，也没有 runner 源码哈希绑定。解析推导不是从 `cv2.resize` 运行得到的结果；`INTER_AREA` 与 `INTER_LINEAR` 均未冻结。

## 固定源数组

由同目录 [标准库生成器](generate_scaling_candidate_sources.py) 生成到 `scaling-inputs/`。三者均为 C row-major uint8、shape `[400,640]`、256000 bytes：

| 文件 | 定义 | SHA-256 |
|---|---|---|
| `constant-gray-73.raw` | 每个字节为 73 | `aa33e56924504144d0ea31c71ad74281064ba96c7572c2b303325091eea954af` |
| `gray-ramp-12.raw` | 每行 `min(12*x,255)`，`x=0..639` | `87d33b08d68a971d3d0c86ddf7ce671f5059a2b6af19599cde9c42cb81476354` |
| `binary-nearest-rect-points.raw` | 全零，指定 3×3 矩形和两个源点置 255（共 11 个非零像素） | `467bf9002e72f182da0c3a65dfb5454d6c04a15dc5647c02f373d4c0e6bc5263` |

这些固定源数组记录输入内容；其中常量灰度和二值 nearest 分别有候选 expected array。常量灰度输出对两种插值候选相同，不构成策略选择。二值输出由记录的坐标映射推导。两份数组均不表示候选执行、比较或验收完成。

## 候选参考输出

| 文件 | 候选规则 | 格式/尺寸 | SHA-256 |
|---|---|---|---|
| `scaling-expected-candidates/constant-gray-576x360.raw` | 常量保持；所有目标像素为 73 | C row-major uint8, `[360,576]`, 207360 bytes | `ace13c7b9c44a6d95fe0724be529c495d044513433d558e51bac5ab199653ea2` |
| `scaling-expected-candidates/binary-inter-nearest-576x360.raw` | `src_index=floor(10*dst_index/9)` | C row-major uint8, `[360,576]`, 207360 bytes | `35264c92c0c52e311d0c63a5aea1ed662a78ee4ba9347f5ee2e89ab3588e792b` |

## 已知边界与状态

- GrayComposer 输入为 `640×400 uint8`，输出画布为 `1280×720 uint8`。
- 两路图像各缩放为 `576×360`，水平起点 `x=32`；上路 `y=0`，下路 `y=360`。
- 灰度默认策略为 `INTER_AREA`；G1.5 的 `INTER_LINEAR` 仅是候选。二值图缩放固定使用 `INTER_NEAREST`。设计规格仍将缩放策略记为候选，因此本文不将候选写成已决定或已冻结。
- 几何顺序为先裁剪，再缩放坐标；裁剪后的坐标按 `floor(0.9*x + 0.5)` 换算。
- 首末行标记位于 `x=640..863`。右侧状态面板的像素会受字体及 Pillow 环境影响。
- 现有 `test_compositor.py` 用 `cv2.resize` 生成预期灰度及二值数组。因此这些数组与实现共享缩放实现来源，不能单独充当独立 oracle。
- G3 v2 缺少 runner 源码哈希；目标目录现有三份固定 raw 源输入，以及常量灰度与二值 nearest 两份候选 expected arrays；灰度 ramp expected outputs、PNG 和黄金图比较结果仍缺，因此无法把实际比较结果绑定到确切 runner 版本。

## 静态检查记录

静态查看发现 `test_render_preserves_fixed_geometry_scaling_padding_and_binary_values` 的输入曾将 `source binary[100,100]` 设为 `255`，但断言输出 `(x=9,y=18)` 与 `(x=11,y=20)`；按当前 0.9 缩放和最近邻采样，这些位置对应源区域 `x=[10,13), y=[20,23)`。已将夹具输入改为该 3×3 源区域，使输入与既有断言的坐标意图一致。此修改只依据源码和坐标关系静态判断，未运行测试；不据此声称测试通过，也未生成独立黄金向量。

## 独立 oracle 的可审阅规则建议

后续获准准备和验证时，参考结果应能脱离 compositor 及其 `cv2.resize` 预期生成路径，供审阅者从规则和输入独立复算。建议在生成前固定并记录：

1. 输入数组的尺寸、dtype、完整像素值，以及裁剪区域和目标坐标约定。
2. 缩放映射、采样点、边界处理、舍入和饱和规则；若灰度插值策略仍为候选，应分别标明候选及其来源，不以其中一种结果宣称策略已冻结。
3. 每个向量的人工可核算预期、逐点坐标和值，以及独立参考实现或推导过程；避免把生产 compositor 或其同源库调用作为唯一预期来源。
4. 比较范围、比较方式和失败定位信息。图像区域、标记位、状态面板宜优先分别比较，以免字体栅格差异掩盖图像缩放问题。
5. 输入、参考数组、生成工具及 runner 的版本或哈希记录，使后续结果可追溯。

## 候选向量定义（文本手算；仅输入源数组已生成）

- **常量灰度：** 输入为 `640×400 uint8`，所有像素均为 `73`。对 `INTER_AREA` 与 `INTER_LINEAR` 两种灰度插值候选，`576×360` 目标区域任意样点的解析预期均为 `73`；对应候选数组已生成并记录哈希。该向量只用于检查几何/放置以及常量保持，不用于区分或冻结两种插值策略。
- **灰度横向 ramp：** 输入尺寸为 `640×400 uint8`，每行相同，定义为 `I[y,x] = min(12*x, 255)`，其中 `x=0..639`。以下只列目标第一行输出列 `j=0..8` 的手算候选值：
  - `INTER_AREA`：将目标列 `j` 对应的源像素区间 `[10j/9, 10(j+1)/9)` 做面积加权均值，再舍入到 `uint8`；预期为 `[1, 14, 28, 41, 54, 67, 80, 94, 107]`。
  - `INTER_LINEAR`：在源采样坐标 `(20j+1)/18` 处作线性插值，再舍入到 `uint8`；预期为 `[1, 14, 27, 41, 54, 67, 81, 94, 107]`。

  以上均为按所列公式得到的解析手算值，不是从 `cv2.resize` 运行得到；候选结果在输出列 `j=2` 和 `j=6` 不同。并列记录两种候选不构成策略选择或冻结。
- **二值 nearest：** 输入为 `640×400 uint8` 全零图，将源矩形 `x=[10,13), y=[20,23)` 设为 `255`，另将边界点 `(0,0)` 与 `(638,398)` 设为 `255`。按 `src_index = floor(10*dst_index/9)` 反查，矩形预计映射到目标 `x=[9,12), y=[18,21)`；两个边界点预计分别映射到 `(0,0)` 与 `(575,359)`。该规则已用于生成 `scaling-expected-candidates/binary-inter-nearest-576x360.raw`（C row-major uint8，shape `[360,576]`，207360 bytes）；源 SHA-256 为 `467bf9002e72f182da0c3a65dfb5454d6c04a15dc5647c02f373d4c0e6bc5263`，候选输出 SHA-256 为 `35264c92c0c52e311d0c63a5aea1ed662a78ee4ba9347f5ee2e89ab3588e792b`。这是公式生成的候选期望数组，不是 compositor 实测结果，也未与其他输出比较。

## 画布比较与环境依赖

若完整比较 `1280×720` 画布并包含右侧状态面板，需固定字体文件及其哈希、Pillow 版本和相关栅格化依赖，否则文字区域的像素可能随环境变化。建议优先将比较拆为图像区域、首末行标记位和状态面板三部分；在字体与栅格化环境固定前，不把状态面板像素作为跨环境稳定的黄金值。

## 留待后续获准事项

灰度 ramp 的 candidate expected arrays、source/expected PNG、compositor 比较及 runner 源码哈希绑定列入后续黄金向量生成与验证工作。本轮生成了三份固定 raw 输入材料，以及常量灰度和二值 nearest 两份候选 expected arrays 并记录其 SHA；没有生成 ramp expected outputs 或 PNG，也没有运行比较。本准备稿不改变算法、接口或路线，也不宣告任何验证通过；状态仍为 `candidate_unexecuted`，G3 未放行。
