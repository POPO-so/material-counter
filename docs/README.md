# 项目文档目录

[返回作品首页](../README.md)

本目录按比赛阅读和工程复现组织，描述 GitHub 默认分支 `LJ-01`。展示页引用现有记录，不改变范围、接口、路线或验收状态。

## 推荐阅读顺序

| 读者 | 阅读路线 |
| --- | --- |
| 比赛评审 | [作品介绍](competition/OVERVIEW.md) → [架构](competition/ARCHITECTURE.md) → [实物](competition/HARDWARE.md) → [演示](competition/DEMO.md) → [结果](competition/RESULTS.md) |
| 离线复现人员 | [开发环境](DEVELOPMENT.md) → [RLE 运行说明](../scripts/rle/README.md) |
| 板端操作人员 | [部署入口](DEPLOYMENT.md) → 对应版本的包、工件与运行记录 |
| 开发维护人员 | [PROJECT_INDEX](../PROJECT_INDEX.md) → 相关接口、ADR、工作包和实际源码 |

## 展示与操作文档

| 文档 | 内容 |
| --- | --- |
| [OVERVIEW](competition/OVERVIEW.md) | 场景、特色与应用边界 |
| [ARCHITECTURE](competition/ARCHITECTURE.md) | RLE 基线、JPEG 硬解候选、HDMI 和代码对应 |
| [HARDWARE](competition/HARDWARE.md) | 器件、安装、接口与原始实拍 |
| [DEMO](competition/DEMO.md) | 离线体验、实物演示和 HDMI 照片 |
| [RESULTS](competition/RESULTS.md) | 分版本指标、原始证据和提交待办 |
| [DEVELOPMENT](DEVELOPMENT.md) | Python、原生库、软件检查与 FPGA 构建入口 |
| [DEPLOYMENT](DEPLOYMENT.md) | V12 与 JPEG v8 的版本选择和部署边界 |
| [照片说明](assets/photos/README.md) | 六张原图的来源、用途和 SHA-256 |
| [CHANGELOG](../CHANGELOG.md) | 重要变更记录 |

## 工程依据

| 主题 | 原有入口 |
| --- | --- |
| 公开贡献规范 | [CONTRIBUTING](../CONTRIBUTING.md) |
| 范围、接口和候选路线 | [范围](00_scope.md) · [接口](02_interfaces.md) · [路线](07_routes.md) |
| 技术决策与工作要求 | [ADR](adr/) · [Work Orders](work-orders/) |
| RLE 数据通路与原尺寸接口 | [RLE 说明](../scripts/rle/README.md) · [RLE 设计](superpowers/specs/2026-10-28-rle-design.md) |
| 显示集成与现场部署记录 | [独立部署说明](../scripts/standalone/DEPLOYMENT.md) |
| JPEG 候选包与板测 | [JPEG PL 记录](../data/results/jpeg-pl-board-20261102/README.md) |
| 历史验证与原始输出 | [verification](verification/) · [data/results](../data/results/) |

同名文件在不同分支可能对应不同实现。核对结论时应一起查看源码版本、BIT/HWH、配置、测试输入及记录日期，不能仅凭目录名推断已部署或已验收。
