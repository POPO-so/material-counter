# G4 部署准备与板端状态记录（2026-10-31）

## 本轮状态

本记录依据 `baseline-before-install.json`、`baseline-after-install.json`、候选环境配置和候选包身份整理。两份基线记录相同的板卡 boot ID：`8b380359-59a7-46f8-8f5a-f172fd1398dd`。

| 项目 | 部署前 | 部署后观察 |
|---|---|---|
| `material-counter-integrated.service` | `not-found` / `inactive` / `dead`；enable 状态 `not-found` | `loaded` / `inactive` / `dead`；`enabled` |
| Unit | `/etc/systemd/system/material-counter-integrated.service` 缺失 | 同路径存在；SHA-256 `30c22b6c869e95f13ed9692e15e854e3a4f67b0189217aa90d5f3604b2cb587a` |
| Environment | `/etc/material-counter/package.env` 缺失 | 同路径存在；SHA-256 `659364dc817a2d6edcda3e86af12e5de04b8e3835ad6de52f7d910a6e1cf9587` |
| `current` | `/opt/material-counter/current` 缺失 | 同路径为符号链接，指向 `/opt/material-counter/releases/standalone-20261030-g4-offline-package-candidate`；目标目录存在，release 索引 SHA-256 `96b4661b8e00f27de35be53236d108717aec80b23d9ff26ee63265e77afa3088` |

候选配置 `package.env.candidate` 指定 `PACKAGE_ROOT=/opt/material-counter/releases/standalone-20261030-g4-offline-package-candidate`，并绑定上述索引 SHA-256。候选包身份为 37 个运行模块，来源提交 `544a6d44af74708ae17ab88af6419093a45859cb`；索引 SHA-256 为 `96b4661b8e00f27de35be53236d108717aec80b23d9ff26ee63265e77afa3088`。已安装 release 上的 56 文件完整性 `PASS` 有持久原始证据 [release-package-integrity.json](release-package-integrity.json)：`files_checked=56`、`hardware_actions=0`、`scope=integrity_only_no_gate`、`status=PASS`。记录的命令为 `/usr/local/share/pynq-venv/bin/python3 -B -m scripts.standalone.delivery --root . --index-sha256 96b4661b8e00f27de35be53236d108717aec80b23d9ff26ee63265e77afa3088`。该命令仅核对包文件完整性，不是软件测试或整机测试。

## 当前会话只读复核

先前只读复核观察到桌面计数进程 PID `4225` 正在运行；G4 service 为 `LoadState=loaded`、`ActiveState=inactive`、`UnitFileState=enabled`。该进程状态是当时的会话观察，未使用未校准的板端时钟推定时间；不能据此推断后续板端是否空闲。

后续会话中曾因 Windows 终端显示中文乱码而短暂禁用 G4 service；逐码点读取包内 `package-source.json` 后确认 UTF-8 分支名正确，遂重新启用。恢复后只读 `systemctl show` 为 `loaded/inactive/enabled`，未启动服务。再次查询时 PID 4225 已不在进程列表，`board_entry.py`、`integrated_run` 和 `boot_integrated` 无匹配进程；这仍不证明 DMA/VDMA、相机和缓冲已安全释放，也不能据此直接启动 G4。当前包源码对应报告见[候选包目录](../standalone-20261030-g4-offline-package-candidate/source-correspondence-current-20261031.json)。

部署前/后 baseline 中的进程列表仅描述各自采集时点。其空列表不能代表当前无 owner，也不能证明目前硬件资源已释放或允许启动。部署前 `hardware_safe_to_switch=false` 仍是安全终态未证明的直接证据。

## 边界与后续

- 这次观察没有启动 G4 服务、重启板卡或执行运行测试。服务观察仍为 inactive/dead；板端安装状态变化本身不等于启动或 G4 验收。
- 两份 JSON 的 `captured_at_utc` 都落在 `2025-05-07`，与项目记录日期 `2026-10-31` 明显冲突。板卡时钟未校准；不得把 JSON 时间戳当作真实采集日期或可信时间顺序。
- 部署前基线明确为 `hardware_safe_to_switch=false`、state `unproven`。现有基线不能证明 DMA idle、VDMA halted、无错误或 owner/资源已释放；不得据此声称硬件安全终态已证明。
- 五次冷启动、连续 60 分钟有目标运行及故障恢复测试已由用户豁免，不安排代理执行。现场最终测试由用户执行；G3/G4 仍未放行。

基线原始记录见本目录的 `baseline-before-install.json` 与 `baseline-after-install.json`。候选配置及包身份见 `../../standalone-20261030-g4-offline-package-candidate/package.env.candidate` 和该候选目录的 `README.md`。

## 预装依赖快照

只读快照保存在同目录 [offline-runtime-snapshot.json](offline-runtime-snapshot.json)，schema `v1`。快照报告 Python distributions `status=complete`、261 条；dpkg `status=complete`、1630 条。快照同时明确 `complete_inventory=false`、`offline_rebuild_proven=false`，所以该采集仅是观察到的预装环境清单，不证明完整依赖闭包或可离线重建。采集发生时桌面计数进程 PID `4225` 正在运行，且未触碰该进程。记录时间戳受板卡时钟偏差影响，不作为可信采集时间。Manager 后续以本地只读 JSON 对比确认：`board-runtime.json` 所列 5 个可映射 Python distributions（`pynq`、`numpy`、`pillow`、`scipy`、`cffi`）版本均一致；8 个 `os_packages` 的 dpkg name/version 均一致；解释器路径 `/usr/local/share/pynq-venv/bin/python3` 与 glibc `2.35` 均一致。该对比不覆盖 `cv2` Python 模块版本，因此不能声称其版本已证明；完整传递依赖闭包仍未成立。
