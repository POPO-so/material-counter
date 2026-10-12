# WO-0008 ROI 交付说明

本次只交付真实裁剪模块、测试、参考模型、运行入口和精简离线证据。
依赖 PR #1 的公共前端；当前独立 PR 的基线为 feat/pl-stream-skeleton，
差异只含 WO-0008。先合并 PR #1，再将本 PR 基线改为 main 后进行合并。
本次不会把 ROI 合并到 PR #1 分支。

窗口采用半开区间，默认全幅；区域内像素按原扫描顺序输出，SOF/EOL依据
裁剪后的帧行重建。空窗口无输出，复位后等待新SOF，不实现运行时CSR。
计数只在有效握手时推进；输出停顿保持像素与边带。

## 离线验收

24组有效测试、8组非法参数拒绝测试；主矩阵输入3687135拍，裁剪输出1540059
像素，Python数据和边带差异0。另有3组联合入口检查。Lint0条。
全幅综合28 LUT/29 FF，中心窗口34 LUT/29 FF，均0 DSP/BRAM。
没有完整I/O约束，未宣称时序收敛；未上板，未验证实际物料计数效果。

精简证据位于 data/results/roi-handoff-20261012。复制记录保存原路径、原字节
哈希；runtime.txt为原simulate.log副本。原始工程、HEX、完整stdout和失败运行
保留在本机。两个source-hashes记录对应历史验收文件，交付哈希另行列明。
交付源码保持已验收版本的字节哈希；提交前再次运行全部Python像素检查。
原工具报告的空白格式保留，不为消除Git格式提示而改写验收数据。

## 克隆后的复现

Python3.11+、Vivado2026.1/XSim、官方Ross MCP服务器为外部依赖。
公共前端依赖和本机配置步骤见 frontend-handoff.md；真实.codex/config.toml
不随仓库发布，使用已有的 ross-config.example.toml 模板配置各自机器。

从仓库根目录运行，先用冻结的spec重建H3：

```powershell
.\.venv\Scripts\python.exe -m pip install -r python\synth\requirements.txt
.\.venv\Scripts\python.exe -m python.synth.generate --spec python\synth\specs\h3_matrix.json --out data\synthetic\h3_full
.\.venv\Scripts\python.exe fpga\scripts\run_roi_crop_mcp.py --phase all
```

已有个人数据时在独立克隆中重建，避免覆盖。结果目录须使用新名称。
--case small-center --skip-invalid-parameters是快速联合检查，不能替代完整矩阵。
本模块给后续模块提供ROI局部坐标；原图偏移仍需在后续集成中明确映射，
此交付不修改特征记录接口、Route或摄像头方案。
