# WO-0014 离线图像流水线验证记录

日期：2026-10-14。分支：`feat/pl-stream-skeleton`。本轮没有连接开发板、加载 Overlay 或启动 DMA；WO-0014 保持未提交。

后续基线验收：同日重新执行全部 15 项新链路 XSim 和 134 项 Python 回归，并重新进行 H3/定向输出黄金比对后，授权独立本地提交。提交时 100 MHz OOC 时序仍未闭合；原始未提交状态和下列初轮记录保留为历史证据。

## WO-0013 本地交付

独立本地提交：`47103338d2f59177af0c6570242aff953baf07b7`，标题 `feat: validate WO-0013 PS-to-PL DMA replay on PYNQ-Z2`。仅暂存 50 个明确属于 WO-0013 的源代码、脚本、已验证 BIT/HWH 和验收证据，约 4.8 MB。身份认证辅助程序、请求队列、Vivado 工程缓存、无关文件未纳入。没有 push 或合并 main。

板级结论来自此前保存的真实单次测试，不是本轮重新板测：76816 字节、19204 拍、TLAST 1、首字 FFFF00FF、末字 12340000、校验和 70B5017B；DMA DMASR=00001002、DMACR=00010003，Monitor status=00000002、error=0，最终 Ping/SSH/Linux 正常。详见 [WO-0013 验收记录](wo-0013-level2-accepted-20261014.md)。

## 实验实现

新链路：独立 RGB 图像包 → `axis_dma_rgb_adapter` → 既有 `axis_rgb2gray` → 既有 `axis_gray2binary` → `axis_binary_output_checker`。Morphology 未插入请求链路，但执行旧模块回归。生产 RTL 和四份受保护文档未修改，G1/Route/ADR 无变更。

WO-0013 的 Binary8+元数据测试包不能作为 RGB888 输入。新的 [实验包规范](../../fpga/experimental/wo0014/README.md) 使用独立 16 字节包头、RGB 字节载荷和 16 字节包尾。32 位流低字节 lane 在前，RGB 按 R/G/B 排列，跨 word 的像素由 byte reservoir 拼接。包头、包尾不进入图像变换；SOF 来自首像素，EOL 来自行末，输入 TLAST 仅结束包尾。所有输出 VALID/data/SOF/EOL 在 stalled 周期保持。

包格式错误产生独立错误事件并排空至 TLAST。错误帧可能已经输出前缀，必须整帧作废；当前不提供整帧回滚。`packet_done` 是输入包元数据校验事件，不能代替所有输出像素已握手。校验器统计 accepted 像素、SOF、EOL、Binary8 校验和；最终帧 underflow 由 TB 全量黄金比对检查，校验器自身没有帧结束输入，不能独立判断 underflow。

基线审查补充：checker 遇到 SOF 会开始新统计窗口并重置错误，不能独立捕获上一帧未满时提前出现的新 SOF。现有逐像素/边界 TB 黄金比对覆盖合法输入的完整帧；本实验 checker 不能单独作为完整的硬件帧间几何保护器。此限制不在时序优化中改变。

## 功能验证

- 完整 H3：21 个序列、全部 487 个 320×240 PNG，逐输入记录 SHA-256。原数据为 L 模式，本轮复制为 R=G=B；另以独立彩色小图验证通道顺序及灰度权重。
- 最终 XSim：487 个合法包，37401600 个像素，0 包错误；14857622 个输出反压周期。最后一帧 checker：76800 像素、SOF 1、EOL 240、checksum 0008B740。
- 每个输出以 16 位记录 Binary8、SOF、EOL，实际输出 74803200 字节。Python 按源 PNG 重新计算 `(77R+150G+29B)//256` 和 `gray>=threshold`，逐像素及边界比对。最终结果见 `data/results/wo0014-offline-20261014/reports/h3-final-pixel-comparison.json`。
- 彩色 5×3 图覆盖阈值 0/73/128/200/255，末载荷 TKEEP=1；2×1、1×1 图分别覆盖 TKEEP=3/7；完整 H3 覆盖 TKEEP=F。复位覆盖包头、载荷及 stalled 输出；断言逐时钟沿检查 stalled VALID/data/marker。
- 七种负例：错误包头、错误长度、错误 TKEEP、提前 TLAST、缺失 TLAST、错误校验和、额外载荷。各自检查期望错误码、无非法 packet_done，排空后新合法帧恢复且逐像素一致。
- 旧 XSim 回归：RGB 230912 像素、Binary 231168 像素、Morphology 3 帧/230400 像素、WO-0013 Monitor 76816 字节/19204 拍，全部自检及独立输出比对通过。
- Python 回归：134 passed，包含新增 7 项图像包和边界单元测试。原始输出见 `pytest-final.txt`。

