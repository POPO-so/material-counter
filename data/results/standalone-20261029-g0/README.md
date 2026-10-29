# 脱机显示 G0 环境核对（2026-10-29）

原分支 `LJ-01`（起点 `8e95f59`），开发分支 `feat/板端脱机显示`。本记录是环境与依赖证据；G1 实际输出、G1.5 合成和 G2–G4 整机验收仍待完成。

## 板端实测

- boot ID：`e48ff153-bd85-4dd6-98f6-438bd51da08d`，Linux 6.6.10-xilinx-v2024.1，armv7l。
- PYNQ 3.1.1、NumPy 1.21.5、OpenCV 4.5.4、Pillow 9.0.1；程序使用 `/usr/local/share/pynq-venv/bin/python3`。OpenCV 来自系统包，缺少两个 pip distribution metadata；模块本身已成功导入，不据此误判缺失运行库。
- 相机稳定入口 `/dev/v4l/by-id/usb-HSK_USB_Camera_20210616103-video-index0` → `/dev/video0`，实读 V4L2 支持 MJPEG 640×400。
- 中文字体 `/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf`，SHA256 `acb6440a713d880a13a21b468ba7cd43f5a2b2934972e51be791c880730777b8`。
- 视频原生扩展为 `pynq/lib/_video.cpython-310-arm-linux-gnueabihf.so`，不是 `libvideo.so`；最终采集器已按实际名称收集。其哈希、驱动、base BIT/HWH/Tcl 哈希和地址参数在 `board-probe-final.json`。
- CMA 总量 128 MiB；两次采集的空闲值分别为 30,176 / 15,256 KiB，存在可迁移页面造成的动态变化。因此额外执行了 **24 MiB CPU 独占 CMA 分配/释放实测**，成功取得 25,165,824 字节且安全释放，随后 CmaFree 为 33,856 KiB，见 `cma-admission.json`。24 MiB 为暂定显示/RLE CMA 准入预算，不能代替峰值实测。4 槽 + 最多 5 张驱动缓存 + 1 张 CPU 帧约 9.216 MB，另计 RLE、分页和分配器开销；IPC/合成工作区属于普通内存。
- XADC/IIO 可读：最终采集温度约 36.7°C、VCCINT 约 1.023 V、VCCAUX 约 1.802 V。按 Linux IIO ABI 使用 `(raw + offset) × scale`，原始单位分别为 m°C/mV，再换算为 °C/V，保存单调采样时间。[内核 ABI](https://www.kernel.org/doc/Documentation/ABI/testing/sysfs-bus-iio)
- root `/proc` FPGA 所有权扫描无其他持有者、无拒绝访问；RLE DMA 双向状态 `0x1002`（idle）。只读基线见 `baseline-registers.json`，本阶段未加载位流。
- **偏差：**现存 RLE 会话 FCLK0 实读 31.25 MHz，不能沿用历史 50 MHz 宣称；G1/G2 加载新制品后须分别核对时钟。其余 FCLK 实读均 100 MHz。
- UIO0 为 audio-codec-ctrl（0x43c00000），UIO1 为 fabric（0x40000000），驱动均为 `uio_pdrv_genirq`；PL fabric 使用 GIC 61。新候选保留 fabric 地址，避开 audio 区域，实际中断功能留待 G1 验证。
- 用户在本会话确认独立供电、HDMI OUT 已接显示器，可配合按钮、拔插和观察。尚未把这项连接确认当作十分钟输出或冷启动验收。

## 依赖/职责矩阵

| 项目 | 固定来源/职责 | 当前证据 |
|---|---|---|
| 视频驱动/分配器 | 板端 PYNQ 3.1.1，硬件所有者管理 CMA 和 VDMA | 驱动源码、原生库哈希；4 帧槽、5 张缓存；提交后所有权转移 |
| 动态时钟/TMDS/颜色交换 | 官方 PYNQ v3.1 外部 IP | 固定 commit 和 32 个源码文件哈希 |
| 颜色矩阵/像素展开 | 同一官方源码，经 Vitis HLS 2026.1 重建 | C 仿真、综合和 IP 打包通过；寄存器与板端 PYNQ ABI 一致 |
| VDMA/VTC/AXI Video OUT/INTC/GPIO | Vivado 2026.1 内置 IP | 实际 catalog 找到 10/10 所需定义 |
| 约束/按钮/HDMI 管脚 | 官方 PYNQ-Z2 base.xdc | 已纳入源码锁，G1 仅提取所用输出/按钮约束 |
| PS 时钟/HP2/监测器地址 | 新候选显示工程 | [候选设计](../../../docs/superpowers/specs/2026-10-29-standalone-display.md)，须构建与板测验证 |

官方 source commit：`19ed17d4fd4ea71f95a97d30f8ccd0d62b8dbaaa`。板端 `base.tcl` SHA256 与该版本逐字节一致；不能据此推定整张 SD 镜像的来源。

HLS 首轮单独构建的资源估计：color_convert 1053 LUT / 916 FF / 6 DSP、估计周期 4.520 ns；pixel_unpack 943 LUT / 340 FF / 0 DSP、估计周期 3.552 ns。颜色转换 II=1；像素展开报告没有固定函数 II，不能填成已证 II=1。这些是 IP 综合估计，不是集成后的资源/时序结果。

可重建脚本实跑中发现：当输出与 PYNQ checkout 同处 `.runtime` 父目录时，本机 HLS 2026.1 将设计文件保存成错误的相对路径，`csim.mk` 只含 testbench，最终 undefined top。采用独立 `data/results/.../rebuilt-ip` 输出目录后，脚本完成两项 C 仿真、综合及打包，退出码 0；脚本现在在不兼容路径下提前拒绝。失败日志保留在本地，不伪造通过。

## 复现

从仓库根目录运行；源仓库和构建目录必须是新目录：

```powershell
git clone --filter=blob:none --no-checkout https://github.com/Xilinx/PYNQ.git .runtime/pynq-v3.1
git -C .runtime/pynq-v3.1 sparse-checkout set boards/ip boards/Pynq-Z2/base
git -C .runtime/pynq-v3.1 checkout 19ed17d4fd4ea71f95a97d30f8ccd0d62b8dbaaa
.venv/Scripts/python.exe -B -m scripts.standalone.verify_sources --source-root .runtime/pynq-v3.1 --brief
$env:PYNQ_SOURCE = (Resolve-Path .runtime/pynq-v3.1).Path
$env:VIDEO_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
$env:VIDEO_IP_OUT = "$PWD/data/results/<新的G0目录>/rebuilt-ip"
C:/AMDDesignTools/2026.1/Vitis/bin/vitis-run.bat --mode hls --tcl scripts/standalone/build_video_ip.tcl
$env:VIDEO_IP_BUILT = $env:VIDEO_IP_OUT
$env:G0_CATALOG_OUT = "$PWD/data/results/<新的G0目录>/catalog.tcldict"
C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat -mode batch -nojournal -source scripts/standalone/check_catalog.tcl
```

板端将 `probe.py` 放入新证据目录，用 root 和板端 PYNQ Python 执行 `probe.py --output <新JSON>`。程序不导入 PYNQ、不打开 MMIO、不分配 CMA，也不宣称门禁通过。寄存器/所有权额外核对由 Manager 在确认现存位流身份后单独进行。

Vivado MCP 本次启动握手后子进程退出；命令行 Vivado 正常工作，已保留原生日志。后续实际屏幕和缓冲验收不能由 catalog、软件测试或 BD 验证替代。
