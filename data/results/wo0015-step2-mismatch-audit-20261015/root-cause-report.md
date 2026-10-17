# WO-0015 Step 2 差异：离线根因审计与取证增强

日期：2026-10-15（Asia/Shanghai）

**结论：前景在 Checker 处存在；历史摘要与奇偶 Word 假设精确吻合，但确切数据差异根因尚未证实。** 锁定 HWH/BD 的 S2MM 整条路径均32位，无静态32→64位宽转换。新发现的关键待核验点是 **PS HP1 的运行时 AFI 32-bit enable**：HWH参数与运行寄存器是两层证据，历史报告只覆盖前者。此次没有RX原始字节，也没有AFI1读数，不能把候选模式判为已证根因。

本轮仅本地文件审计、离线Python/Mock测试及AMD官方文档核对；**板卡连接、MMIO、CMA分配、Overlay加载、DMA和Vivado构建均为0**。没有修改RTL、BIT/HWH、板端脚本、Guard或任何历史结果。原板端结论仍为FAIL_STOPPED，不能声称当前会话状态已重新验证。

## 1. 已保存的实板寄存器与缺失证据

来源是上轮停止后的独立快照：[exit-os-result.json](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-runonly-resume-retry2-20261015/exit-os-result.json)，而不是本轮新测。其SHA为 `0d5e71fa072d00a1e1e68dc9fe22c1da3b7ea64a256112e58b238ca0cdf6a87a`。双DMA DMASR均0x1002；此证据说明传输完成及无DMA错误，不说明回读数据正确。

### Checker

| offset | 字段 | 实测 decimal / hex |
|---|---|---|
| 0x08 | status | 9 / 0x00000009 |
| 0x0c | error_code | 0 / 0x00000000 |
| 0x10 | frame_id | 0 / 0x00000000 |
| 0x14 | packet_done_count（完成包数） | 1 / 0x00000001 |
| 0x18 | packet_error_count | 0 / 0x00000000 |
| 0x1c | input_bytes | 230432 / 0x00038420 |
| 0x20 | input_beats | 57608 / 0x0000e108 |
| 0x24 | input_tlast | 1 / 0x00000001 |
| 0x28 | input_first_word | 1464807732 / 0x574f3134 |
| 0x2c | input_last_word | 3235823636 / 0xc0dec014 |
| 0x30 | input_checksum | 6620674 / 0x00650602 |
| 0x34 | pixel_count | 76800 / 0x00012c00 |
| 0x38 | SOF count | 1 / 0x00000001 |
| 0x3c | EOL count | 240 / 0x000000f0 |
| 0x40 | output_checksum | 571200 / 0x0008b740 |
| 0x44 | output_total | 76800 / 0x00012c00 |
| 0x48 | output_stalls | 0 / 0x00000000 |

没有单独名为 `frame_count` 的寄存器；`frame_id=0` 是帧编号，`packet_done_count=1` 才是这次完成包数。没有独立 `fg_count` 寄存器。实测 `output_checksum=571200`，按Binary8的255前景字节定义，**571200 / 255 = 2240**，属于由实测寄存器推导的前景像素数；不是额外实测计数器。

Pre-check该校验和为0、完成后571200，且执行器的Checker检查与锁定Golden checksum一致，因此不支持“该帧Checker完全未产生前景”的解释。但统计量匹配不能单独证明每个像素的位置完全一致。寄存器映射/成功帧派生方式见 [当时实际执行脚本](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-runonly-resume-retry2-20261015/remote-evidence/board_once_watermark.py:47)。

### Packer 全部暴露字段

| offset | 字段 | 实测 decimal / hex |
|---|---|---|
| 0x4c | status | 2 / 0x00000002 |
| 0x50 | accepted_pixels | 76800 / 0x00012c00 |
| 0x54 | sent_beats | 19200 / 0x00004b00 |
| 0x58 | error_code | 0 / 0x00000000 |
| 0x5c | arm readback | 0 / 0x00000000 |
| 0x60 | signature | 1464807733 / 0x574f3135 |