## Lint、综合与边界

最终 Lint 共 2 条既有 RGB 警告：ASSIGN-1（加法位宽）及 ASSIGN-6（gray_sum 低位未使用），新实验模块 0 条，0 errors。对应 `reports/linter-final.csv`。生产 Gray RTL 未因警告而修改。

最终 OOC 综合成功，综合阶段 0 errors / 0 critical warnings / 0 warnings；资源 700 Slice LUT、377 FF、0 BRAM、0 DSP。汇总 Tcl 日志另包含两条 Lint 警告及一条 HD.CLK_SRC 时序估计警告。资源和时序以 `reports/utilization-final.rpt`、`reports/timing-ooc-final.rpt` 为准。初次综合发现 checker 的宽取模路径在 10 ns 下 WNS=-5.331 ns；只在实验 checker 中改为按握手推进的行内计数器，并重跑全部新链路仿真、独立 Python 比对、Lint 与综合。原始失败报告保留，不降低测试标准。

修复后的 10 ns OOC WNS=-1.668 ns、TNS=-29.856 ns，29 个失败 endpoint；最长路径由 packet_adapter/received_bytes 至 payload_sum。**100 MHz 时序仍未闭合**，属于剩余风险；本轮完成离线功能集成和资源综合，不将功能 PASS 宣称为时序 PASS。

OOC 顶层不是完整 Zynq 系统，没有 IO delay 或 HD.CLK_SRC 约束；综合时序不能作为布线后时序闭合、板卡 100 MHz 或吞吐性能证据。本轮不生成或加载实验 Bitstream。

最终 Python H3 比对 PASS：37401600 像素、0 mismatch，实际输出和黄金记录 SHA-256 均为 `3e2f35b2dbbb21b55408dcc62da11aa29217e304a9f976f7feac207aa384cced`。定向和负例恢复比对以 `reports/directed-final-comparison.json` 为最终版本。

320×240 RGB 包长度 230432 字节，超出 WO-0013 17 位 DMA 长度字段能力。未来硬件实验必须先审查至少 18 位长度能力或独立分包协议，以及错误帧作废/输出收集语义；本轮未调整 DMA IP 或正式接口。正式 Route/ADR、板测、PL CCL 和 Phase B 均未推进。

## 文件与复现

源文件清单及哈希：`data/results/wo0014-offline-20261014/changed-files.txt`、`source-hashes.json`。保护与生产 RTL 哈希：`protected-before.json`、`protected-after.json`。最终 Git 状态：`git-final.txt`，暂存区应为空。

离线生成：`.venv/Scripts/python.exe python/refmodel/experiments/wo0014/image_vectors.py --out data/results/wo0014-offline-20261014/vectors`。

在已有 Vivado Tcl 环境 `source scripts/wo0014/run_full.tcl` 运行完整新链路回归；`run_legacy.tcl` 依赖保存的旧向量；`run_synthesis.tcl` 在实验工程中执行 Lint 与 OOC 综合。Python 独立比对使用 `compare_output.py`（H3）及 `compare_run_set.py`（定向/负例恢复），随后执行 `scripts/wo0014/audit_delivery.py`。Tcl 脚本仅调用本地 XSim/综合，不包含硬件访问。
