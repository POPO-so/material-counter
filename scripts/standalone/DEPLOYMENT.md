# 独立部署、离线依赖与旧版回退说明（Task 5/6）

本文记录当前可核对的独立显示/G2 制品、离线依赖和安全边界。它不是安装器或回退脚本；下文标为“未实现”的步骤不能当作现成命令执行。

## 当前状态和入口

- **G1 有限显示入口**：`scripts/standalone/material-counter-g1-display.service` 启动 `scripts/standalone/boot_autostart.py`，再执行 `scripts/standalone/g1_run.py` 的 camera 显示路径。服务描述和入口均限定为最多 7200 秒（2 小时）的 G1 显示。它不含 G2 RLE 联合验收能力，**G1 service 不能作为 G4 整机自启动**。
- **G1/G1.5 离线入口**：`scripts/standalone/g1_run.py` 通过 `scripts/standalone/bundle.py` 检查显示 manifest、BIT/HWH、运行时字体和 PYNQ 源/本机库哈希；需要显式 `--execute`、manifest 哈希、当前 boot ID 和 root 权限。运行还依赖 `cv2`、`numpy`、PYNQ `Overlay/MMIO/allocate` 与视频 API、相机 API、Droid Sans 和 DejaVu 字体，以及匹配板端 ABI 的预编译 PYNQ `_video.cpython-310-arm-linux-gnueabihf.so` 等已锁定文件。服务固定使用 `/usr/local/share/pynq-venv/bin/python3`，不是通用系统 Python。
- **视频源离线校验入口**：`scripts/standalone/verify_sources.py` 只读核对 `video_sources.lock.json` 记录的 PYNQ checkout commit 与文件 SHA-256；其现存调用形式为：

  ```sh
  python scripts/standalone/verify_sources.py --source-root <PYNQ-checkout> --brief
  ```

- **历史：早期 G2 集成制品**：旧制品目录为 `data/results/standalone-20261030-064700-g2-integration/export/`，其旧 BIT SHA-256 为 `2ec1f1dec5a2df2e07044e94ecacd5d20aeef19ec6787077c0728d24053a8b18`。本段保留早期制品与 [G2 安全恢复记录](../../data/results/standalone-20261030-g2-recovery/README.md) 的历史说明，不代表当前候选制品身份或后续门禁状态。
- **当前 G2/G2.5/G3 证据**：最新 G2/G2.5 制品位于 `data/results/standalone-20261030-123000-g2-integration/`，BIT SHA-256 为 `79609f9a60c3e727b5f2f54a8d639a464a098715fb5eb28d294646a9735948e0`。[G2 复验报告](../../data/results/standalone-20261030-123000-g2-integration/README.md)和 [G2.5 并发压力报告](../../data/results/standalone-20261030-g25-concurrency/README.md)记录的证据通过。 [G3 部分回归报告](../../data/results/standalone-20261030-g3-functional/README.md)仅记录固定回放的显示开关一致性及该回放 FPS 子项；G3 未放行，G4 未验收。不得将这些状态合并表述为整机部署验收。
- `scripts/standalone/g2_readback.py` 是安全读回代码的一部分，不是部署/回退入口。最新制品通过 G2/G2.5 不代表统一启动服务、G3 或 G4 已验收。

## 候选整合启动入口（未验收）

以下实现是候选入口，尚未完成板端安装、启动或冷启动验收：

- `scripts/standalone/boot_integrated.py`：校验部署包、包源码清单和运行时文件，对照 `board-runtime.json` 做 Python/平台/发行包版本准入，再读取 `boot-config.json` 并按启动策略初始化后进入运行入口；兼容性比对不安装依赖或证明依赖闭包。该文件自身不生成部署包或 index。
- `scripts/standalone/boot_policy.py`：候选启动策略模块。
- `scripts/standalone/delivery.py`：除既有完整性验证外，当前有 `--build-index` 候选命令，可对**已组装**目录生成并复核 `deployment-index.json`；验证包内 `package-source.json` 的应用模块列表与索引中的 Python 源文件集合一致。它不收集/复制依赖，也不会安装文件或写入板端环境变量。
- `scripts/standalone/material-counter-integrated.service`：候选 systemd 单元，从 `/etc/material-counter/package.env` 读取 `PACKAGE_ROOT` 与 `PACKAGE_INDEX_SHA256` 并传给入口。静态检查显示单元只依赖 local-fs.target，且不等待网络；G4 候选环境文件已填写拟安装目录和索引哈希，但尚未复制到板端、注册 service 或验证启动。