DONE=2，BUSY=0、FAULT=0；没有独立输出EOF事件计数器。Checker input_tlast=1 是输入包统计，不能替代输出EOF计数。Packer DONE、19200拍与S2MM实际76800字节是本轮保留的完成证据。

### Golden 差异点能回答到哪里

历史日志只有：`byte_mismatches=39520`、`first_mismatch=4`、`s2mm_actual_length=76800`。没有差异位置列表、RX摘要或RX `.bin`。[失败异常仅保存这三个数的代码](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-runonly-resume-retry2-20261015/remote-evidence/board_once_watermark.py:513)。

| 偏移 | Golden expected | 历史 RX actual |
|---|---|---|
| 0..3 | 00 00 00 00 | 可由“首差=4”推断相同；不是保存的字节转储 |
| 4 | 00 | 仅能推断非00；具体值未知，不能写成A5 |
| 5..63及后续 | 可读取锁定Golden | RX未保存，未知 |

因此不能补造“前几个差异的expected/actual”。旧物理范围已释放，本轮没有读取其内容或补跑恢复数据。

## 2. Poison 预填充与Cache调用核查

实际执行文件的精确代码（[行446–447](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-runonly-resume-retry2-20261015/remote-evidence/board_once_watermark.py:446)）：

```python
api.write_buffer(s2mm_buffer, b"\xA5" * preflight.RX_BYTES)
api.sync_to_device(s2mm_buffer)
```

全部76800字节填A5，每个32-bit word都为0xA5A5A5A5，**软件未制造“偶数word零、奇数wordPoison”初始化**。真实适配器通过byte视图写入全部字节；allocate请求为uint8形状。完成后（[行513–514](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-runonly-resume-retry2-20261015/remote-evidence/board_once_watermark.py:513)）：

```python
api.sync_from_device(s2mm_buffer)
returned = bytes(memoryview(s2mm_buffer).cast("B")[:preflight.RX_BYTES])
```

[缓存适配方法](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-runonly-resume-retry2-20261015/remote-evidence/board_once_watermark.py:949)优先调用buffer.sync_to_device()/sync_from_device()，缺失时分别回退flush()/invalidate()；两方向均存在，缺两种支持方法会失败。历史日志未记录实际采用的分支、PYNQ缓冲coherent标志或同步前后数据，故只能证实脚本的同步请求与回退逻辑，不能证明底层Cache行为无误，也不能断言实际恰好走了flush/invalidate分支。

## 3. HWH、BD、端口与XSim

锁定HWH SHA-256：`bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6`，与授权严格一致。

| 节点/参数 | 实值 | 实锤位置 |
|---|---|---|
| DMA C_M_AXI_S2MM_DATA_WIDTH | 32 | [HWH](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-hw-50mhz-20261015/export/wo15.hwh:1001) |
| DMA C_S_AXIS_S2MM_TDATA_WIDTH | 32 | [HWH](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-hw-50mhz-20261015/export/wo15.hwh:1012) |
| DMA M_AXI_S2MM bus | 32 / AXI4 | [HWH](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-hw-50mhz-20261015/export/wo15.hwh:1446) |
| hp1_fabric S00 / M00 | 32 / 32 | [HWH](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-hw-50mhz-20261015/export/wo15.hwh:3046) |
| PS PCW_S_AXI_HP1_DATA_WIDTH | 32 | [HWH](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-hw-50mhz-20261015/export/wo15.hwh:4113) |
| PS S_AXI_HP1 bus | 32 / AXI3 | [HWH](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-hw-50mhz-20261015/export/wo15.hwh:5107) |
| image_pipeline WIDTH/HEIGHT/THRESHOLD | 320 / 240 / 128 | [HWH](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-hw-50mhz-20261015/export/wo15.hwh:3114) |

连接是 `DMA M_AXI_S2MM → hp1_fabric/S00_AXI → hp1_fabric/M00_AXI → PS7/S_AXI_HP1`，不是HP0。[构建Tcl连接](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/scripts/wo0015/create_s2mm_bd.tcl:191)、[生成BD连接](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-hw-50mhz-20261015/project/wo15_50m.srcs/sources_1/bd/wo15/wo15.bd:1437)。

