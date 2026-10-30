# G2 安全读回与统一所有权冒烟（2026-10-30）

结论：本轮关闭退出后 pipeline 读回和 HP0 位宽缺口，统一硬件所有者下的 RLE+HDMI 冒烟正常停止。结合 [原 G2 构建、网表回归及相机冒烟](../standalone-20261030-064700-g2-integration/README.md)，G2 放行。G2.5、G3、G4 尚未通过；本轮不是持续性能或物料准确率验收。

## 身份和原始证据

- 板卡：192.168.2.99 / pynq，boot ID `e48ff153-bd85-4dd6-98f6-438bd51da08d`。
- BIT SHA-256 `2ec1f1dec5a2df2e07044e94ecacd5d20aeef19ec6787077c0728d24053a8b18`；HWH SHA-256 `a663072734f2b16be9f37be0699ba897b5902c0b46398a4be914f6ee40fa852f`。本轮复用已加载 G2，没有重编译或重新下载位流。
- [首次安全读回](readback.json)、[统一冒烟原始报告](coordinated-smoke.json)、[退出后独立读回](post-smoke-readback.json)；[实际运行脚本](coordinated_smoke.py)。板上墙钟未校准，时长使用 monotonic。
- 原 Fail-Hold runner 已在此前授权恢复阶段消失，本轮再次进程检查无 runner，无终止信号。原失败证据完整保留。

## External abort 定位

原故障内核地址 `0xb6f59000` 位于映射的 `0x43c20000` 页首。该 pipeline 的 AXI-Lite RTL 将 `0x00` 定义为启动写寄存器，读分支仅定义 `0x04` 起的指定偏移；未定义偏移返回 `RRESP=2`。这解释了页首额外探读与正常 transport 签名/状态访问结果不同的原因。未在真板重复触发故障，也未将整个页当作安全读寄存器范围。

新增 `scripts/standalone/g2_readback.py` 只读 RTL 已定义的 13 个偏移；无写入、无 PYNQ 导入。原 transport 所用寄存器不变。本轮 whitelist 读回签名 `0x31454c52`、geometry `0x01900280`、MAX_RUNS 16384、state 2（done_seen，busy/error 清零）成功。前后内核尾部未出现新 abort。该诊断由 RTL、故障页首地址和成功限定读回共同支持，不是对原失败命令的逐指令重演。

## 时钟、HP 和退出状态

FCLK0/1/2 为 50 / 100 / 142.857143 MHz，SLCR FPGA reset 为 0。AFI0/1/2 RDCHAN_CTRL 为 0/1/1，对应 HP0/1/2 读位宽 64/32/32；AFI1 WRCHAN_CTRL 为 3841，对应 HP1 32 位写通路。11 个控制地址仍由原 HWH/Tcl 构建回读支撑，没有对未定义寄存器做全地址扫描。

统一 runner 在一个 `DriverOwner` 和两把既有互斥锁内持有 overlay、RLE CMA 与 HDMI CMA。相同 G2 BIT 已加载、旧 DMA idle、无设备 owner 后才准入。已完成旧帧的 done_seen 由该显式实验会话承接，不清硬件、不重置 DMA；脚本中 `_completed_frames=1` 仅用于此受控重复冒烟，不是生产启动策略。

12 帧 moving synthetic 输入均为 640×400，逐像素重建一致，RLE DMA/hardware 实际长度一致；PS 合成后的同帧灰度/二值输出由 monitor 识别到 12 个新身份，末帧为 11、上下 marker 一致、错误标志均 false。合成目标计数为 1，仅验证计数代码确实运行，不是实物准确率。

启动首帧前共 7 次 invalid-marker 扫描，partial 为 0；本轮只有结束快照，不能据此给出稳定期零错误或 G2.5 持续欠载结论。G2.5 必须先建立有效身份，再按稳定区间差分统计。

正常停止顺序：确认 RLE 两通道 idle 且无错误 → 释放 RLE 两缓冲 → 关闭 HDMI 并确认 VDMA halted → 关闭 DriverOwner。原始报告记录 `STOPPED`、`buffers_released=true`；独立退出后再读 RLE/VDMA/pipeline 和 owner，`findings=[]`、`denied=[]`。RLE DMACR run bit 仍为 1，称 idle，不称 halted。相机未由本 runner 打开；此前 60 秒 G2 相机显示冒烟的相机释放证据在原 G2 报告。

## 软件验证与工作区边界

`python3.12 -B -m pytest -p no:cacheprovider --basetemp .runtime/pytest-baseline-01 scripts/standalone scripts/rle scripts/wo0015/test_board_preflight.py -q`：213 passed。安全读回 helper 的 7 项测试覆盖未定义偏移/写入禁止、签名、几何、容量、busy/error 和 done_seen。默认临时目录曾导致 89 个 fixture 环境错误，指定全新 basetemp 后全部通过。

原分支 LJ-01，当前开发分支 `feat/推进脱机集成验收`，工作树 `C:/w/mc-progress`，已合入现有 G2 集成分支。原主工作区含未解决合并，未处理其冲突、删除或其他本地修改；自动合回 LJ-01 必须等待该既有合并得到解决。

## 后续

G2.5：固定有目标和高游程输入，对比 HDMI 开/关，测 RLE 延迟和 DDR 竞争，记录有效显示、DMA/VDMA 错误及有界内存。当前 BIT 尚无专门的 64 位 RLE 活动/反压锁存计数接口，不能把 PS 耗时或包时间戳冒充反压计数。G3/G4 仍需联合相机计数、功能恢复和真正拔线冷启动的独立证据。
