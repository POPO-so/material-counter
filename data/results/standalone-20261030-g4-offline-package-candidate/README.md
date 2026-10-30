# G4 脱机包候选（未安装、未验收）

状态：已在工作区组装候选部署目录并生成 `deployment-index.json`。本记录不代表 G3 完成或 G4 放行；计划要求的板端安装、独立供电/拔线冷启动、60 分钟有目标运行、故障恢复和安全停止证据仍缺。

## 包身份

- 候选目录：[package](package/)
- 来源提交：`816ac6ba4055a852e4094144e940acbcf145912b`，分支 `feat/记录G2并发与G3回归`。
- 归档文件数：54 个（不含 deployment index 本身）。索引 SHA-256：`3addc403eacdb17caf7fb699a712dbbb90b047ec1f7049c47df2b84bf1eccc2a`。
- 索引生成命令：`python -B -m scripts.standalone.delivery --root data/results/standalone-20261030-g4-offline-package-candidate/package --build-index`。
- 命令结果：55 项文件清单校验通过，`hardware_actions=0`，范围为 `integrity_only_no_gate`。这只证明当前目录和清单哈希/路径匹配，不证明入口可运行或任何硬件门禁通过。

## 内容

- 与 G2 manifest 配套的 BIT、HWH、manifest 和 ELF32 ARM 原生库；BIT SHA-256 `79609f9a60c3e727b5f2f54a8d639a464a098715fb5eb28d294646a9735948e0`。
- 36 个运行入口及传递导入所需的应用 Python 模块；来源提交写入 `package/package-source.json`。
- manifest 锁定的 7 个 PYNQ 运行源码和 2 个字体。从板卡只读复制后，本地计算的 13 项依赖/制品 SHA-256 全部与来源 manifest 或 G2 记录一致；逐项记录见 [dependency-readback.json](dependency-readback.json)。
- `boot-config.json` 使用 `continuous`、固定相机 by-id 路径；候选 systemd 单元和占位环境模板位于 package/deployment/。同目录外的 [package.env.candidate](package.env.candidate) 填入了拟安装 release 路径及当前索引 SHA-256，不属于已索引包，也没有安装到板端。
- [board-runtime.json](package/board-runtime.json) 记录板端现有运行时的只读版本观测：CPython 3.10.4、PYNQ 3.1.1、NumPy 1.21.5、OpenCV 4.5.4、Pillow 9.0.1、SciPy 1.8.0、cffi 1.15.0。

## 启动入口静态核对（未安装）

- 候选 systemd 单元只声明 After=local-fs.target、WantedBy=multi-user.target，未依赖 network-online.target、network.target 或远端服务。
- boot-config.json 固定相机 by-id 和 continuous 模式。boot_integrated.py 接收 package root 与 index SHA-256，核对 deployment index、manifest 和运行依赖哈希，读取本机 boot ID 后执行一次性初始化，再转入集成运行器。
- package.env.candidate 将 root 指向 /opt/material-counter/releases/standalone-20261030-g4-offline-package-candidate，索引哈希固定为 3addc403eacdb17caf7fb699a712dbbb90b047ec1f7049c47df2b84bf1eccc2a。文件仅供后续部署拷贝与 review；目录未部署，systemd 未注册，入口未启动。
- 以上是对候选配置和代码的静态核对，不证明目标板离线启动、相机打开、位流准入或 G4 门禁通过。
## 范围和未完成项

- 第三方 Python 包由板端既有 PYNQ 运行环境提供，没有打包 wheelhouse，也没有安装或升级这些依赖；当前运行环境观测不等于干净环境可离线重建。
- `package/deployment/package.env.example` 保留安装时需填写的占位值。候选包尚未复制到板卡、注册 systemd、配置 `/etc/material-counter/package.env` 或启动。
- G3 未放行，因此该包没有进入整机验收。没有执行软件测试、仿真、构建或板端运行。
- Route、视频源和 ADR-0005 接口状态不变；物料准确率未验收。
