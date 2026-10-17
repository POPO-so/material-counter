# WO-0015 第一阶段离线实现验收

2026-10-15；工作区 `C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter`，分支 `codex/wo0015-design-20261015`。本轮没有 SSH、板端 MMIO、DMA 或 Overlay 操作。下述实现、仿真和布局布线结果均为离线证据，首次上板仍需队长批准。

## 1. RTL 与 Packer

新增 `fpga/experimental/wo0015/axis_s2mm_frame_packer.v`、`axis_dma_s2mm_pipeline_bd.v`；旧 WO-0014 RTL 保持原样。完整链路：

`DDR → MM2S memory64/stream32 → Packet Adapter → RGB2Gray → Gray2Binary → 共享像素接受点(Checker + Packer) → stream32 S2MM → AXI Interconnect → HP1 memory32 → DDR`。

主时钟固定 50 MHz，MM2S 保持 HP0。第一阶段没有串入形态学或真 ROI。

Packer 输入 Binary8、SOF `TUSER[0]`、EOL `TLAST`；每四个成功输入握手按 `{pixel3,pixel2,pixel1,pixel0}` 形成 32-bit 字。输出 `TKEEP=4'hf`，屏蔽行尾 TLAST，仅最后一个字携带 EOF。计数器只随成功握手前进；正常帧为 76,800 像素、19,200 输出拍、1 EOF。最后四字节先保存在私有寄存器，只有完整输入包 trailer/checksum 验证成功后才提交 EOF，因此坏包不会形成伪成功完整帧。

`TVALID=1 && TREADY=0` 时保留数据、KEEP 和 LAST；故障时也不撤回已经呈现的有效拍。故障封锁新输入，已呈现的普通拍可完成握手；不自动重试或发补齐 EOF。短包、超长包、校验错误由 Adapter packet_error 触发停机；SOF/EOL/非 00/FF 字节也触发独立错误。错误复位须走新受控操作，正常帧间只重新 ARM，无硬件复位。

Checker 使用与 Packer 相同的 `valid && ready` 接受点，反压下不重复计数。保留 0x00..0x48 旧寄存器；新增：

| 偏移 | 含义 |
|---|---|
| 0x4c | Packer busy/done/fault，位 0/1/2 |
| 0x50 | 本帧接受像素数 |
| 0x54 | 本帧输出握手拍数 |
| 0x58 | 错误码：1 ARM 状态、2 packet、3 SOF、4 EOL、5 像素值、6 对齐 |
| 0x5c | 写 bit0 ARM；wrapper 忙时返回 SLVERR 并保留当前传输 |
| 0x60 | 新签名 0x574f3135；0x04 仍为旧签名 0x574f3134 |

## 2. XSim 与 Python 验证

交付入口 `scripts/wo0015/run_sim.tcl` 已在 Vivado 2026.1 运行，终态 `WO0015_XSIM_SUITE_PASS`。最终复验目录：`data/results/wo0015-xsim-replay-20261015/`。外部 Python 调用使用 `-E`，隔离 Vivado 自带 Python 环境变量。

| 用例 | 输出拍 | EOF | 逐字节结果 |
|---|---:|---:|---|
| A 单帧 | 19,200 | 1，第 19,200 拍 | 76,800 字节零差异 |
| B 三帧 | 57,600 | 3，第 19,200/38,400/57,600 拍 | 230,400 字节零差异 |
| C 随机反压，seed 1/7/42/20261015，各一次 | 每次 57,600 | 每次 3 | 每次 230,400 字节零差异 |
| D 短包 | 9,599 | 0 | 已输出前缀匹配，packet fault |
| D 超长包 | 19,199 | 0 | 已输出前缀匹配，packet fault |
| D checksum 错误 | 19,199 | 0 | 已输出前缀匹配，最后字被扣留 |
| D 异常复位 | 复位前 4 | 0 | 复位清空状态，未提交整帧 |

正常用例合计 16 帧、307,200 拍、1,228,800 字节、16 EOF，零差异。H3 原始 487 PNG 的身份全部核对，但本次 WO-0015 仅仿真选定帧，不能将源数据完整性核对写成 487 帧新链路仿真。

Manager 最终 Python 回归：基线与向量测试 `292 passed, 5 subtests passed`（`data/results/wo0015-offline-20261015/final-baseline-regression.log`）；PS 两套 `51 passed`（`ps/manager-final-tests.log`）。总计 **343 项测试、5 项子测试通过**。PS 测试覆盖非有限超时静态拒绝、marker/证据写入失败仍 HOLD、在持久化前安装信号处理器。独立审查记录为 `wo-0015-ps-review-20261015.md`。

额外小几何 Packer 测试覆盖提前/延迟 packet_done、EOF 反压稳定性、故障复位。`tb_axis_s2mm_guards.sv` 验证错误 SOF/EOL/像素值、忙时 ARM SLVERR 和当前传输保留，marker `WO0015_GUARDS_PASS`。旧 `data/results/wo0015-offline-20261015/eof-negative-control/` 保留故意提前 TLAST 的失败对照，首拍即被自检器捕获。最终回归统计见同目录验收索引及 PS 日志。

