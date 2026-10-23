# Optimization Benchmark Ledger

本账本只追加，不覆盖历史行。FPS 必须区分板端端到端、桌面回放与预览；`N/A` 表示该轮未测、未记录或不适用。Git 栏的“证据纳入”提交不是板端部署源码版本；实际部署来源以原始报告中的源码哈希为准。记录日期按采样/验证发生日，不按录入日。

WO-0017 的 12/12（100%）表示单次样本算法总数与操作员报告总数一致；原 `evidence.json` 的 `accuracy_percent=null`，未建立逐颗身份标签或多轮重复性证明。CMA 数值保留采样时点，不能据其变化单独断言泄漏或恢复。历史 pytest 保持原范围，最新回归另行记录。

使用顺序：固定源码提交上测试 → `record ... --dry-run` → `record ...` → 审查/提交账本 → `tag --tag NAME --dry-run` → 获授权后 `tag --tag NAME`。推送必须显式 `--push`；脚本不提交文件，不运行硬件，也不重写历史行。脚本使用主机 Python 3.11+；相对路径均从仓库根目录解析。历史补录用 `--date YYYY-MM-DD`；手动 pytest 计数必须同时显式给出 passed/failed/errors，并附日志。每条自动行末的隐藏 JSON 记录完整 SHA、证据哈希和验证范围，不能手工改写。仓库级完整规则见根 `AGENTS.md` / `CLAUDE.md`。

```powershell
python scripts/record_milestone.py record --stage "WO-xxxx offline" --environment Offline --summary "本轮收益及限制" --evidence "docs/verification/report.md" --pytest-junit "C:/audit/pytest.xml" --test-scope "python scripts；逐项说明 skips" --tested-commit HEAD --verification passed --dry-run
# 核实预览与证据后去掉 --dry-run；审查并提交账本，再预览本地标签：
python scripts/record_milestone.py tag --tag v1.1-fps-opt --dry-run
# 获得推送授权时，只推此标签：
# python scripts/record_milestone.py tag --tag v1.1-fps-opt --push --remote origin
```

| 日期 | 阶段/工单 (WO) | Git Commit / Tag | 验证环境 (Offline/PYNQ-Z2) | 吞吐帧率 (FPS) | 计数准确率 | CMA Free (KiB) | Pytest 通过数 | 关键改动与工程收益摘要 |
|---|---|---|---|---|---|---|---|---|
| 2026-10-21 | WO-0016 Stage B-2 / Attempt-5 Recovery-2 | 证据纳入 `1d325936`；部署源码以报告哈希为准 | PYNQ-Z2 | 22.132（端到端） | 4/5＝80%；准确率验收未通过 | 前118796；分配后65460；释放后64220 | N/A（pytest 为独立离线回归） | 165/165 帧，7.455 s，buffers_released=true；最后两通道 DMASR=0x1002；唯一有效样本，不证明重复性。证据：[验收收口](../verification/wo-0016-board-acceptance.md#stage-b-2-收口attempt-5-recovery-22026-10-21)、[原样本报告](../../data/results/wo0016-stageB2-attempt5-prep-20261021/attempt-5-recovery-2-sample-20261021/REPORT.md)。 |
| 2026-10-23 | WO-0017 physical-calibration-10 / 原始板端样本 | 证据纳入 `4c61479d`；部署源码以 evidence 哈希为准 | PYNQ-Z2 | 14.875227（端到端） | 12/12＝100%（仅总数一致；操作员报告，正式准确率未标注） | 前 70184；分配后 53956；释放后 9144（低于32768门槛） | N/A（板端样本；不混入后续桌面回归） | 300/300 帧，20.167759 s；物理标定仍 NOT_MEASURED。CMA 低水位保留为风险，不宣称健康门或15 FPS通过。证据：[原板端 JSON](../../data/results/wo0017-physical-calibration-20261023/physical-calibration-20261023-10/evidence.json)、[后续离线分析](../verification/wo0017-calibration10-offline-analysis-20261023.md)。 |
| 2026-10-24 | WO-0017 有效行裁剪 / 桌面离线回放 | `4c61479d` | Offline（Windows 11 / Python 3.12.10） | N/A（只测掩膜算法延迟） | 300帧逐帧等价；计数12＝原板记录12，不新增实物准确率证明 | N/A | 784 passed；4 skipped；5 subtests passed | 扫描像素76800→64000（−16.67%）；21轮中位耗时0.190025→0.179967 ms/帧（−5.29%）；未部署到板端。证据：[离线分析](../verification/wo0017-calibration10-offline-analysis-20261023.md)、[回放收据](../../data/results/wo0017-physical-calibration-10-offline-regression-20261023/replay-receipt.json)、[pytest](../../data/results/wo0017-physical-calibration-10-offline-regression-20261023/pytest.log)。 |
| 2026-10-24 | 优化前安全锁定 / 项目源码离线基线 | `0992d773` / `v0.9-baseline-verified` | Offline（Windows / Python 3.12.10） | N/A | N/A | N/A | 794 passed；6 skipped；5 subtests passed | 固定提交回归111.20 s，1495文件哈希不变；根收集5 errors，第三方技能收集2 errors，未宣称全仓全绿。证据为本地 `C:/Users/POP/Documents/Codex/repo-audits/material-counter-20261024/pytest-fixed.xml`，SHA-256 `b51e964b52832667b604854f7b7a529521bd61e3d2be6a810a0bf248c1ead5d2`；附注标签内也保留该哈希。日志未随Git同步。 |