内部是1×1 AXI Interconnect的 `hp1_fabric/s00_couplers/auto_pc`，SI_PROTOCOL=AXI4、MI_PROTOCOL=AXI3，前后32位。[生成BD转换器](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-hw-50mhz-20261015/project/wo15_50m.srcs/sources_1/bd/wo15/wo15.bd:838)。未见此路径auto_us/auto_ds或32→64位宽转换；不能以模块名auto_pc误认位宽转换。MM2S读路径为64-bit memory/HP0→32-bit stream，是独立路径。

阈值128是静态RTL/module-ref参数，无需PS写运行时threshold/ROI寄存器。XSim同为320×240/128，并写drain_enable=1及Packer arm；板端Pre-check drain_enable=1。未发现“仿真配置了阈值/ROI、上板漏配”的静态差异。XSim TB自身说明未建模真实AXI DMA/板卡，因此它不覆盖PS HP/DDR事务。[TB参数/覆盖边界](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/fpga/sim/wo0015/tb_axis_dma_s2mm_pipeline.sv:3)。

### 静态32位不等于运行时AFI1已为32位

生成的 [ps7_post_config_3_0](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/data/results/wo0015-hw-50mhz-20261015/project/wo15_50m.gen/sources_1/bd/wo15/ip/wo15_ps7_0/ps7_init.tcl:150)包含：

```tcl
mask_write 0XF8009000 0x00000001 0x00000001
mask_write 0XF8009014 0x00000001 0x00000001
```

这两地址分别为AFI1读/写通道控制，bit0=1选择32位、0选择64位。AMD [UG585读控制字段](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-AFI_RDCHAN_CTRL-Details)、[写控制字段](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-AFI_WRCHAN_CTRL-Details)提供独立定义；本地生成HTML也列相同字段。完整源摘录/hash见 `local-source-evidence.json`。

实际执行器显式代码只调用Overlay.download，没有显式调用ps7_post_config或采集上述地址；**不代表FSBL、镜像或PYNQ库内部一定没有配置它们**。历史证据不能确定当前AFI1为32或64。若bit0实际仍为0，将与BD的32位HP1声明不符；这是必须优先补证的运行时位宽候选，尚不能判定为已证根因。

## 4. 数学模式复现与非唯一性

锁定Golden SHA-256：`174164ae33086f8606b83113f43ebd5a52470f8640bde1c489e51e76f411a60e`；76800字节只含00/FF，前景2240。偶/奇32-bit word各9600个，前景各1120。

**离线假设，非板端RX：**偶word全0，奇word全A5。

| 分组 | 差异字节 | 全零word | 全A5 word |
|---|---|---|---|
| 偶数word | 1120 | 9600 | 0 |
| 奇数word | 38400 | 0 | 9600 |
| 合计 | 39520，首差4 | 9600 | 9600 |

已生成带明确 `OFFLINE_SYNTHETIC_NOT_BOARD_RX` 标签的例子。另一个反例是保持Golden前4字节不变，将偏移4至39523的每字节异或1；也得到39520差异、首差4，却不是奇偶Poison模式。这直接证明两个摘要数不足以唯一反演RX，更不足以判别PS位宽、Packer或Cache根因。两组完整反例与前64字节分析在 `synthetic-only/`，**不得当作历史实测**。

## 5. 已完成的失败路径增强

改动在 [board_once.py](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/scripts/wo0015/board_once.py)、[11项新测试](C:/Users/POP/.codex/worktrees/wo0015-design-20261015/material-counter/scripts/wo0015/test_rx_diagnostics.py) 和 README；可审阅副本在 `updated-executor/`。新执行器SHA为 `c276840080b8c70bd3294d74ab84061ce3596e087dfb83abfef63690903e58a6`。

Golden失败时，在释放前按阶段、帧序号和frame_id生成三个独占文件：完整 `*_rx_raw.bin`、匹配Golden `*_golden.bin`、`*_diagnostics.json`。写入后flush/fsync并重读校验hash；完成后才允许正常释放。实际硬件阶段默认放到证据JSON旁的 `dma-single-diagnostics/` 或 `dma-three-diagnostics/`，直接run_frames也可显式提供diagnostic_dir。