## 3. Vivado 50 MHz 构建

脚本 `scripts/wo0015/create_s2mm_bd.tcl`、`build.tcl`、`export.tcl`、`finalize_build.py`。S2MM memory/stream32、18-bit BTT、addr32、Simple 模式，无 DRE/Micro/SG；HP1=32-bit AXI3，经新增 1×1 AXI Interconnect。HP0=64-bit 继续承载 MM2S。配置读回、HWH 外部连线和内部 AXI4→AXI3 converter 读回分别留证，HWH 不含 converter 内部节点，未从 HWH 虚构该证据。

构建目录 `data/results/wo0015-hw-50mhz-20261015/`，工程 `project/wo15_50m.xpr`。实现时序：

| 指标 | 结果 |
|---|---:|
| WNS / TNS | +9.009 ns / 0 ns |
| Setup failing endpoints | 0 / 10,250 |
| WHS / THS | +0.028 ns / 0 ns |
| Hold failing endpoints | 0 / 10,250 |
| WPWS / TPWS | +8.750 ns / 0 ns |
| Pulse-width failing endpoints | 0 / 3,763 |
| LUT / FF / BRAM / DSP | 3,040 / 3,553 / 2.5 / 0 |

DRC：0 Error、0 Critical Warning；4 类 Warning 共 16 项（PDCN-1569×3、REQP-1839×8、REQP-1840×4、RTSTAT-10×1），2 类 Advisory 共 3 项（REQP-165×1、REQP-181×2）。Methodology：0 Error/Critical，1 类 LUTAR-1 警告共 3 项，涉及厂商 HP 协议转换器异步复位路径。告警原文完整保存在 `reports/drc.rpt` 和 `reports/methodology.rpt`；不得称为“零告警”。

| 工件 | SHA-256 |
|---|---|
| `export/wo15.bit` | `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420` |
| `export/wo15.hwh` | `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6` |

完整工件、RTL 快照和报告哈希见 `export/pair-manifest.json`，数值汇总见 `reports/build-summary.json`。同目录还导出 routed DCP、BD recreate Tcl、实现 XDC 和时钟报告。原生 Vivado log 的哈希为构建取证时快照，随后离线仿真追加日志，不代表整个会话终态日志。

## 4. PS 执行器与首次上板申请

`scripts/wo0015/board_once.py` 与 `board_preflight.py` 默认离线，不导入 PYNQ；Mock 走相同寄存器驱动状态机。仅一次分配 TX=230,432、RX=76,800 字节，串行复用；每帧 TX 写入并同步、RX 写 A5 哨兵并同步，再 ARM。严格 S2MM Run→DA→LENGTH=76800，之后 MM2S Run→SA→LENGTH=230432。轮询双端 Idle+IOC、Packer done，无 DMA error，再核对回写长度、像素/拍数、Checker、回读逐字节与 CMA 归还。

执行前校验 boot、固定 manifest/BIT/HWH/执行器 hash、HWH 参数与 HP DDR 范围、实测 CMA 范围与余量、TX/RX 不重叠及对齐、设备 FD/maps 占用、FPGA manager、PYNQ global state；阶段后校验 boot 和 dmesg 连续性。无缓存 API 即拒绝。潜在在途错误保留两个 CMA buffer 和进程，保存故障并进入 fail-hold；禁止复位、自动重试、跳过 Guard 或继续三帧。已完成 DMA 后的内容错误仍保留 transfer_started=true，不能误报为未启动。

首次上板需要另行批准 `overlay-only → dma-single → dma-three` 三个单次阶段。第一阶段包括一次 Overlay 下载及两次只读签名 MMIO；后两阶段只附着已有 BIT，不重新加载。每步独立固定 Guard；三帧必须有同 boot/manifest/BIT 的单帧 PASS 收据；任一故障禁止推进。精确打包身份和待批准命令见 `wo-0015-first-board-request-20261015.md`。

WO-0014 依队长决定封存，本轮未补跑；此前 CmaFree=76,508 KiB 只是历史基线，不作为下一次实时健康证明。

## 交付状态

已核对 9 个受保护旧文件 hash、7 个构建 RTL、5 个构建脚本及 9 个回放源文件 hash；BIT/HWH 身份匹配。原开发目录 HEAD 仍为 `7a868c070024bdc29cdeb057fbec9ccaf94ef160`，无 tracked diff；独立 worktree 的新增交付文件仍为 untracked，未提交、推送或合并。构建占用及全部原始日志保留，未执行清理。

首次上板包的 offline 与 Mock 结果分别保存为 `data/results/wo0015-offline-20261015/bundle-offline.json`、`bundle-mock.json`。打包身份和精确待批准命令见首次上板申请；唯一剩余验收边界是实板双向闭环，本轮没有对此作 PASS 声明。