候选索引生成调用形式（仅用于已经完整组装并包含有效 `boot-config.json` 的离线包目录）：

```sh
python -m scripts.standalone.delivery --root <assembled-package> --build-index
```

旧索引 SHA-256 `3addc403eacdb17caf7fb699a712dbbb90b047ec1f7049c47df2b84bf1eccc2a` 及其 55 项记录是历史完整性结果。当前本地 `delivery.py --build-index` 已以退出码 0 完成，索引覆盖 55 个包文件，内建 `verify_package` 核对 56 个文件；新索引 SHA-256 `63216dc2014621bc5f03e5201fcdc87dd33e4b620fe420317d660d266f09d56d` 与 `package.env.candidate` 一致。此结果仅证明本地文件完整性并完成候选包准备，不代表离线依赖闭包、目标板运行、service 安装或 G4 通过。候选状态见 [G4 包候选记录](../../data/results/standalone-20261030-g4-offline-package-candidate/README.md)。

完整部署交付仍缺少并需另行验证：

- [ ] 候选目录已组装入口、37 个应用模块、匹配 BIT/HWH/manifest/native、7 个锁定 PYNQ 源和 2 个字体；板端已有运行环境的版本见包候选记录。第三方 Python 包没有封装或重装，仍须确认目标部署仅依赖现有板端环境，或另行准备可追溯的离线依赖集。
- [x] 本地运行 `delivery.py --build-index`（退出码 0），更新 `deployment-index.json` 和外置 `package.env.candidate` 摘要；索引覆盖 55 个包文件，内建 `verify_package` 核对 56 个文件，新索引 SHA-256 `63216dc2014621bc5f03e5201fcdc87dd33e4b620fe420317d660d266f09d56d` 与环境文件一致。此项只关闭候选包文件完整性/准备子项，不代表离线依赖闭包或板端验收。
- [ ] `boot-config.json` 已在候选包中定义 `continuous` 模式、固定 by-id 相机和 7200 秒参数；package.env.example 仍为占位模板；G4 目录的环境值候选文件已绑定当前索引。仍须将包及环境文件安装到板端，并核实实际 service 启动行为。
- [ ] 确认目标板离线依赖齐备及 ABI/哈希匹配；不得以启动时联网补包或现场编译替代。
- [ ] 完成板端安装、正常启动/停止和冷启动验收，核对实际加载 BIT/HWH/manifest、唯一 owner、安全终态及逐次 boot ID。

## 离线依赖与完整性约束

1. 部署包必须带齐该版本的 Python 入口/模块、manifest、匹配 BIT 与 HWH、字体、PYNQ Python 源和预编译 native 库，以及各自清单。G1 manifest 将这些文件及 SHA-256 列出；`bundle.verify_bundle()` 会校验 manifest、BIT、HWH、地址映射和 VDMA 参数，`bundle.verify_runtime()` 会校验字体及安装后的 PYNQ 源/native 文件。整机入口还会比对包内板端运行时观察记录，拒绝平台或所需发行包版本不一致；此检查不提供第三方依赖或系统库的离线重建能力。
2. native 扩展必须是目标板 Python/架构 ABI 对应的已构建文件，并按 manifest 哈希核对。启动阶段**禁止现场编译 native 代码、安装依赖或联网补包**；缺文件、ABI 不符或哈希不符应拒绝启动。当前代码提供运行时哈希拒绝逻辑，没有离线安装器或启动时编译步骤。
3. 相机使用稳定路径 `/dev/v4l/by-id/usb-HSK_USB_Camera_20210616103-video-index0`；G1 自动入口在设备缺席时最多等待 120 秒。G1 自动入口校验的 `manifest-diag-resume.json` SHA-256 固定为 `685bb871fed4d0c1ed58ff9bb5c079b2a51748c1102c25b86dee5ca8a6e7c8d3`。每次启动/验收须绑定当次 `/proc/sys/kernel/random/boot_id`，运行报告记录该 boot ID。入口会拒绝传入的 boot ID 与当前值不一致，并拒绝覆盖已有报告。不要把某次记录的 boot ID 当成后续冷启动的固定值。
4. 比对 manifest 哈希、manifest 内每个文件哈希和实际板端 BIT 身份；G2 还须确认 BIT/HWH 与 manifest 是同一构建对。哈希缺失/不符、身份不明、boot ID 不符或制品不成对时，停止启动/切换并保留现场供 owner 处理。
5. 运行目录和版本制品目录必须分开：版本包只读保留，日志/报告写入各自版本的运行目录，禁止覆盖旧报告或在不同版本间共用可变部署目录。G1 当前路径 `/home/xilinx/standalone-20261029-g1` 与旧依赖 `/home/xilinx/rle-20261028` 由 service 明确引用；G2 冒烟路径 `/home/xilinx/standalone-g2-smoke-20261030/` 是另一独立目录。保留旧包及其 manifest、BIT、HWH 和依赖，未确认新包可恢复前不得清除或替换旧包。

