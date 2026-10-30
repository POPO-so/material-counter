# G4 脱机包候选（未安装、未验收）

## 本次候选包刷新（2026-10-31）

- 37 个应用 Python 模块已按 `package/package-source.json` 清单，从本工作树基线 `544a6d44af74708ae17ab88af6419093a45859cb`（`feat/新增G4激活方案生成`）逐字节同步。来源提交记录已更新；包中未加入清单之外的主机部署工具。
- 索引构建命令：`python -B -m scripts.standalone.delivery --root data/results/standalone-20261030-g4-offline-package-candidate/package --build-index`。
- 构建结果：`INDEX_BUILT_CANDIDATE`，55 个包文件写入索引，内建完整性核对 56 个文件通过（`PASS`），索引 SHA-256 为 `96b4661b8e00f27de35be53236d108717aec80b23d9ff26ee63265e77afa3088`；父目录 `package.env.candidate` 已同步此 SHA。
- 本次仅刷新候选包源码并准备索引；未运行软件测试、仿真或板端命令，未安装到板端，G3/G4 仍未放行。
- 以下较早记录中的提交号、索引 SHA 和构建摘要是刷新前的历史快照，不代表当前包状态。
状态：G4 候选，未验收。当前候选包的 37 个应用 Python 模块来源记录为提交 `544a6d44af74708ae17ab88af6419093a45859cb`（`feat/新增G4激活方案生成`）。启动前运行环境准入只读核对覆盖已观察的 8 个 dpkg 包状态/版本；它不安装依赖，也不构成完整离线依赖闭包。本地 `delivery.py --build-index` 索引覆盖 55 个包文件，内建 `verify_package` 核对 56 个文件通过，当前索引 SHA-256 `96b4661b8e00f27de35be53236d108717aec80b23d9ff26ee63265e77afa3088` 与 `package.env.candidate` 一致。此项仅为候选包文件完整性核对，未运行软件测试、仿真或板端命令；G3/G4 均未放行，未证明离线依赖闭包或自启动。五次冷启动、60 分钟有目标运行及故障测试已由用户豁免，不作为代理执行项；最终现场测试由用户执行。部署/恢复文档目前仅为[审阅草稿](deployment-recovery-draft.md)，不构成可执行或已验证的 G4 回滚方案。

## 包身份

- 候选目录：[package](package/)
- 应用源码来源提交：`544a6d44af74708ae17ab88af6419093a45859cb`，分支 `feat/新增G4激活方案生成`；`package-source.json` 当前列出 37 个应用 Python 模块，包含绘图进程启动故障注入、真实相机首帧等待修复及观察到的 OS 包状态/版本准入。
- 上一版归档为 54 个包文件（不含 deployment index），索引 SHA-256 `3addc403eacdb17caf7fb699a712dbbb90b047ec1f7049c47df2b84bf1eccc2a`。源码刷新后，该摘要及其 55 项核对结果仅为历史记录，不代表当前候选目录完整。
- `python -B -m scripts.standalone.delivery --root <package> --build-index` 是已组装候选包的索引生成入口；本地步骤已完成；当前索引 SHA-256 为 `96b4661b8e00f27de35be53236d108717aec80b23d9ff26ee63265e77afa3088`，内建完整性结果为 55 个包文件建索引、56 个文件核对通过。

本地源码对应核对工具 `scripts/delivery/verify_standalone_source.py` 已提交，调用方式见 [部署说明](../../../scripts/standalone/DEPLOYMENT.md)。2026-10-31 曾对刷新前的旧包运行该工具；其报告与本次刷新后候选包无关，当前 `package-source.json` 的基线记录不能单独充当本次源码对应核验报告。

## 内容

- 与 G2 manifest 配套的 BIT、HWH、manifest 和 ELF32 ARM 原生库；BIT SHA-256 `79609f9a60c3e727b5f2f54a8d639a464a098715fb5eb28d294646a9735948e0`。
- 37 个运行入口及传递导入所需的应用 Python 模块；来源提交和模块清单写入 `package/package-source.json`。`delivery.py` 现在要求包源码清单与索引中的应用 `.py` 文件集合一致。
- manifest 锁定的 7 个 PYNQ 运行源码和 2 个字体。从板卡只读复制后，本地计算的 13 项依赖/制品 SHA-256 全部与来源 manifest 或 G2 记录一致；逐项记录见 [dependency-readback.json](dependency-readback.json)。
- `boot-config.json` 使用 `continuous`、固定相机 by-id 路径；候选 systemd 单元和占位环境模板位于 package/deployment/。同目录外的 [package.env.candidate](package.env.candidate) 填入拟安装 release 路径及当前索引 SHA-256 `96b4661b8e00f27de35be53236d108717aec80b23d9ff26ee63265e77afa3088`；文件不属于包索引，也没有安装到板端。
- [board-runtime.json](package/board-runtime.json) 记录板端目标平台基线和既有运行时观测。本次选定版本的只读查询明细见 [runtime-package-observation.json](runtime-package-observation.json)；该文件位于 `package/` 外，不属于 deployment index，不改变候选包索引或其哈希。
- `boot_integrated.py` 在一次性硬件初始化前调用 `runtime_environment.py`，对照观察记录检查 Python、操作系统/内核、架构、libc、distribution 版本，以及已观察 8 个 dpkg 包的已安装状态和版本。此门只比对已观察环境，不安装依赖，也不形成完整依赖锁、wheelhouse 或干净环境重建能力。

