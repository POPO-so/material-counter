# WO-0015 仓库关键文件核验清单

日期：2026-10-15，Asia/Shanghai。只读源码/工件核验，不是WO0015实现或板测结果。

## 版本边界

实际远端main通过 `git ls-remote origin refs/heads/main` 确认为 `b7d8be05caff7da3ad2235617b43b591daa5e5fc`，已获取到 `refs/codex/wo0015-main-snapshot-20261015`。独立工作区分支 `codex/wo0015-design-20261015` 基于该快照。旧本地main=`4398f64d`与原origin/main=`4332ba96`均陈旧，不能用它们的文件缺失否定已合并代码。原开发目录仍为 `feat/pl-stream-skeleton`，本轮未改其提交/实验文件。

以下未加分支说明的路径/行号均指上述live-main快照。ROI单独来自 `codex/wo-0008-roi` commit=`f0fee4444056f3365889674b854becb4a4c0422b`，尚未在本次main树中；不得宣称已串入WO0014。

## 1. Block Design与DMA配置

生成脚本 `scripts/wo0014_hw/create_image_bd.tcl:40-47`：

```tcl
CONFIG.PCW_USE_M_AXI_GP0 1 CONFIG.PCW_USE_S_AXI_HP0 1
CONFIG.PCW_S_AXI_HP0_DATA_WIDTH 64
CONFIG.c_include_sg 0 CONFIG.c_include_mm2s 1 CONFIG.c_include_s2mm 0
CONFIG.c_micro_dma 0 CONFIG.c_sg_length_width 18 CONFIG.c_addr_width 32
CONFIG.c_m_axi_mm2s_data_width 64 CONFIG.c_m_axis_mm2s_tdata_width 32
```

50MHz HWH为 `data/results/wo0014-hw-50mhz-20261014/export/wo0014_image.hwh`，SHA-256=`9e2e09f0cb79ca678d24c3c3d826fbb393832bec6a45a43e2c49f4276dcc4b57`；与现有50MHz BIT配对，不加载文件。

| 参数 | 当前值 | HWH行号 | 工程含义 |
|---|---:|---:|---|
| c_include_s2mm | 0 | 989、1022 | 没有启用接收通道 |
| c_m_axi_s2mm_data_width | 32 | 1001、1028 | 关闭通道的保留参数值，不是已连通DDR写口 |
| c_s_axis_s2mm_tdata_width | 32 | 1012、1036 | 同上，HWH没有实际S_AXIS_S2MM接口 |
| c_sg_length_width | 18 | 1009、1038 | 最大262143 bytes；足够两方向单帧 |
| c_include_sg | 0 | 992、1025 | Simple DMA，长度字段仍生效 |
| c_addr_width | 32 | 1016；S2MM地址宽另见1000 | 地址寄存器按32-bit使用 |

更正长度要求：`76800 < 2^17`，17 bits已可表示Binary8单帧；`230432 > 2^17-1`，MM2S的RGB协议包需要18 bits。WO0013旧17-bit设计不能直接承载新RGB包；当前WO0014已经18-bit，WO0015保持18-bit即可，不需无依据改大。

Tcl `:63-65`：

```tcl
axi_dma_0/M_AXI_MM2S -> hp0_protocol/S_AXI
hp0_protocol/M_AXI -> ps7/S_AXI_HP0
axi_dma_0/M_AXIS_MM2S -> image_pipeline/s_axis
```

`:51-52`的hp0_protocol将AXI4转换AXI3，宽64；`:66-70`连同一FCLK；`:84-85`将Data_MM2S映射0起512 MiB DDR窗。HWH `:1286/:2158`共享`axi_dma_0_M_AXI_MM2S` BUSNAME，`:2206/:3964`共享`hp0_protocol_M_AXI` BUSNAME。HP1关闭（HWH `:2537/:3407`），不是现有第二条DMA路径。新增S2MM必须新增HP1/转换器或显式多主仲裁。

## 2. TLAST / TUSER语义

实际端口名多为 `m_tlast/m_tuser`，而非统一的m_axis前缀；语义如下。

| 模块/文件 | 关键行 | TLAST和TUSER |
|---|---|---|
| `fpga/experimental/wo0014/axis_dma_rgb_adapter.v` | 87-89 | `m_tuser<=(emitted_pixels==0)`；`m_tlast<=(column==WIDTH-1)`：首像素SOF、每320像素EOL；输入s_tlast才是MM2S包EOF |
| `fpga/rtl/axis_rgb2gray.v` | 31、40-41 | 弹性单拍握手；直接转发SOF/EOL |
| `fpga/rtl/axis_gray2binary.v` | 28、37-39 | 输出00/FF；直接转发SOF/EOL |
| `fpga/experimental/wo0014/axis_binary_output_checker.v` | 23-41、50-81 | 没有Master Stream输出；只监听成功握手，按WIDTH检查EOL并累计；SOF开始新观察窗 |
| `fpga/rtl/axis_morphology3x3.v` | 49、124-132 | 重新生成`m_tuser<=(output_y==0 && scan_x==1)`、`m_tlast<=(scan_x==PAD_X)`；保持全幅320×240、每行EOL，行相位及底边flush会背压 |
| ROI分支 `fpga/rtl/axis_roi_crop.v` | 69-70 | `m_tuser[0]<=(x_extended==X0_VALUE && y_extended==Y0_VALUE)`；`m_tlast<=(x_extended==ROI_LAST_X)`：裁后每行EOL，不是EOF。输出大小为ROI宽×高 |

