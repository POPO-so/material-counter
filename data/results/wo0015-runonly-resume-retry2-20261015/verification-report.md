# WO-0015 CMA 水位门控修正与恢复 Retry 2 核验报告

日期：2026-10-15（Asia/Shanghai）

最终裁决：**FAIL_STOPPED，停在 Step 2 的 S2MM Binary8 与 Golden 全量比对**。本轮已完成一次真实单帧传输并回收 76800 字节，发现 **39520 字节差异，首个差异 offset=4（从 0 开始）**；未执行 Step 3。此次已不再是软件 CMA 算术差值准入失败，不能将传输完成等同于数据闭环 PASS。

## 环境、工件与资源

| 项目 | 实测结果 |
|---|---|
| boot_id（前后相同） | `822a9c29-37c0-438c-81c4-da998d57073b` |
| CmaTotal | 131072 KiB |
| 首次连接 CmaFree | 54952 KiB |
| Pre-check CmaFree | 52468 KiB |
| 退出 CmaFree | 52852 KiB |
| FPGA manager | operating / flags=0 |
| Overlay 加载 / Reset / Run-only 重新写入 | 0 / 0 / 0；本次加载耗时不适用 |
| 退出进程 FD/maps 占用 | findings=[]，denied=[] |
| 退出 Ping | exit code=0 |

板端 Pre-check 和退出均复核原部署工件字节摘要：

- BIT：`a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420`。
- HWH：`bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6`。
- base manifest：`84c27012ec038d48267128af08deb9685eb1bb9a5fdb1a79e5da064083788312`。

上述摘要严格匹配授权。已加载 BIT 路径仍为 `/home/xilinx/wo0015_first_board_retry3_20261015/wo15.bit`，PYNQ global state 的路径/hash、manager 与 boot 均通过。Tier 1 为 `MEMINFO_IOMEM_VERIFIED`，System RAM 精确 `[0,0x20000000)`，安全包络 `[0x00100000,0x20000000)`，未声称获知静态 CMA 子区间。

## Tier 2 修正与离线验证

仅修改 `scripts/wo0015/board_once.py` 中 run_frames、PynqBoardAPI.device_address、MockBoardAPI.__init__，加 16 MiB 常量和整数地址解析；README 同步更新，新增 `test_cma_watermark.py`。RTL、BIT/HWH 和原板端部署文件均未修改。

取消“分配时至少下降304 KiB”和“释放后精确恢复原值”硬门控，将差值和页对齐字节数仅作遥测。分配前、每次分配后、释放后均须至少 16384 KiB；物理地址必须是非零整数，physical/device 地址相同；严格验证下限1 MiB、512 MiB DDR上界、尺寸、对齐与不重叠。MM2S保留8字节对齐要求，S2MM为4字节。Tier 1 仍要求至少32768 KiB。

17项新回归先观察 RED（11 failed、6 passed），修正后17 passed；全量最终 **448 passed + 5 subtests**，6项续执行归档/边界测试通过，实际续执行 wrapper 的 Mock 回放共4帧零差异、4次free、0次Overlay加载。首次全量中旧WO-0014短时限测试曾出现1项已知抖动，日志保留；定向复查和完整重跑通过，未修改旧测试。

## 身份链与单次权限

原 manifest 仍绑定旧部署作为工件/基础身份；实际执行的是独立、字节同于已修正源代码的 `board_once_watermark.py`，其 SHA-256 为 `6be386b2140d8a373677eb337e6a3028464c0c720a75bd6c14ea03046cec7ec1`，另由 `continuation-execution.json` 和主机固定启动摘要绑定，不能误称旧 manifest 单独绑定了新执行器。

执行与证据在 root 管理的 `/var/lib/wo0015-s2mm/continuations/watermark-retry2-20261015`。上传先落独立暂存目录，ZIP一次读入、验证固定SHA后从同一字节流解包。启动器核对OUT与祖先root属主、无group/other写权限、无符号链接，持有flock，执行同一份已核验脚本字节；重读配置/执行器再次回核固定摘要。Step 3还要求单帧续执行PASS收据绑定新executor/config/result摘要。

旧 Overlay PASS、Overlay consumed、Run-only consumed 和旧单帧 consumed 均原样保留。旧单帧token继续阻断旧执行器；本次明确授权使用固定 `dma-single-watermark-retry2-consumed.json` / `dma-three-watermark-retry2-consumed.json`，不允许换路径或自动重试。每条请求已核对等于固定模板。

## 阶段 1：Pre-check 与历史归档 PASS

三步之前只读确认两CR=0x10003、两SR=0、SA/DA/LENGTH=0、Checker/Packer初始零计数，签名、CMA、占用与内核增量检查PASS。没有重新写DMACR、重载或软复位。

