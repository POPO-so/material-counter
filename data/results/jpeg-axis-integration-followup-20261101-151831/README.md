# JPEG AXIS 联合集成修复交付状态

采集时间：2026-11-01 15:18 +08:00。工作区 branch `test/验证MJPEG硬解码前提`，HEAD `6e37b7cbfc8b7b14507dfde9e3f49a1a05b25215`。本报告只覆盖 capture 窄审查、脚本门禁/线程设置及新 clone 准备；不代表连接、综合或资源 fit 已通过。

## 改动文件

- `fpga/experimental/jpeg_integration/joint_common.tcl`：仅 `capture_refresh_region` 有 refresh_region 的严格负 warning-counter-reset 审查；原 FAIL receipt 保留；成功与负例都核对预期返回路径。overlay refresh 采用普通 capture。
- `fpga/experimental/jpeg_integration/joint_connect.tcl`：对 image_pipeline 与 binary_overlay 两个含 region 核的 module reference 分别 refresh，第二个不继承窄例外。
- `fpga/experimental/jpeg_integration/joint_capture_review.py`：对原生日志指定字节区间独立核对完整行、warning 逐条/导出数、计数差、错误等级和操作返回；记录 range SHA，并明确整文件 SHA 仅为 review 时快照。
- `fpga/experimental/jpeg_integration/test_joint_capture_review.py`：15 个离线单测覆盖允许路径、错误码/错误描述、警告不匹配、缺失/超界/半行区间、ERROR/CRITICAL WARNING、返回值差异、日志追加后范围哈希稳定及 supplementary receipt 写入。
- `fpga/experimental/jpeg_integration/joint_prepare.py`：将 review helper 纳入 immutable manifest/source verification 与新 stage spec。
- `fpga/experimental/jpeg_integration/joint_build.tcl`：synth/route launch jobs 设为 1；进程及子进程 `maxThreads=2` 保留。route 未执行。
- `fpga/experimental/jpeg_integration/README.md`：记录窄审查和双 module-reference 验收边界。

未改 `prepare_snapshot.py`、JPEG RTL/HLS/component、baseline XDC 或全局技能；未 commit。旧用户改动及未跟踪路径未清理。

## 已执行验证命令

1. 离线负例/正例测试：

   `C:/AMDDesignTools/2026.1/Vivado/tps/win64/python-3.13.0/python.exe -m unittest fpga.experimental.jpeg_integration.test_joint_capture_review -v`

   结果：15/15 PASS。

2. 新 clone source/gates 复核：

   `C:/AMDDesignTools/2026.1/Vivado/tps/win64/python-3.13.0/python.exe fpga/experimental/jpeg_integration/joint_prepare.py --verify-manifest C:/w/m/joint_manifest.json --verify-gates`

   结果：`JOINT_SOURCE_HASHES_OK`、`JOINT_GREEN_GATES_OK`，退出码 0。

3. 既有 refresh capture 的离线 supplementary replay 使用 `joint_capture_review.py`，输入原 receipt `C:/w/j/evidence/connect/002-refresh_region.tcldict`、native log 区间 `[28529,36502)`、warning 9→4/exported 2、实际/期望 XCI 路径。

   结果：`PASS_SUPPLEMENTAL_REVIEW`；独立区间有 2 条逐字匹配 WARNING，CRITICAL WARNING/ERROR 均为 0；区间 SHA-256 `0ec76e1665686dda4a4513ccf435feff07b468875ad1b7772999d4b9f7f373c6`。完整 review receipt 在 `C:/w/m/retained-refresh.supplement.json`。原始 receipt 仍为 FAIL，未改写。

## 新 clone 与未完成项

新隔离 clone：`C:/w/m`。manifest SHA-256 `88d97eb800146dcbf5e3aeb964e5893900c241771901bcc92635eeb6c98fd50a`；spec SHA-256 `b7ee37552f6f8510ea952313f459e2a09b6f6bb719a2d959947cd97ae17230a9`。固定 baseline `c5a86807c93e6ed572e2f75e13a6f9c7f1320321`、decoder source `d695aac91915f3bf8b4d4357d7b2ca539929197720b2f2390cfb5a2472108f7d`、component `0bdfce509b1f51829aa1c2b12536fba14088925f89ce8c7da2558707246725d5`。manifest 包含两份 region RTL replacement，哈希均为 `19439a2c666696d48521b112705763df6ac48012cfacd9476fcd6850a3551911`。文件 `C:/w/m/offline-stage-verification.json` 保留 staging 摘要。

