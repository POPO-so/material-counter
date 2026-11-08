# 比赛交付许可证核对

核对日期：2026-11-06

目标分支基线：LJ-01，7a5068bc421f1f3c5c1f9d1f5d3da697d22f8baf

## 项目 MIT 文件

- 交付包：material-counter-5438e0e1-complete-r2.rar，SHA-256 ae5204d6e32cf89b826585e73ee91c45bb840ec393b51e34b31b3471fe489938。
- 包内根文件：license，1,086 bytes，SHA-256 4946971bdeebec2fbd414199c872e47de232f482a26d2e4fb4020382fa79b1ee。
- 本分支根目录 LICENSE：1,086 bytes；与包内文件 SHA-256 相同，逐字节复制。
- 包内版权声明为 Copyright (c) 2026 Material Counter contributors。本分支的 [LICENSE_SCOPE.md](../../LICENSE_SCOPE.md) 说明 MIT 文本的预期适用范围。

## 第三方边界

交付包还含 third-party-notices.md，其中明确说明 MIT 不重新许可第三方材料、AMD/Xilinx 器件 IP、生成 IP 或工具输出。该文件逐字引用了包内 licenses/pynq/ 路径；该路径不存在于当前 Git 分支，因此未将该包级文件原样复制到仓库。改用与当前仓库目录相符的 [第三方通知索引](../../THIRD_PARTY_NOTICES.md) 和[逐项来源记录](../../docs/competition/THIRD_PARTY.md)。

本核对只确认许可证文件和记录的字节身份，不证明每个第三方文件、图片、字体、依赖或生成工件均已取得再分发许可。剩余事项继续列在第三方来源记录中。

## 2026-11-08 团队授权确认补记

团队维护者确认：

- `Material Counter contributors` 指代对项目原创源代码和交付脚本有授权权利的贡献者；相关代码作者同意以 MIT 许可授权。
- `scripts/rle/native/rle_components.cpp` 具备纳入项目 MIT 范围的权利依据。
- 当前仓库公开的第三方材料和照片按各自许可或授权处理；团队确认有权公开这些内容，且它们不因此纳入团队 MIT 授权。

本补记针对当前仓库内容，不代表 PYNQ、AMD/Vitis、依赖、字体或生成工件的来源和许可证清单已逐项补齐。