因此现有链路没有S2MM帧尾打包器。若只做8→32宽度转换而保留每行TLAST，第一行80个32-bit拍共320 bytes就结束首包；S2MM_LENGTH=76800是容量，不会将240个行包自动拼为一帧。需要新增 `axis_s2mm_frame_packer`：保持内部EOL供Checker验证，输出第19200次握手唯一EOF。

ROI真裁剪若小于320×240，会破坏冻结的76800-byte合同；阶段一不串入小ROI。后续可采用全幅mask或明确填回全幅，但不能悄悄改变接收长度。Morphology虽保持全幅，仍须独立逐像素/反压/多帧验证后串入。

规范依据：[PG021 Simple DMA](https://docs.amd.com/r/en-US/pg021_axi_dma/Direct-Register-Mode-Simple-DMA)、[S2MM_LENGTH实际字节回读](https://docs.amd.com/r/en-US/pg021_axi_dma/S2MM_LENGTH-S2MM-DMA-Buffer-Length-Register-Offset-58h)。本结论结合官方包传输语义与当前RTL，不把内部video EOL等同DMA EOF。

## 3. Checker兼容方式

`axis_dma_image_pipeline.v:95-114`已将最终Binary8像素和同一 `m_tready` 接到被动Checker；源码注释明确不进入ready路径。BD wrapper `axis_dma_image_pipeline_bd.v:128`当前为 `assign pipe_m_tready = drain_enable;`，`:110`以此计算接受，`:200-202`累计输出/停顿；`:236-260`映射AXI-Lite统计。

建议新增WO0015 wrapper，改为 `pipe_m_tready=drain_enable && packer_s_tready`，给Packer的VALID为 `pipe_m_tvalid && drain_enable`。Checker继续监听原像素流和这个统一ready；这样Packer接受几个像素，Checker就准确观察几个。保持0x00～0x48地址和语义，Packer诊断仅追加新寄存器。

无需把Checker改成透传模块，也无需axis_broadcaster。若未来另加独立主动消费者，必须使用能记住各分支接受状态的AXIS broadcaster；普通TVALID扇出加任意ready拼接不满足广播握手。

## 4. PS参考基线

当前已有离线参考，尚无板端双通道DMA接收服务：

- `python/refmodel/ps_reference/evaluate_h3.py:749-797`：CLI/parser/main与`evaluate_dataset`调用；`:77-220`校验H3配置、GT及8-bit Gray PNG文件；`:472-491`读可见PNG为二维`np.uint8`后调用区域提取和tracker。
- `python/refmodel/ps_reference/regions.py:47-51`：非空二维uint8合同；`:64-99`执行Gray>=128二值化及可选形态学；`:101-125`调用OpenCV `connectedComponentsWithStats(..., connectivity=8, ltype=cv2.CV_32S)`。
- `python/refmodel/ps_reference/tracking.py:56-63,88-92`：`Tracker.update(frame_id,detections)`，Region列表输入、帧号严格递增；不是DMA内存API。

报告 `docs/verification/wo-0010-ps-reference.md:27-35,63-68` 给出命令和结果。在原仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe python/refmodel/ps_reference/evaluate_h3.py --data data/synthetic/h3_full --out data/results/wo0010-reproduce-new --line-x 160 --hysteresis 2
```

`--out`必须不存在；已存在时选另一新路径，不能覆盖旧结果。独立worktree复现可使用原环境的绝对解释器与原H3数据绝对路径，脚本用新main版本，输出放独立worktree新目录。依赖NumPy/OpenCV/Pillow；本轮核验命令和既有证据，**未重新跑H3，不把历史61.29%写成新测**。

其余默认参数：threshold128、min_area16、morph_mode0、max_width24、confirm_hits2、max_missing2、max_step6、max_y8、direction+1。原Gray PNG图像通路31个可确认越线事件，TP19、FN12、FP/重复0，precision100%、qualified recall=19/31=61.29%；另有35次物理中心线穿越，4次未完成迟滞退出。剥离身份GT几何诊断的31/31不是图像推理成绩。

将S2MM Binary8二维数组交给regions后再次threshold128是幂等操作；若PL已做形态学则PS必须morph_mode0，避免重复滤波。GT身份/未来帧/速度不得喂给Tracker；评分脚本能读GT不代表其关联器使用GT。实板流接入需新增明确的frame_id和所有权管理适配，不以离线CLI代替板端实现。

## 补充：三帧入口为何不能直接用于新boot

`closeout_worker.py:24`硬编码旧BOOT；`:143-144`验证manifest；`:230-252`拒绝父terminal-fail并要求旧单帧/Overlay/MMIO成功receipt；`:409-421`再次检查实时ENV boot和50MHz身份。`closeout_session.py:47-68`拒绝终态与已消耗模式。原本“286项离线通过”不证明这个入口能跨boot自动恢复。

新boot下的旧Guard已只读归档于 `data/results/wo0014-coldboot-closure-20261015/auth-attempt-02/baseline.json`。恢复需要单独设计和验证；本轮未绕过任何上述检查。
