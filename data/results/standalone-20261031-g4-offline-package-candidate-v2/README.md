# G4 原生库准入候选包 v2（2026-10-31）

状态：候选包已在本地重建并部署到板端，尚未据此认定 G3/G4 放行。应用源码取自提交 `6cab07f244b8a0d0fb35d6eb4a2f9a293b4ee5ed`、分支 `feat/锁定PYNQ视频原生库`，37 个模块逐字节同步；[源码对应报告](source-correspondence-20261031.json)绑定本版索引和来源提交。

- [package](package/) 的 `deployment-index.json` SHA-256 为 `c4c73cbb7c8df5ff2adb0f9df8be52ff1919faf38516747b039ee6b7910f2e31`，覆盖 55 个包文件；构建时内建完整性核对检查 56 个文件并返回 `PASS`，范围仅是文件完整性，不是软件或板端运行测试。
- [外置环境候选](package.env.candidate)固定新版本 release 路径及相同索引摘要。旧候选包和旧板端 release 保留，不覆盖。
- `board-runtime.json` 升至 v2，记录已观察的 PYNQ 视频原生扩展 `lib/_video.cpython-310-arm-linux-gnueabihf.so` SHA-256 `015bfa5c37a98da364dc2014d37c08dbe7cfad7333f72448fee2c9b7bca91829`。该摘要与 G0 板端探针记录及 2026-10-31 当前会话只读 `sha256sum` 一致；启动准入在硬件初始化前定位、哈希并比对这个板端预装文件。此文件未打入候选包。
- 本版继续依赖既有板卡的 PYNQ/NumPy/OpenCV/Pillow 与系统库。绑定本版索引及 `board-runtime.json` 摘要的[板端离线运行时快照](offline-runtime-snapshot-20261031.json)已只读采集；它明确记录 `complete_inventory=false`、`offline_rebuild_proven=false`，且板卡时钟未校准。完整离线重建闭包、实际动态加载、自启动和物料计数显示均未证明。

板端安装在 `/opt/material-counter/releases/standalone-20261031-g4-offline-package-candidate-v2`，`/opt/material-counter/current` 和 `/etc/material-counter/package.env` 已切换到本版，旧 release 与旧 env 备份保留。[切换后只读快照](deployment-after-20261031.json)记录 service loaded/enabled/inactive/dead，unit SHA-256 `30c22b6c869e95f13ed9692e15e854e3a4f67b0189217aa90d5f3604b2cb587a`、env SHA-256 `ee1c2afa04d5447bebf5e742aa834499b2d95bf7de2bdeb46c298e32e3db2c96` 和 v2 索引摘要。[安装位置完整性记录](installed-integrity-20261031.json)为 56 文件 PASS、`hardware_actions=0`，范围仅为包文件完整性。快照采集脚本的 `purpose` 仍是固定的“pre-deployment observation”文字，不能覆盖其中实际的切换后路径与状态；板卡时间显示 2025-05-07，不可当作真实采集时间。`hardware_safety.state=unproven`，进程快照也不证明 DMA/VDMA、相机或 owner 已安全释放。

本次只执行源码同步、索引构建、源码对应核对、文件复制与配置切换；没有运行软件测试、仿真或 G4 业务，没有重启板卡。最终现场测试由用户执行；五次冷启动、连续 60 分钟运行及故障恢复测试已豁免。
