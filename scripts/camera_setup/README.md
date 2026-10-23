# PYNQ-Z2 相机曝光按键（WO-0017 候选）

本功能在现有 WO-0015 DMA 与图像流水线基础上增加两位 AXI GPIO，采集板载 BTN0/BTN1，并通过 V4L2 调整 USB 相机曝光。图像处理、DMA 和相机预览代码保持原路径。

## 按键行为

- BTN0 每按一次将曝光减少 0.5 ms；BTN1 每按一次增加 0.5 ms。
- 长按不连发；按键经过去抖，两个键同时稳定按下时忽略该边沿。
- 程序读取相机当前手动曝光范围、值和步进；到达上下限时钳位。每次修改后回读确认。
- 程序只接受已经处于 Manual Mode 的相机，不会替用户切换自动曝光模式；启动时只读，按键后才写曝光。
- 相机的 V4L2 absolute exposure 以 100 μs 为单位，0.5 ms 对应 5 个单位。若相机硬件步进不能表示精确的 5 个单位，程序会拒绝修改。

PYNQ-Z2 的按钮为按下时高电平：BTN0 使用 PL 引脚 D19，BTN1 使用 D20；候选工程将其约束为 LVCMOS33。按钮位 0/1 直接接入 `exposure_buttons_gpio` 的两个输入。

## Overlay 与现有画面的关系

该功能不加载 PYNQ BaseOverlay。WO-0017 从现有 WO-0015 项目生成新 bit/HWH：保留原 PS、DMA、Gray/Binary 图像路径和寄存器布局，只在控制 AXI-Lite fabric 增加一个 GPIO 目标，并增加 BTN0/BTN1 引脚。

初次启用时，新 bitstream 必须在板卡空闲时写入 PL；PL 更新期间现有 PL/DMA 工作会暂停，所以不能在采样或 DMA 传输过程中切换。之后此按键程序通过 `Overlay(..., download=False)` 读取元数据，并要求匹配的 WO-0017 bitstream 已经加载；程序本身不会重载或替换 PL。USB 相机预览由 PS 上的 V4L2 程序读取，曝光工具不停止或重开预览流。

## 板端启动

将此目录以及配对的 `wo17.bit`、`wo17.hwh`、`pair-manifest.json` 放在板端。确认当前 PL 已加载该 WO-0017 bit/HWH 对后，在 PYNQ Python 环境启动：

```sh
sudo -n env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 pynq_exposure_buttons.py \
  --bit /home/xilinx/wo0017/wo17.bit \
  --hwh /home/xilinx/wo0017/wo17.hwh \
  --manifest /home/xilinx/wo0017/pair-manifest.json \
  --device /dev/video0
```

启动前只读相机控件和当前 Overlay 身份；曝光仅由 BTN0/BTN1 触发。`Ctrl+C` 停止工具，不释放或更改已加载 Overlay，也不关闭相机预览。

## 当前状态与证据边界

WO-0017 是接口评审前的 Proposed 候选，代码和独立 Vivado 构建脚本已准备；新增的 AXI GPIO/顶层按钮端口仍需按 `docs/02_interfaces.md` 进行 PR、三人确认及 ADR 审批。当前没有 Vivado 构建、bit/HWH 产物或 PYNQ 板端验证；相机曝光、按键边沿和预览保持仍待板上验收。本次没有连接板卡或改变其 PL、相机及预览状态。
