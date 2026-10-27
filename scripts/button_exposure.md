# 板载按键调曝光

独立相机调光工具，使用 PYNQ-Z2 基础设计的 `btns_gpio`；不接入计数程序。

- BTN0：曝光减少一个 V4L2 单位，即按标准换算的 0.1 毫秒。
- BTN1：曝光增加一个单位。
- 50 毫秒消抖；每次完整按下、松开触发一次，长按不重复。
- 两键同时按下忽略，完全松开后才允许下一次调整。
- 从相机当前曝光值开始，不重置曝光或修改增益；必须已经处于手动曝光。
- 到驱动上下限不继续写入；命令失败或回读不一致时退出并在日志记录错误。

该程序不会加载 FPGA 设计。启动前必须由操作员授权切换到基础设计，
并核实当前全局状态记录中的 bitfile 路径和哈希与指定基础设计一致。
WO-0015 计数设计没有按键接口，不能直接运行本程序。
后续恢复 FPGA 计数前，应先停止按键程序，并按正式计数准入流程恢复匹配的 Overlay。

板端在已配置 XRT 的环境中运行（需要相机及 FPGA 访问权限）：

```bash
cd /tmp
sudo env XILINX_XRT=/usr OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  /usr/local/share/pynq-venv/bin/python -u \
  /home/xilinx/lighting-tuner-20261027/button_exposure.py \
  --base-bitfile /usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq/overlays/base/base.bit \
  --state-file /usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq/pl_server/global_pl_state_.json
```

程序出现 `READY` 后完全松开按键，再开始调整。按 SIGTERM/Ctrl+C 停止时保留相机参数。
网页调光台本身未启用硬件参数滑块，页面默认曝光数字不是本程序的实际回读。
实际值以本程序日志和 `v4l2-ctl --get-ctrl=exposure_time_absolute` 为准。

离线逻辑验证：`python -m unittest discover -s scripts -p test_button_exposure.py`。
离线测试不替代操作员按下实体按键后的硬件验收。
