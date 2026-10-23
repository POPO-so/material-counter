# WO-0015 Run-only 初始化及单帧/三帧恢复方案

状态：**仅方案与离线模型，未执行，待明确批准寄存器写入与失败标记归档。** 用户本轮明确选择“准备 Run-only 与恢复方案”；这没有解除硬件熔断。此目录没有 SSH/PYNQ 实板适配器或实板入口，所有模型结果均不可作为板端结果。

## 已确认的问题与依据

Retry 3 已实测 Tier 1 PASS、一次 Overlay 加载及 WO14/WO15 签名 PASS，但两个通道均 CR=0x00010002、SR=0x00000001，Run/Stop=0、Halted=1、Err=0；地址与 LENGTH 都为 0，Checker/Packer 计数为 0。上述是保存的历史快照，**本轮没有重新连接板卡，不能声称现场仍未变化**。

AMD PG021 将通道 RS=1 与后续地址/LENGTH 编程分别列出，非零 LENGTH 是 Simple DMA 数据搬运的触发步骤。DMACR bit 1 是读回为 1 的保留位，bit 2 才是 Reset。因此上次的 0x00010002 不是“Reset=1”。这也解释了为何不能把停止初态直接当作数据传输故障。[Simple DMA](https://docs.amd.com/r/en-US/pg021_axi_dma/Direct-Register-Mode-Simple-DMA)、[S2MM_DMACR](https://docs.amd.com/r/en-US/pg021_axi_dma/S2MM_DMACR-S2MM-DMA-Control-Register-Offset-30h)、[MM2S_DMACR](https://docs.amd.com/r/en-US/pg021_axi_dma/MM2S_DMACR-MM2S-DMA-Control-Register-Offset-00h)、[S2MM_DMASR](https://docs.amd.com/r/en-US/pg021_axi_dma/S2MM_DMASR-S2MM-DMA-Status-Register-Offset-34h)。

## 锁定身份

- boot：`822a9c29-37c0-438c-81c4-da998d57073b`。
- 已部署 manifest：`84c27012ec038d48267128af08deb9685eb1bb9a5fdb1a79e5da064083788312`。
- BIT：`a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420`。
- HWH：`bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6`。
- 沿用 `/home/xilinx/wo0015_first_board_retry3_20261015` 的现有已加载 Overlay；不改脚本/工件、不更换 manifest、不重载。
- 原失败标记按原始字节计算 SHA-256：`662e03d871f7275ed47bec0c302440a94dbc4e2fa35d499bce49a44d8decdeda`。
- overlay-only-consumed 原始字节 SHA-256：`c333975de186464bc3db9809c7762d7d7115cfc73f8b17f8e8672e3349efa37f`。
- overlay-only-pass 原始字节 SHA-256：`ee3d9ba6462962657bbd43807302387af16d4fbec34ee65f909a385886802d96`。

## 阶段 R0：执行前只读准入

未来若获执行批准，先新建独立恢复结果目录，保存严格指纹 SSH、隐藏密码认证证据。重新确认 boot、当前部署全部摘要、manager operating/flags=0、PYNQ global state 的当前 BIT 路径/hash、无其他硬件 FD/maps 占用、Tier 1 RAM/CMA 条件、无新内核异常。读取当前三个 Guard 的**原始字节**，必须与上述摘要完全一致；单帧/三帧 consumed Guard 必须仍不存在。

MMIO 前后的 boot 必须一致。当前 Checker/Packer 身份与计数、DMA CR/SR、SA/DA/LENGTH 必须与上次停止快照完全相符。特别要求两侧地址与 LENGTH=0、SG 未启用、Reset=0、CR=0x10002、SR=1，所有数据/握手/EOF 计数为 0。任何变化都停止，不用本方案猜测新现场，也不把“已运行”自动当作同一次初始化成功。

只在上述全部通过后，以独占创建方式领取一个**新增且固定**的 `run-only-init-consumed.json`，路径为 `/var/lib/wo0015-s2mm/run-only-init-consumed.json`。它记录本次新操作，不替代、不删除、不改名原 Overlay consumed Guard。若已存在则停止，不换路径或重试。

## 阶段 R1：最多两次控制写入

采用读-改-写，仅将 bit 0 置 1，保留其余位。按当前锁定基线，拟写：

| 顺序 | 通道 | 物理地址 | 原值 | 拟写值 |
|---|---|---|---|---|
| 1 | S2MM DMACR | 0x40400030 | 0x00010002 | **0x00010003** |
| 2 | MM2S DMACR | 0x40400000 | 0x00010002 | **0x00010003** |

每次写后只读轮询对应 DMASR。要求 Halted=0、Err=0；允许初次 Idle=0，IOC 不得凭空出现。S2MM 未达 PASS 则不写 MM2S。轮询每通道最多 1 秒（前提为 MMIO 调用能够返回）；同步 MMIO 卡死不受 Python 轮询期限中断，届时保留进程和现场，不终止后补写。

最多两次写入，禁止写 Reset、地址、LENGTH、DMASR 清状态或 Packer ARM；禁止分配 CMA、加载 Overlay或发送帧。任何一次写入/读取异常、boot 变化或状态不符即停止，不自动补写、重试或回退 Run=0。随后要求 CR 读回 0x10003、SR=0，地址/LENGTH 和所有计数仍为 0；签名、Tier 1 与硬件占用条件也必须继续通过。

Run-only 不分配 CMA；前后 CmaFree 都记录并要求至少 32768 KiB，不要求全局采样相等，也不把全局变化解释为本进程分配或泄漏。超时必须为有限正数，NaN/无穷值在领取 Guard 前拒绝。

该阶段仅建立通道运行态，不把它记为单帧 DMA PASS。记录每次实际写入、全部前后快照和独立 Run-only PASS/FAIL 收据；异常路径须尽力只读保存完整寄存器、dmesg 与进程现场，取证失败也不能解除熔断。所有证据绑定同一身份及 once Guard。

## 阶段 R2：失败标记的条件式原样归档

**必须另行明确批准此标记操作；Run-only PASS 本身不授权移除熔断。** 原 `/var/lib/wo0015-s2mm/terminal-failure.json` 在 R0/R1 全程保留。只有 R1 PASS、全部只读后检 PASS、且再次确认该标记原始 SHA 与 stage/reason 都完全匹配本次 Halted-only 失败后，才允许原样归档到：

`/var/lib/wo0015-s2mm/history/20261015-retry3-initial-state-terminal-failure.json`

历史 terminal 原始 schema 未含 HWH 字段；不得补写历史标记。归档计划要求其 boot、BIT、manifest 摘要完整匹配，并以同一锁定 manifest 及本次独立实测 HWH 摘要绑定工件；若历史标记含 HWH 则也必须匹配。仅有 PASS 字符串不能归档，必须同时校验前后全量快照、两次精确写入和零 LENGTH/分配/加载/传输证据。

目标必须不存在，禁止覆盖。先以独占方式保存完整原始字节并同步落盘、重读核对 hash，保留属主/权限和归档索引；在确认这份不可覆盖证据已保存后，才允许移除当前 canonical terminal 文件，使原执行器不再被这一历史失败拦截。新 Run-only consumed Guard 保留。任何保存/核对/后检异常都不能移除 canonical 文件，也不能继续帧测试。

原 `overlay-only-consumed.json` 与 `overlay-only-pass.json` **始终不动**，不重做 Overlay。没有重置任何阶段的已消费次数。新生成的恢复收据与 Step 1 acceptance 必须同时引用旧 Overlay PASS、新 Run-only PASS、旧 terminal hash 和归档位置；没有完整收据则不发 Step 2 命令。

## 阶段 R3：继续原单帧与三帧

仅在 R0/R1/R2 全部通过、且队长明确批准本恢复后的 Step 2/3 时，调用已部署的原 `board_once.py`，使用同一 manifest 摘要：

```bash
WO=/home/xilinx/wo0015_first_board_retry3_20261015
PY=/usr/local/share/pynq-venv/bin/python
HASH=84c27012ec038d48267128af08deb9685eb1bb9a5fdb1a79e5da064083788312
sudo env XILINX_XRT=/usr "$PY" -u "$WO/board_once.py" --mode dma-single --manifest "$WO/run-manifest.json" --manifest-sha256 "$HASH" --execute --authorize-stage dma-single --approve-once --timeout-s 5 --evidence-json "$WO/dma-single-result.json"
```

上述单帧命令只允许一次。必须逐项审阅物理地址 Tier 2、CMA 分配/释放、DMA/Checker/Packer/EOF/全量 Golden 结果及后检，PASS 后才允许以下命令一次：

```bash
sudo env XILINX_XRT=/usr "$PY" -u "$WO/board_once.py" --mode dma-three --manifest "$WO/run-manifest.json" --manifest-sha256 "$HASH" --execute --authorize-stage dma-three --approve-once --timeout-s 5 --evidence-json "$WO/dma-three-result.json"
```

单帧回收 76800 字节，三帧合计 230400 字节，原全部判定标准不变。任一失败继续熔断；若 LENGTH 已可能触发 DMA，则保持原 HOLD 路径，不释放/复位/重试。新错误创建新的 terminal 文件，不能覆盖历史归档文件。所有恢复结果另存新目录，不改写 Retry 3 的 FAIL_STOPPED 历史报告。

## 本轮离线核验与审批范围

纯离线模型 **41 项全部通过**。包括两个受限 RMW、错误/超时后无重试、boot/hash/地址/LENGTH/计数等前置变化时零写入、后检失败时保留熔断、归档只能生成受审计划，以及在现有 Mock 执行器中以 Idle=0 的 Run-only 后态完成一次单帧和一次三帧，零 Mock 数据差异且不重载 Overlay。后检三项回归先观察到失败，再修复模型拒绝逻辑。测试日志和 JUnit 保留。

模型没有 PYNQ/SSH/文件删除或重命名接口；真实适配器未实现、未上传。未来执行批准后仍须按此模型实现固定写入/once Guard/归档适配器，离线检查写入白名单与文件边界后才可发出板端命令，不能新增寄存器操作。

待批准的具体范围为：**一次 Run-only 初始化（最多两次上述控制写入）＋仅在全部 PASS 后原样归档这一条 Halted-only terminal 标记＋复用现有 Overlay 继续原单帧一次、三帧一次。** 当前批准仅涵盖方案准备，本轮这些动作全部未执行，原失败标记与所有 Guard 未变。