**Connection：NOT RUN。Synthesis：NOT RUN。Route：NOT RUN。** 没有 live readback 证明两个 module reference 已刷新，也没有综合资源结果。

## 环境阻塞快照

`environment-snapshot.json` 记录只读检查结果：另一 G2 路径 `Q:/data/results/standalone-20261101-151222-g2-integration/` 仍存在；Vivado PID 44788 仍运行，另有 PID 46532 的 `vivado.exe`，其归属未擅自判定；PID 43380 当前未出现在活动相关进程列表。系统 FreePhysicalMemory 为 **734.1 MiB**，低于启动条件 **3 GiB**；pagefile 已分配 34,816 MiB、当前使用 3,455 MiB，不能替代物理内存门槛。

因此当前连接和综合均因资源/他人 Vivado 活动阻塞；未启动 Vivado、未关进程、未触碰 Q: 构建。满足“没有他人重型 Vivado 且可用物理内存≥3 GiB”后，按 `C:/w/m/joint_spec.tcldict` 用 `C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat` 原生 batch 先运行 `joint_connect.tcl`。若 overlay 普通 capture 出现任何 counter reset/error，停在证据并交 Manager；成功并检查两个 replacement 的 live readback 后，方可用 `joint_build.tcl -stage synth`（jobs=1）继续。不得启动 route。

## 恢复命令与后续门槛

Manager 最新复测：FreePhysical **806 MiB**，另一 G2 仍在运行。本任务不启动 Vivado，不轮询等待。此前 `environment-snapshot.json` 是 15:18 的独立快照（734.1 MiB），保留原值，不覆盖历史采样。

资源条件满足（无其他重型 Vivado、FreePhysical≥3072 MiB）后，在 PowerShell 从工作区执行以下 connect 命令。它只使用 `C:/w/m` 的冻结 clone 和真实 `-log` 路径：

```powershell
$repo = 'C:/Users/14173/Documents/material-counter/.runtime/worktrees/jpeg-probe-20261101'
$stage = 'C:/w/m'
$vivado = 'C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat'
$connectLog = "$stage/native-connect.log"
Push-Location $stage
try {
  & $vivado -mode batch -notrace `
    -source "$repo/fpga/experimental/jpeg_integration/joint_connect.tcl" `
    -log $connectLog -journal "$stage/native-connect.jou" `
    -tclargs -spec "$stage/joint_spec.tcldict" -native-log $connectLog
  if ($LASTEXITCODE -ne 0) { throw 'JPEG joint connect failed; preserve all evidence.' }
} finally { Pop-Location }
```

连接成功后先核实 `joint-connected.tcldict` 和 live-files/readback，确认 image_pipeline 与 binary_overlay 两处 region replacement 均生效。overlay 普通 capture 若失败，保留原始 FAIL receipt 并停止交 Manager；不可沿用 image_pipeline 的窄例外。两处 readback 通过后，资源条件仍满足时仅运行以下综合命令：

```powershell
$synthLog = "$stage/native-synth.log"
Push-Location $stage
try {
  & $vivado -mode batch -notrace `
    -source "$repo/fpga/experimental/jpeg_integration/joint_build.tcl" `
    -log $synthLog -journal "$stage/native-synth.jou" `
    -tclargs -spec "$stage/joint_spec.tcldict" -native-log $synthLog -stage synth
  if ($LASTEXITCODE -ne 0) { throw 'JPEG joint synth failed; preserve all evidence.' }
} finally { Pop-Location }
```

route 不在后续命令内，本轮及本报告均未启动 route。

## 可 selective-stage 的小文件

`selective-stage-files.txt` 列出本补丁涉及的小型源码/文档/测试文件。不要将 `.runtime`、生成 Vivado 工程或本轮结果目录加入补丁提交。`prepare_snapshot.py` 保留原样。
