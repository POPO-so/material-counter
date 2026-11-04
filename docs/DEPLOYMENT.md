# 板端部署入口与版本选择

[作品首页](../README.md) · [文档目录](README.md) · [离线体验](competition/DEMO.md)

## 先选择版本

| 使用目的 | 入口 | 版本边界 |
| --- | --- | --- |
| 已有板端 HDMI 集成演示 | [standalone 部署记录](../scripts/standalone/DEPLOYMENT.md) | V12 等历史版本有各自源码、包索引及工件；记录中的 service、PID 和 Boot 是当轮观察，不是实时状态 |
| JPEG 硬解独立候选 | [v8 包、SHA-256 与命令](../data/results/jpeg-pl-board-20261102/README.md#v8-默认入口运行包) | 使用独立候选 BIT/HWH 和包内源码，不能混用 LJ-01 合并后的设计 |
| 从当前合并源码生成新硬件 | [JPEG 联合构建](../fpga/experimental/jpeg_integration/joint_build.tcl)及[证据边界](../data/results/jpeg-pl-board-20261102/README.md#合入-lj-01-后的证据边界) | 需重新综合、实现、生成成对 BIT/HWH 并上板复验 |
| RLE 基线的硬件自测或 PS 解码路径 | [RLE 运行说明](../scripts/rle/README.md) | 按匹配 manifest 与参数运行；当前相机入口默认 JPEG PL，PS 解码需显式 `--software-decode` |

本页整理已有入口，不发起部署，也不替代对应版本的准入和操作流程。比赛仓库的默认分支可以继续开发，最终演示应固定具体 commit、包和工件。

## 核对顺序

1. 核对板卡、相机和显示设备，并选择本轮获准使用的软件与硬件版本。
2. 对照对应包的清单逐项校验 BIT、HWH、manifest、PS 模块、原生库、字体和配置。
3. 检查运行环境、当前 Boot、相机占用及硬件 owner，按该版本流程读取所需 DMA/VDMA 状态。
4. 使用新的报告文件和获准采集目录，明确有限帧数或运行时长，以及所需真值记录。
5. 执行该版本说明中的命令，保留退出状态和证据，分别核对数据处理、计数和外接显示器。

## 两类原生库策略

standalone 正式包记录要求预编译且匹配架构/ABI 的原生库，不在启动时联网补依赖或现场编译。JPEG v8 独立候选则允许在未指定 `--native-lib` 或 `RLE_NATIVE_LIB` 时，先用包内 C++ 源构建源码哈希绑定的 SO，再接触硬件，因此需要板端已有 `g++`。两种策略属于不同入口，不能互相套用。

## 故障与回退

哈希、Boot、工件身份或运行环境不匹配时按对应门禁拒绝启动，保留失败原因。不确定 DMA/VDMA 所有权或进入 `FAIL_HOLD` 时保留相关缓冲与现场，遵循既有状态机和 owner 处置；不增加自动重试、重启或回退动作。

本次 GitHub 文档整理没有访问开发板，没有切换 service、加载位流或修改现有部署。