## 硬件所有权和回退安全门

- 任一时刻只能有一个硬件 owner 操作 PL、DMA、VDMA、相机及相关缓冲。`scripts/standalone/driver_owner.py` 的超时语义是进入 `FAIL_HOLD` 并保留资源引用；不得通过自动杀进程、自动重启 service、自动重载 overlay 或并发启动另一个 runner 来“恢复”。不确定时维持隔离并由唯一 owner/Manager 判定。
- 切换或回退前必须先由当前 owner 停止工作，并有同一轮可核对证据证明 **DMA idle、VDMA halted、无错误、缓冲/相机资源已释放且 owner 已释放**。G2 记录里的 RLE DMA idle 和 HDMI VDMA halted 属于特定冒烟结束后的状态，不是任意时刻的通用切换许可；只允许读取已定义的 pipeline 偏移，禁止对控制页从偏移 0 开始盲读。
- 只有上述安全条件全部成立，且旧包的身份与目标 BIT/HWH/manifest 三者匹配并通过哈希校验后，才可由获授权的唯一 owner 切换到旧版。切换后重新核对加载 BIT 身份和新 boot/session 记录。任何一项状态未知、读回异常或 hash 不匹配时，不切换，保持 `FAIL_HOLD` 并交 Manager 决策。
- 当前仓库没有已实现、已验证的通用 G2→G1 自动回退脚本、旧包重装命令或安全停机命令序列；此节是必须满足的人工安全门，不代表脚本已具备这些动作。已有 G1 runner 只允许特定已识别 BIT 身份下进行受控迁移，不能推广为 G2 回退流程。

G4 部署/恢复边界另见[审阅草稿](../../data/results/standalone-20261030-g4-offline-package-candidate/deployment-recovery-draft.md)。候选包尚未复制到板端，候选 service 尚未注册或启动，部署前 service/env/current 状态未知，也没有已确认的旧 G4 release。该草稿仅记录静态事实和待实测项；仓库仍没有可执行或已验证的 G4 回滚命令或步骤。不得将其视为部署、测试或恢复授权。

## G4 整机自启动尚缺的验收证据

以下项目尚未完成；G1 两小时 service 与 G2 串行冒烟不能替代它们：

- [ ] **G4 selfstart**：正式、受审查的整机启动入口/服务及明确依赖、单 owner、锁、启动失败和停机行为；冷启动后确认加载的是成对且哈希匹配的 BIT/HWH/manifest。
- [ ] **5 次冷启动**：逐次保存唯一 boot ID、实际加载 BIT/HWH/manifest 哈希、owner/进程状态、启动结果、DMA/VDMA 安全终态和日志；5 次都须可复核。
- [ ] **连续 60 分钟**：在 G4 整机路径运行并保存时长、帧/处理统计、错误计数、资源/缓冲状态、owner 和最终 DMA idle/VDMA halted 证据。已有 G1 2 小时显示只能证明其限定 G1 路径，不能代替此项。
- [ ] **拔线人工证据**：按批准的现场步骤记录断开相机/视频输入线后的检测、告警、输入恢复或停机行为，以及 DMA/VDMA/缓冲/owner 的安全状态。该行为目前未定义为已验收功能，需 Manager 决定验收步骤和安全边界。
- [ ] **整体验收**：旧 BIT 的 G2 已放行；新增 BIT 必须重新验证，随后依次完成 G2.5/G3/G4，不能把 G2 视作整机完成。

## 可核对的现存命令

G1 systemd 单元中的现存启动命令（仅说明代码配置，不代表 G4 服务）：

```sh
/usr/local/share/pynq-venv/bin/python3 -B -u /home/xilinx/standalone-20261029-g1/scripts/standalone/boot_autostart.py
```

`boot_autostart.py` 内部再以当前 boot ID、固定 by-id 相机路径和固定 manifest SHA-256 调用 `scripts.standalone.g1_run --execute`，时长固定 7200 秒、请求 20 FPS。除上述已存在入口及 `verify_sources.py` 校验命令外，本文不提供安装、service 启停、G2 启动或回退命令；这些流程目前未形成可核对且已验收的命令接口。