上一轮 CMA 算术失败的 terminal 与单帧consumed原始字节复制至 `/var/lib/wo0015-s2mm/history/20261015-retry1-cma-accounting/`，目标独占创建、fsync、重读校验摘要、保留uid/gid/mode。只移除当前旧terminal，旧single consumed canonical仍保留。恢复once Guard和Step1收据均持久化。

## 阶段 2：单帧真实传输，Golden FAIL

实际一次调用，`transfer_started=true`、`dma_start_attempted=true`、`dma_may_be_active=false`，退出码3。Tier 2已PASS，实际地址与尺寸：

| 缓冲 | 物理区间（左闭右开） | nbytes |
|---|---|---|
| MM2S | [0x15880000, 0x158b8420) | 230432 |
| S2MM | [0x15860000, 0x15872c00) | 76800 |

两区间在DDR包络内、对齐、尺寸精确、不重叠；S2MM回收76800字节/19200 words。遥测差值为 -676 KiB，不再据此误熔断。

| CMA 采样 | CmaFree / KiB |
|---|---|
| before | 36712 |
| after_mm2s | 37388 |
| after_s2mm | 37388 |
| after_release | 37232 |

四次水位均PASS；最终OS另采样52852 KiB。全局值不等于原值不再是错误，也不能据此单独证明每一缓冲区的内核页归属。此次没有直接追踪/证实页迁移过程，按授权修正规则处理全局计数。

软件先通过DMA Idle+新IOC+无错误、Packer DONE/计数/无错、Checker当前帧/累计计数、S2MM实际长度检查，随后在全量回读比对失败。停止后的独立寄存器快照进一步确认：

| 项目 | 停止后实测 |
|---|---|
| MM2S/S2MM DMASR | 0x00001002 / 0x00001002：Idle=1、IOC=1、Halted=0、Err=0 |
| Checker status / error | 0x9 / 0 |
| packet_done / packet_error | 1 / 0 |
| 输入字节 / 输入拍 / 输入TLAST | 230432 / 57608 / 1 |
| 像素 / SOF / EOL | 76800 / 1 / 240 |
| output_total / output_stalls | 76800 / 0 |
| output checksum / 前景像素 | 571200 / 2240 |
| Packer status / pixels / beats / error | DONE(2) / 76800 / 19200 / 0 |
| Golden | **39520/76800字节不一致，首差offset=4，FAIL** |

这些是停止后取证值，不虚构比对瞬间的完整帧收据。`frames=[]` 是因为原执行器在Golden成功之后才append帧记录，不能据此认为“没有传输”。输入TLAST=1是输入包统计，Packer没有独立输出EOF事件计数寄存器；可观测完成证据为DONE、19200拍及S2MM精确长度，不能冒充额外测得的EOF计数器。

**证据限制：现有失败路径只保存差异数量与首偏移，没有持久化完整RX二进制、差异位置列表或失败RX摘要。** 缓冲已释放，本轮不会读已释放物理范围、补跑或重建伪原始数据；本报告不凭计数推断差异根因。

## 阶段 3：三帧未执行

未创建三帧请求、三帧consumed或PASS收据；没有三帧地址/恢复/Golden验证结果，不能以离线Mock代替。

## 完整寄存器快照

| DMA寄存器 | Pre-check | 停止后 |
|---|---|---|
| MM2S_DMACR | 0x00010003 | 0x00010003 |
| MM2S_DMASR | 0x00000000 | 0x00001002 |
| MM2S_SA | 0x00000000 | 0x15880000 |
| MM2S_LENGTH | 0x00000000 | 0x00038420 |
| S2MM_DMACR | 0x00010003 | 0x00010003 |
| S2MM_DMASR | 0x00000000 | 0x00001002 |
| S2MM_DA | 0x00000000 | 0x15860000 |
| S2MM_LENGTH | 0x00000000 | 0x00012c00 |

| Checker/Packer offset | Pre-check | 停止后 hex / decimal |
|---|---|---|
| 0x00 | 0x00000001 | 0x00000001 / 1 |
| 0x04 | 0x574f3134 | 0x574f3134 / 1464807732 |
| 0x08 | 0x00000000 | 0x00000009 / 9 |
| 0x0c | 0x00000000 | 0x00000000 / 0 |
| 0x10 | 0x00000000 | 0x00000000 / 0 |
| 0x14 | 0x00000000 | 0x00000001 / 1 |
| 0x18 | 0x00000000 | 0x00000000 / 0 |
| 0x1c | 0x00000000 | 0x00038420 / 230432 |
| 0x20 | 0x00000000 | 0x0000e108 / 57608 |
| 0x24 | 0x00000000 | 0x00000001 / 1 |
| 0x28 | 0x00000000 | 0x574f3134 / 1464807732 |
| 0x2c | 0x00000000 | 0xc0dec014 / 3235823636 |
| 0x30 | 0x00000000 | 0x00650602 / 6620674 |
| 0x34 | 0x00000000 | 0x00012c00 / 76800 |
| 0x38 | 0x00000000 | 0x00000001 / 1 |
| 0x3c | 0x00000000 | 0x000000f0 / 240 |
| 0x40 | 0x00000000 | 0x0008b740 / 571200 |
| 0x44 | 0x00000000 | 0x00012c00 / 76800 |
| 0x48 | 0x00000000 | 0x00000000 / 0 |
| 0x4c | 0x00000000 | 0x00000002 / 2 |
| 0x50 | 0x00000000 | 0x00012c00 / 76800 |
| 0x54 | 0x00000000 | 0x00004b00 / 19200 |
| 0x58 | 0x00000000 | 0x00000000 / 0 |
| 0x5c | 0x00000000 | 0x00000000 / 0 |
| 0x60 | 0x574f3135 | 0x574f3135 / 1464807733 |