JSON包含前64字节的16个32-bit RX/Golden word（原始hex、小端数值、每word差异字节数），前32个逐字节差异的offset/expected/actual，完整RX/Golden SHA，及偶/奇word各自总数、差异字节数、全零数、全Poison数。同时保存失败帧的DMA状态、Checker前后快照、Packer状态/计数、物理地址门控和运行身份，避免Golden失败后frames=[]丢失完成细节。

若任何落盘/校验失败，记录计划路径、可能部分文件及错误，保留已完成缓冲区进入证据HOLD；`dma_may_be_active=false`保持真实，明确不同于不确定DMA仍在途的HOLD。真实PYNQ包装器保留阶段FAIL_HOLD和hardware_stage。禁止自动覆盖、重试或先free再取证。

本轮只验证了离线Mock/文件系统行为，没有上传或执行新脚本。后续打包必须重新绑定新执行器摘要，并显式导出诊断子目录；旧仅导出结果根目录一层的collector不能自动带回子目录 `.bin`。

### 回归

11项定向测试全部通过，覆盖奇偶统计、原始/Golden完整文件在首次free前存在并hash一致、Step3失败帧命名、防覆盖、部分保存失败、真实PYNQ HOLD包装的阶段与活动状态。新增测试先RED再修复，日志完整保留。

最终全量命令 `python -m pytest -q scripts python`：**459 passed + 5 subtests**，没有跳过/修改既有测试或放宽timeout。之前以 `python scripts` 收集顺序运行两次均触发既有WO-0014 40ms墙钟用例抖动（api.buffers为空），各456 passed + 1 failed；其单独运行通过。更改两目录收集顺序后，同一完整范围通过；先前失败日志未删除，不能宣称该旧测试抖动已修复。

## 6. 根因裁决与待批准计划

| 候选 | 本轮裁决 |
|---|---|
| Checker完全无前景 | 不支持；实测checksum推导2240 |
| HWH/BD静态S2MM32→HP164错配 | 未发现；静态整条路径32 |
| 上板漏写threshold/ROI运行寄存器 | 未发现对应寄存器；静态参数与TB一致 |
| 实际偶零/奇A5模式 | 数字完全吻合，但RX未保存，未证实 |
| 运行时AFI1仍是64位 | 优先只读核验的候选；缺两个寄存器读数 |
| Packer数据、Cache/coherency等 | 不能由当前材料唯一确认或排除 |

已证实并修复的是“失败路径丢失RX原始数据”的脚本缺陷；**不能把这一取证缺陷冒充本次数据差异的根因**。

下一步分阶段审批，详见 `next-step-approval-request.md`：先仅只读核对AFI1；若配置不符，再评审最小AFI位更新或与镜像一致的64-bit内存接口新构建方案；若配置已一致，则在另行批准的一次捕获中同时记录原始RX与Cache分支/元数据，再定位Packer/内存/缓存。不会直接运行完整ps7_init/post_config，也不会仅凭算式重编BIT或重跑DMA。

## 完整性与交付

旧结果索引全部未变：`{"wo0015-first-board-retry3-20261015": 114, "wo0015-runonly-proposal-20261015": 16, "wo0015-runonly-resume-20261015": 34, "wo0015-runonly-resume-retry1-20261015": 84, "wo0015-runonly-resume-retry2-20261015": 104}`。BIT/HWH摘要仍严格锁定；未改RTL或构建新工件，未改任何板端Guard。原开发目录和worktree未提交、未合并、未暂存，前后git status及diff --check已保留。

完整结果目录：`C:\Users\POP\.codex\worktrees\wo0015-design-20261015\material-counter\data\results\wo0015-step2-mismatch-audit-20261015`。主要证据是 `saved-board-evidence.json`、`static-bus-audit.json`、`local-source-evidence.json`、`parity-hypotheses.json`、`synthetic-only/`、`primary-sources.json`、测试日志、代码diff、`summary.json`、`evidence-sha256.json`。
