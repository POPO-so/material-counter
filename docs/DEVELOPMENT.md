# 开发环境与离线复现

[作品首页](../README.md) · [文档目录](README.md)

## 环境

软件复现需要 CPython、NumPy、OpenCV、pytest 和可用的 `g++`。RLE 原生模块由本机 C++ 源码编译：Windows 生成 DLL，Linux 生成 SO。不要复用其他架构或旧 ABI 的库。依赖版本参考[交付环境记录](../scripts/delivery/requirements.txt)，下面命令只用于主机离线环境，不直接改动开发板环境。

从仓库根目录执行，Windows PowerShell 示例：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r scripts/delivery/requirements.txt
g++ --version
```

板端的 Python/PYNQ、原生库和字体依赖必须按选定运行包另外核对。Vivado/XSim 仅在 RTL 仿真、综合和位流构建时需要。

## 离线运行

```powershell
python -B -m scripts.rle.run_counter --mode offline --frames 12 --output data/results/rle-demo/offline.json
```

输出文件应不存在。程序在输出目录构建原生库，生成合成帧和 RLE 数据包，完成像素重建比对及跟踪计数，预期报告为 `OFFLINE_VERIFIED`。不访问 PYNQ、相机或 DMA，结果不能作为 PL 吞吐和真实物料准确率。预期现象见[演示页](competition/DEMO.md)。

## 软件回归

在新的证据目录运行既有软件检查，原流程见 [RLE README](../scripts/rle/README.md)：

```powershell
New-Item -ItemType Directory data/results/rle-check
python -B -m pytest -p no:cacheprovider --basetemp data/results/rle-check/tmp scripts/rle scripts/gray8 -q
```

记录实际通过、失败和跳过数量。旧报告中的 118 passed 只对应历史版本，不能直接沿用到当前分支。此页不要求为文档改动执行全仓回归。

## FPGA 与显示构建

| 目标 | 构建与验证入口 |
| --- | --- |
| RLE 位流 | [RLE 流程](../scripts/rle/README.md)：RTL 黄金向量 → 综合后功能仿真 → 构建门禁 → [build.tcl](../scripts/rle/build.tcl) → 制品 manifest |
| HDMI 集成 | [build_g2_integration.tcl](../scripts/standalone/build_g2_integration.tcl) · [部署及制品记录](../scripts/standalone/DEPLOYMENT.md) |
| JPEG 联合候选 | [joint_build.tcl](../fpga/experimental/jpeg_integration/joint_build.tcl) · [候选实现与板测边界](../data/results/jpeg-pl-board-20261102/README.md) |

构建脚本包含板卡仓库、辅助脚本和工具配置要求，应先检查对应流程。已有 v8 候选位流不是当前合并源码的新构建结果。

## 开发协作

按 [AGENTS](../AGENTS.md) 和 [PROJECT_INDEX](../PROJECT_INDEX.md)定位最小必要上下文。范围、接口与路线分别遵循原文档及 ADR；新增实验不能仅凭文件存在就写成已集成或已验收。

发布时记录源码提交、工件与配置哈希、依赖、输入条件、命令和结果。板端版本选择见 [DEPLOYMENT](DEPLOYMENT.md)。
