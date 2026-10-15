# PL 公共前端交付准备：流骨架、WO-0006、WO-0007

核查日期：2026-10-12。当前为 GitHub 交付准备，尚未提交、推送或创建 PR。

## 远端与交付范围

已用 git ls-remote 和 git fetch origin 核对团队仓库。
远端 main 为4398f64，远端 feat/pl-stream-skeleton 为7ae04db；两者均仅有
fpga/rtl/.gitkeep，没有这三项 RTL 或 python/refmodel。当前 HEAD 为01175c7，
包含一个尚未推送的项目导航提交；该提交的 AGENTS、PROJECT_INDEX、README
改动需要随分支审查，不能把它误认为本次新增的算法实现。
远端 main 后来的添加与回退提交净内容与7ae04db一致，但提交历史不同。

本次候选新增内容包括三项 RTL、三个 Testbench、两项 Ross 运行脚本、四项
Python 参考程序、WO-0006/0007 工单、本说明、通用配置模板、官方 Lint
解析器及许可证说明，以及 data/results/frontend-handoff-20261012 的精简证据。
不包括 docs/runtime_flow.md、个人 .codex/config.toml、其余安装的 Skill、
Vivado 工程与缓存、失败运行目录、完整 H3 图片集、HEX 向量和像素转储。
所有原始材料均保留在本机；排除交付不等于删除。

README 和 PROJECT_INDEX 的实现状态仍有历史描述；本交付不改写受保护文档。
实际实现及验证范围以本说明、对应源码和工单验收记录为准。

## 已验证能力与限制

| 项目 | 实际证据 |
|---|---|
| 流骨架 | 独立 XSim 回归8入/8出，稳定保持和反压断言通过 |
| RGB888 → Gray8 | 两种模式各230912像素；公式 floor((77R+150G+29B)/256)，差异0 |
| Gray8 → Binary8 | 阈值0/128/255 × 独立/串联 × 连续/随机反压，12组通过，共2774016像素，数据与边带差异0 |
| Gray 阶段资源 | 综合90 LUT、11 FF、0 DSP、0 BRAM |
| Binary 阶段资源 | 默认阈值128，综合5 LUT、4 FF、0 DSP、0 BRAM |
| 复位 | 同步低有效，清除 VALID 并丢弃寄存的像素；串联两级共同复位 |
| SOF/EOL | TUSER[0]为SOF，TLAST为EOL，逐像素保留 |

Gray Lint 两条警告已经解释并保留；Binary Lint0条，综合7条输入低位
未使用警告属于阈值128的优化。官方空 Lint 报告解析器误读说明文字的情况
保留了原始结果和规范化记录；未修改解析器、创建 waiver 或忽略断言。
glbl 参数覆盖提示及大数组波形显示提示不影响完整 scoreboard。

这些结果仅证明当前离线功能与综合。未证明完整时序收敛、FPGA 上板、
实际摄像头输入、DMA、CSR、跟踪计数或实际物料阈值效果。Route 尚未冻结。

## 队友克隆后的复现步骤

以下命令从仓库根目录运行。使用 Python3.11或更新版本，安装本机可用的
Vivado2026.1/XSim 和官方 Ross MCP server。服务器是外部工具，不随仓库复制。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r python\synth\requirements.txt
.\.venv\Scripts\python.exe -m pytest python\synth\tests -q
```

使用现有 H3 spec 重建数据，不修改规则。若该输出目录已有个人数据，先保留
它并在独立克隆中复现，不覆盖已有数据。生成器和三个固定输入帧的内容可
由交付 manifest 与精简证据核对。

```powershell
.\.venv\Scripts\python.exe -m python.synth.generate --spec python\synth\specs\h3_matrix.json --out data\synthetic\h3_full
```

本机配置由队友维护：在 .codex/config.toml 设置 mcp_servers.vivado-mcp 的
command 和 env.VIVADO_PATH；模板见 fpga/scripts/ross-config.example.toml。
复制模板后替换两个路径，使用已经安装的官方服务器。不要把实际配置提交。
Ross runner 会为每次运行创建独立 state 目录，不复用模板 args 中的状态路径。

```powershell
.\.venv\Scripts\python.exe fpga\scripts\run_gray2binary_mcp.py --phase all
```

上述入口重新生成 RGB/Binary 向量，验证骨架及两级串联，运行 Binary 独立
Lint 与综合。它不单独复验 Gray 的 OOC 综合；若需要完整 WO-0006 独立回归：

```powershell
.\.venv\Scripts\python.exe python\refmodel\rgb2gray_vectors.py --out data\results\rgb2gray-vectors-replay
.\.venv\Scripts\python.exe fpga\scripts\run_rgb2gray_mcp.py --phase verify --vectors data\results\rgb2gray-vectors-replay
```

向量和证据目录必须使用新名称。命令依赖附带的官方 Lint 解析器；不要只复制
axis_gray2binary.v 和新 TB 后就声称能复现完整回归。

## 供审查的本地产物

output/github-handoff-20261012 为本地已忽略目录：包含候选文件清单、SHA256、
相对 origin/main 的候选补丁、PR 描述草稿，以及隔离复现检查结果。
候选补丁使用独立临时 Git 索引生成，主工作区索引不变。
精简证据是实际已验证运行的原文件副本；新增 runtime.txt 为 simulate.log 的
内容副本，仅因仓库日志忽略规则改了扩展名。完整 MCP transcript 留在原运行
目录；精简调用摘录和调用数量能够指向同一运行。

后续提交时按清单逐项选择，不使用 git add .。建议按流骨架、WO-0006、
WO-0007/交付证据拆分逻辑提交，再针对团队 main 创建 PR；当前不执行这些操作。

原工具生成的 RPT 保留了末尾空格与空行，完整候选补丁的空白检查会提示这些
格式；源码和手写文档单独通过空白检查。证据副本保持原始字节，不为消除格式
提示而改写实际报告。历史源码 SHA256 对应当时文件的字节；Git 的 LF/CRLF
转换可能改变文本文件哈希，跨机器比较应核对 Git blob 或规范化文本。
