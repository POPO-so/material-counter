# ARM 基准脚本兼容修复（OPT3A 入口）

| 项目 | 内容 |
|---|---|
| 状态 | 已修复并通过主机验证；旧版与 verified 入口统计一致；待补跑 OPT123 ARM。 |
| 基线 | `f6416bf9`（OPT3A），分支 `codex/m8-arm-bench-fix-20261102` |
| 范围 | 仅基准工具；未修改 `counter.py` / `split_touching.py` / 生产算法 |

## 1. 根因

旧基准脚本在 `main()` 中直接包装 `counter_mod.split_touching_component`：

```python
original = counter_mod.split_touching_component   # OPT3A 下不存在
```

OPT3A 的 counter 只导入 `split_touching_component_verified`，因此脚本在第一帧之前就
`AttributeError`。

## 2. 修复

新增 `install_split_meter(module, meter)`：

- 优先检测 `split_touching_component_verified`；存在则包装它，统计 `len(partition.pieces)`，返回 `"verified"`；
- 否则检测 `split_touching_component`，包装它，统计 `len(pieces)`，返回 `"legacy"`；
- **只包装一个入口**；两个入口同时存在时仍只包装 verified，legacy 保持原函数对象；
- 调用前先累加 `meter.calls`，与旧脚本在异常时的计数行为一致；
- 参数、返回对象、异常均原样透传。

脚本 SHA-256：`7828e09c413b55529c912de631dd9906ae8472c6da0544e4d9e6dab728f31252`。

## 3. 测试

`scripts/rle/experiments/test_arm_split_ab.py`（8 项，全部通过）：

- 旧版入口包装一次；verify 入口包装一次；
- 两个入口同时存在时只包装 verified，legacy 不被包装、单独调用不计数；
- 单块（无拆分/失败）计入 `pieces[1]`；4 块与 >4 块均封顶到 `pieces[4]`；
- 参数原样转发；
- 异常传播且 `calls` 已计数（与旧 wrapper 一致）；
- 无入口时抛 `RuntimeError`。

## 4. 主机差分

同一 10 帧样本、1200/2000、3 轮：

| 代码根 | meter 入口 | split_calls | split_children | max_det |
|---|---|---|---|---|
| OPT2（旧 counter） | `legacy` | 3 / 33 | 6 / 96 | 10 / 11 |
| OPT123（f6416bf9） | `verified` | 3 / 33 | 6 / 96 | 10 / 11 |

逐条 `(value, round, fid)` 比较 `det`、`raw`、`counted`、`split_calls`、`split_children`：
**0 处差异**，且与既有主机记录一致。

## 5. 板端 staging 变更

- 仅替换 `~/m8-split-arm-admission-opt123/arm_split_ab.py` 为上述 SHA 的版本；
- 不修改正式 release、package.env、systemd unit 或 FPGA 文件；
- 原版结果 `arm-ab-orig2.json` 继续作为对照，不重复运行原版。

## 6. 下一步

按 OPT123-ARM-BENCH-FIX 第二阶段：重跑 CPU-only 准入（脚本 SHA 更新后），只补跑一次 OPT123，
与 `arm-ab-orig2.json` 对比。