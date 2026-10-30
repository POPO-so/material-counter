# G2 监测增量制品与板端复验

结论：新增 RLE 64 位活动/握手/反压锁存监测的候选制品完成 G2 复验。随后同一 BIT/HWH 的 [G2.5 并发门禁](../standalone-20261030-g25-concurrency/README.md) 已放行。G3/G4 仍须单独验证；Route、视频源和正式接口仍未冻结。

## 制品与构建身份

| 文件 | SHA-256 |
|---|---|
| `export/g2-integrated.bit` | `79609f9a60c3e727b5f2f54a8d639a464a098715fb5eb28d294646a9735948e0` |
| `export/g2-integrated.hwh` | `986de17a030540adc9e892cb6cd7b146ead7b5ddcf0aab2da80a9c0bf5027243` |
| `export/display-manifest.json` | `2872e1598b3c3ab7ccb5d383f5cee6a8822dbd91eb0f198098eca067489a2966` |

Vivado 2026.1 build 6511674，xc7z020clg400-1，display_wrapper。RTL 监测增量提交 `229487a`；构建来源及源/报告摘要见 `export/build-manifest.json`、`rtl/` 和 [决定记录](decision-record.md)。旧 G2 制品及其证据未覆盖。新寄存器是候选诊断接口，不改变 RLE_PACKET_V1 数据包或原控制地址。

## 静态实现与回归

- Setup WNS **+0.188 ns**、hold WHS **+0.029 ns**，TNS/THS 和 failing endpoints 均为 0。
- LUT 12,221，FF 16,174，BRAM 4.5 tiles，DSP 9；较旧 G2 增加 153 LUT、737 FF，BRAM/DSP 不变。
- 8 个原 640×400 RTL 场景通过；新增断言验证输入 64,008 beats、输出 beats、忙时锁存、序号、非零输出反压及快照稳定。RED 轮先以 `AXI write rejected 0000003c` 复现缺少监测接口，GREEN 日志在 `rtl-green/xsim/`，黄金输入可由 `scripts.rle.sim_vectors` 重建并与 `vector-sha256.json` 核对。
- **本次实际集成 pipeline IP 的综合后功能网表 8/8 通过**，含新增监测断言。复现入口 `scripts/rle/run_full_pipeline_netlist.tcl`；`full-netlist/` 保留网表、实际测试台及逐场景日志。范围不含 PS、DMA/HP 与 HDMI 的顶层联合仿真。
- frame monitor RTL 在首次 fresh 工作目录中曾出现 `full-resolution frame was not accepted`；增加失败诊断打印后复跑通过，再用同一快照独立重复 3 次均通过。未修改 monitor RTL，该次测试台失败未能稳定复现，保留为测试稳定性限制，不能说从未失败。该修改只改善失败现场输出。`monitor-sim/` 保留通过及重复日志。

## 告警审查

DRC 33 项（30 Warning、3 Advisory），methodology 8 Warning，无 Error/Critical。CDC 与旧 G2 的路径、类别和数量一致：CDC-3 Info 102、CDC-6 Warning 1、CDC-15 Warning 965。965 条仍属于显示 monitor 的 mailbox→snapshot 稳定数据路径；RLE 新计数在同一 50 MHz 域，没有新增跨域路径。残留厂商 VTC CDC-6 和已解释的 mailbox CDC-15 均未被改写成零告警。

综合日志记录 566 warnings、0 critical、0 errors；实现日志最终为 7 warnings、1 critical、0 errors。该 critical 是载入厂商通用 RGB2DVI 时钟约束时的 Timing 38-249：其假设内部 PixelClk×5，而本工程使用独立 axi_dynclk 的 MMCM 串行输出。后续 `display_timing.xdc` 按实际 CLKOUT0 重新约束；最终 [时钟属性读回](evidence/final-clock-readback.txt) 为 SerialClk period 2 ns，SOURCE 为动态 MMCM CLKOUT0，最终 routed timing 满足约束。它与旧 G2 已解释的初始/覆盖告警同类，未隐藏原日志。

## 板端复验

板卡 192.168.2.99 / pynq，boot ID `e48ff153-bd85-4dd6-98f6-438bd51da08d`；新部署目录 `/home/xilinx/standalone-g25-20261030`。加载前核对旧 G2 身份、RLE idle、VDMA halted、无设备 owner 和两把互斥锁，再一次性加载新 BIT。隔离模块导入未导入 PYNQ 或打开设备，82 个部署文件的哈希核对通过；原始 [准入记录](board/admission.json) 及实际 [准入脚本](board/admit_new_bit.py) 保留。

FCLK0/1/2 = 50/100/142.857143 MHz；HP0 读 64 位、HP1 读/写 32 位、HP2 读 32 位；新 pipeline 签名/640×400 几何/16384 容量读回正常。监测总时钟在准入后增长，其余计数初值为 0。

- [相机显示冒烟](board/camera-smoke.json)：STOPPED，62.184 s，提交/识别 670 个新帧身份，相机已释放，VDMA halted。使用 G1.5 显示 runner，不含 RLE 计数；启动首个有效身份前有 715 次 invalid 扫描，之后没有持续增加。它不等于 G3 性能门禁。
- [RLE+HDMI 像素冒烟](board/pixel-smoke.json)：46 帧逐像素验证通过，动态显示，0 invalid RLE 帧，正常 STOPPED。输入硬件 beat 差值 2,944,368 = 46×64,008，输出差值与实收长度一致。反压可读，输入 stall 8,832,042 cycles、输出 stall 0。
- 随后的 180 s 压力及独立终态见 G2.5 报告：RLE DMA idle、VDMA halted、无设备 owner，新 BIT 身份不变，无新增 external abort。RLE DMA run bit 仍可为 1，状态称 idle，不称 halted。

## 复现和边界

使用原 G2 创建/构建入口和已锁定 PYNQ/HLS IP 依赖；`build-logs/` 保留本次完整构建、综合、实现和网表日志。工具调用曾超过 300 秒返回窗口，后台构建/仿真继续；两次均由实际日志及 MCP 最终状态核对为退出码 0，不把工具超时当作构建失败或通过。

`software-tests.txt` 记录该轮 286 项软件回归；之后新增交付和证据导出用例的结果归后续记录。`offline-native/rle_components_v2.so` 是目标板已编译的 ELF32 little-endian ARM 库，SHA-256 `a04fdd74bf0791358b803271757bdc89317fd8852231e8c572705f266db4f0f7`；运行时不编译或下载。

`staging-index*.json` 对应不同部署修订，不能混用：早期六轮压力的 runner 来源和确切代码副本见 G2.5 `source-provenance.json`；后续索引用于增加内存诊断后的部署。任何包完整性校验都不能替代 G3/G4、实物真值或 30 FPS 相机验收。
