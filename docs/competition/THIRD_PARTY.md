# 第三方来源与许可记录

[文档目录](../README.md) · [提交准备](SUBMISSION.md)

## 本页范围

本页整理当前仓库和部署记录中能定位的第三方来源与许可证文件。根目录 [LICENSE](../../LICENSE) 采用 MIT，适用范围限于团队拥有相应权利或获准再授权的项目源代码和交付脚本，详见 [LICENSE_SCOPE.md](../../LICENSE_SCOPE.md)。项目许可已补入仓库；这不表示第三方依赖、素材、生成工件或全部授权资料已经齐备。

## 已有记录与缺口

| 内容 | 用途与来源依据 | 现有许可证记录与待补资料 |
| --- | --- | --- |
| AMD/Xilinx Vitis Libraries JPEG decoder 子集 | [vendor 说明](../../fpga/experimental/jpeg_axis/vendor/README.md)和[来源清单](../../fpga/experimental/jpeg_axis/vendor/SOURCE_MANIFEST.json)固定源码提交 `1204c74cf86ff1e54e0933849328cb9ef7ae7fa6` 及逐文件哈希 | 本地附有 [Apache-2.0 文本](../../fpga/experimental/jpeg_axis/vendor/LICENSE.txt)；来源清单尚未记录上游仓库 URL |
| Vitis 外部头文件与工具链 | vendor 说明中的 `utils/x_hls_utils.h` 及 Vitis base headers，来自安装的 AMD Vitis 2026.1 | 未包含在上述 vendored Apache 子集中；具体授权记录与部署/再分发范围待维护者核对 |
| PYNQ 视频/显示相关源 | [video_sources.lock.json](../../scripts/standalone/video_sources.lock.json)记录上游仓库、v3.1、commit `19ed17d4fd4ea71f95a97d30f8ccd0d62b8dbaaa` 和逐文件哈希 | 锁文件没有逐文件许可字段，需按实际使用的 IP、源码及依赖补核许可证 |
| NumPy、Pillow、pytest、opencv-python-headless | [离线依赖文件](../../scripts/delivery/requirements.txt)记录版本 | 版本锁定不等于许可清单；需按实际分发包补充来源及许可文件 |
| 板端 PYNQ 视频扩展 | [部署说明](../../scripts/standalone/DEPLOYMENT.md)记录 `_video` 等扩展和目标 ABI 依赖 | 安装依赖的哈希核对与许可证核对分别记录，当前资料未给出完整本地许可路径 |
| Droid Sans、DejaVu 字体 | 同一部署说明列出的显示依赖 | 需按最终包实际包含的字体文件补充来源及许可文本 |
| RLE 原生模块 | [rle_components.cpp](../../scripts/rle/native/rle_components.cpp)作为项目构建源码使用 | 文件头没有独立来源/许可声明；项目整体授权确定后仍需维护者核对其来源归属 |
| 团队实物照片 | [来源与哈希清单](../assets/photos/README.md)记录用户提供的原图 | 原图已核对，作者归属与对外使用授权仍需团队确认 |

## 发布资料检查

保留现有第三方许可证、版权头和来源记录。正式提交前按实际分发文件确认来源及必要声明；不把“仓库有某份 Apache-2.0 文本”解释为全部代码均为 Apache-2.0，也不以新增项目 LICENSE 覆盖第三方自身的许可。

AMD 指南对开源协议有专门说明。MIT 文件与 R2 包内原文的哈希核对见[许可证核对记录](../verification/competition-license-audit-20261106.md)；未闭环的来源、再分发权和素材授权继续列入[仓库完整性状态](REPOSITORY_COMPLETENESS.md)。