0x04=WO14 Checker签名，0x60=WO15 Packer签名；RTL无独立版本寄存器。完整原始环境、内核日志和寄存器在 `exit-os-result.json`。

## 失败 Guard、清理与历史保留

当前terminal已重新立起，stage=`dma-single`，reason为Golden的39520字节差异。新的单帧 consumed保留，无新单帧PASS，三帧guard不存在。旧归档均保留。

| Guard/历史路径 | 原始SHA-256 |
|---|---|
| `/var/lib/wo0015-s2mm/overlay-only-pass.json` | `ee3d9ba6462962657bbd43807302387af16d4fbec34ee65f909a385886802d96` |
| `/var/lib/wo0015-s2mm/cma-watermark-recovery-consumed.json` | `ed86d62376dfaf78673b7af9b0e014e5a3ffbb56909b70ac5f40eb97abcc4de0` |
| `/var/lib/wo0015-s2mm/terminal-failure.json` | `b796fba0208adbfe2ec5df7ceaa6a3988f6602f9c1f6e18c8b74a6255817421e` |
| `/var/lib/wo0015-s2mm/overlay-only-consumed.json` | `c333975de186464bc3db9809c7762d7d7115cfc73f8b17f8e8672e3349efa37f` |
| `/var/lib/wo0015-s2mm/dma-single-consumed.json` | `1585844bceb4bca82cee2f4a2be14916b2fee847a26fb7f28c207e9234fcf862` |
| `/var/lib/wo0015-s2mm/dma-single-watermark-retry2-consumed.json` | `1585844bceb4bca82cee2f4a2be14916b2fee847a26fb7f28c207e9234fcf862` |
| `/var/lib/wo0015-s2mm/run-only-init-consumed.json` | `ac53e6acba00404c86acd7c2e5dae1729ef0a62228538f857b8104d6b866df18` |
| `/var/lib/wo0015-s2mm/history/20261015-retry3-initial-state-terminal-failure.json` | `662e03d871f7275ed47bec0c302440a94dbc4e2fa35d499bce49a44d8decdeda` |
| `/var/lib/wo0015-s2mm/history/20261015-retry1-cma-accounting/terminal-failure.json` | `1bac1e556839b37a9deb42fa229801f21637904b2ad4a288026a8618dc9dc9ea` |
| `/var/lib/wo0015-s2mm/history/20261015-retry1-cma-accounting/dma-single-consumed.json` | `1585844bceb4bca82cee2f4a2be14916b2fee847a26fb7f28c207e9234fcf862` |

异常发生于DMA已完成后的比较，未进入FAIL_HOLD；两次free均返回后记录after_release=37232 KiB。最终进程FD/maps扫描为空，manager正常，内核增量健康 `exit-kernel-health.json` 为 `PASS`；远端命令已退出，SSH已关闭，本地凭据控制台已结束，未强制终止硬件占用进程。

所有旧结果索引均复核未变：`{"wo0015-first-board-retry3-20261015": 114, "wo0015-runonly-proposal-20261015": 16, "wo0015-runonly-resume-20261015": 34, "wo0015-runonly-resume-retry1-20261015": 84}`。未提交、未合并、未暂存；前后Git状态、实际source差异已保存，git diff --check通过。硬件失败后未修改板端代码/工件或执行重试。

完整本地结果目录：`C:\Users\POP\.codex\worktrees\wo0015-design-20261015\material-counter\data\results\wo0015-runonly-resume-retry2-20261015`。

板端root结果目录：`/var/lib/wo0015-s2mm/continuations/watermark-retry2-20261015`；经sudo只读Base64导出，主机逐文件核验原始SHA，原件在 `remote-evidence/`。本目录包含完整stdout/stderr、传输耗时、Guard原始字节、寄存器、dmesg、摘要索引与测试日志。

当前维持 **FAIL_STOPPED**。单帧计数与DMA完成证据不等于S2MM数据闭环通过；后续调查/硬件操作必须另行裁决，本轮不继续。
