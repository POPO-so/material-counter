# ADR-0002 · Current Project Identity and Mechanical-Speed Baseline

| 项 | 内容 |
|---|---|
| **状态** | `Current working baseline` —— 实现与硬件证据仍待完成，三人评审后生效 |
| **日期** | 2026-09-23 |
| **决策者** | 项目负责人提出，三人团队评审 |
| **关联** | `README.md`、`MASTER_PLAN.md`、`docs/00_scope.md`、`docs/02_interfaces.md` |

## 决策

1. 当前正式作品名称为 **《高速小型物料智能视觉计数系统》**。GitHub 仓库名继续保持 `material-counter`。
2. 当前机械与输入基线为：

   ```text
   振动盘/供料器
     → 单条可调速主输送带
     → 全局快门摄像头 + 固定光源
     → PYNQ-Z2
   ```

3. 编码器用于获取主输送带的实际运行速度。逻辑上的 `encoder_speed` 由输入捕获、计数和标定链路产生；该链路当前是 **Planned**，尚未实现或分配具体引脚。
4. PL 的目标职责是图像采集、ROI、滤波、二值化、形态学和资源可控的区域/连通域特征提取；PS 的目标职责是跨帧关联、运动方向、速度预测、目标状态管理和越线计数。
5. Route A / Route B / Hybrid 和真实视频输入路线仍按既有 G1/C2/C3 证据门推进。目标链路中的“连通域分析”不等于现在承诺实现无资源边界的通用 union-find CCL。
6. **独立双传送带差速机构**不属于当前正式方案。审计未发现其已进入追踪代码、GT 或已验证测试；相关历史记录如有需要应保留并标记为 historical/superseded，不改写为当前事实。

## 实现状态

| 状态 | 内容 |
|---|---|
| **Implemented** | `python/synth` 确定性生成器、配置、ground truth 和现有回归测试。 |
| **Planned** | PL/PS 视觉链路、编码器输入与标定、真实摄像头接入、显示输出和 PYNQ 集成。 |
| **Exploratory** | 结合计数、在途量和编码器带速的送料停止时机预测与控制。 |

## 后果

- README 和活跃 scope/plan 文档应使用新正式名称和当前单输送带基线。
- `docs/02_interfaces.md` 只预留 `encoder_speed` 语义，不改动现有接口 B 特征记录布局，也不伪造已完成硬件接口。
- 已验证的 synthetic dataset、ground truth、测试和视频源可行性证据不因作品改名而重写。
- 历史 Controlled Errata、work-order 和 AI 协作记录保留原始语义；若与当前基线不同，以历史标记和本 ADR 的当前状态区分。