## 离线依赖静态清点

- 板端目标平台以 [board-runtime.json](package/board-runtime.json) 记载的 Python/Linux/ARM/glibc 为准。本次 JSON 是选定版本的只读观察：筛选到的发行包均标为“已安装版本观察”；另记录所选 distribution metadata 的 `Requires` 与 `Required-by` 声明边，可辅助后续离线清单整理。声明边仅反映 pip metadata，未确认应用完整依赖闭包或系统库闭包，也不是 lock 或重建证明。`dpkg-query -W` 观察到 `python3-opencv:armhf` 及三个 OpenCV 库均为 `4.5.4+dfsg-9ubuntu4`，另记录 cffi、numpy、Pillow、SciPy 的 Debian/Ubuntu 包版本。OpenCV 来自系统 Python 包，而不是本次筛选命中的 pip distribution；筛选输出没有 `opencv` 开头的 pip distribution。
- 静态导入证据显示，集成运行器直接导入 `cv2` 和 `numpy`，compositor 使用 `PIL`，硬件入口延迟导入 PYNQ。SciPy 和 cffi 是板端“已安装版本观察”，但现有静态导入证据没有证明它们是集成运行器的直接依赖。该观察不是完整清单、direct/transitive 依赖锁或可重建证明。
- 已核验的 13 项仅包括 7 个 PYNQ 源文件、2 个字体，以及 BIT、HWH、manifest 和 native library；哈希与来源记录见上文及 [dependency-readback.json](dependency-readback.json)。以下为**静态缺口分析**：当前没有 wheelhouse、完整 Python 依赖锁、完整 PYNQ 运行时离线制品、OS 包/镜像基线，亦没有 native library 的重建工具链资料。
- 后续依赖锁清单可记录 direct/transitive、distribution 名称/版本、Python/ABI/平台标签、SHA-256/来源，以及 OS 包/版本/离线制品来源；未锁定的信息留空待证，不填未经核实的猜测。

## 启动入口静态核对（未安装）

- 候选 systemd 单元只声明 After=local-fs.target、WantedBy=multi-user.target，未依赖 network-online.target、network.target 或远端服务。
- boot-config.json 固定相机 by-id 和 continuous 模式。boot_integrated.py 接收 package root 与 index SHA-256，核对 deployment index、manifest 和运行依赖哈希，读取本机 boot ID 后执行一次性初始化，再转入集成运行器。
- package.env.candidate 当前索引哈希为 `96b4661b8e00f27de35be53236d108717aec80b23d9ff26ee63265e77afa3088`。文件仅供后续部署 review；目录未部署，systemd 未注册，入口未启动。
- 以上是对候选配置和代码的静态核对，不证明目标板离线启动、相机打开、位流准入或 G4 门禁通过。
## 范围和未完成项

- 第三方 Python 包由板端既有 PYNQ 运行环境提供，没有打包 wheelhouse，也没有安装或升级这些依赖；运行环境预检仅防止版本/平台与已观察基线不一致，不能证明完整依赖闭包或干净环境可离线重建。
- `package/deployment/package.env.example` 保留安装时需填写的占位值。候选包尚未复制到板卡、注册 systemd、配置 `/etc/material-counter/package.env` 或启动。
- G3 未放行，因此该包没有进入整机验收。本地仅执行索引生成与文件完整性核对；未运行软件测试、仿真或板端命令。五次冷启动、60 分钟有目标运行及故障测试已由用户豁免，不要求代理执行；最终现场测试由用户负责。
- Route、视频源和 ADR-0005 接口状态不变；物料准确率未验收。

## 代码阶段记录（2026-10-30；包来源与索引已由 2026-10-31 刷新）

- 当前候选包 37 模块来源记录为 `544a6d44af74708ae17ab88af6419093a45859cb` / `feat/新增G4激活方案生成`。刷新后本地 `delivery.py --build-index` 结果为 55 个包文件入索引、56 个文件内建核对通过；当前 SHA-256 `96b4661b8e00f27de35be53236d108717aec80b23d9ff26ee63265e77afa3088` 与 `package.env.candidate` 一致。仅为文件完整性核对和候选包准备，未运行软件测试、仿真或板端命令；G3/G4 仍未放行。
- 启动前增加已观察 8 个 dpkg 包状态/版本的只读准入；仅覆盖已观察兼容性，不代表完整离线依赖闭包。G4 仍未验收。

## 本地源码对应核对补记（2026-10-31）

- 历史记录：本地源码核对工具命令退出码 0；37 个应用模块内容与旧来源提交 `bf817ca98b3a1032f83ac732424314d926e3da52` 的 Git blob 匹配。该报告记录的 index SHA-256 `63216dc2014621bc5f03e5201fcdc87dd33e4b620fe420317d660d266f09d56d` 对应 2026-10-31 刷新前的旧候选包，不验证当前包。报告：[source-correspondence-20261031.json](source-correspondence-20261031.json)；命令记录：[source-correspondence-20261031-command.json](source-correspondence-20261031-command.json)。
- 上述历史核对不证明当前包的源码对应、离线依赖闭包或板端状态。G4 未放行；五次冷启动、60 分钟有目标运行及故障测试已由用户豁免，最终现场测试归用户执行。
