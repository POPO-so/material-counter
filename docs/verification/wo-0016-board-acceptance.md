# WO-0016 板端验收记录

## 当前状态

**Stage A 30 帧单次尝试失败，完成帧数为 0；WO-0016 板端计数验收未通过。** 运行进入了 PYNQ、Overlay 只读核验和 CMA 分配阶段，但在首帧编码时异常，尚未启动 S2MM/MM2S 传输。一次性 Guard 已消耗；不重试、不清除 Guard、不重载 Overlay。

## 前置版本问题及修复

第一次纯软件预检在导入 PYNQ 前拒绝：旧 board_preflight.py 绑定 Boot 2cc2ab91-2e3f-450e-b9e7-361ec38e2815，与当前板端 Boot f8b24aee-ed7e-430a-b3f3-d26056746bfd 不符。随后从板端已验证隔离目录取回规范源码：

| 依赖 | SHA-256 | 对应 Manifest |
|---|---|---|
| board_preflight.py | f6556d811f2b4488b1ad471ea5d8ad565b74ae194b6db0b4356ae907e05de2d6 | 2c24709bbe60711c095b5ccbdf4b3582e93e65d98a9538ad2b413136ad942e8b |
| camera_dma_oneshot.py | 5b9684f4b02988e8a822c36ef0ac7faec78c0a7980f2fca1574afd3027c9df28 | 同上隔离包版本 |

board_once.py、camera_adapter.py、prepare_vectors.py 也与隔离包逐字节一致。源码已同步到本地 scripts/wo0015/ 和板端 /home/xilinx/wo0016/wo0015/，没有修改或放宽哈希门禁。源码对齐提交为 b1cf74a3934ea10a883e1707a87bf830706015b1。

## 板端只读预检和部署

| 检查项 | Stage A 启动前结果 |
|---|---|
| /proc/sys/kernel/random/boot_id | f8b24aee-ed7e-430a-b3f3-d26056746bfd |
| /proc/meminfo CmaFree | 78864 KiB，高于 32768 KiB |
| fuser /dev/video0 | 无占用进程输出 |
| 相机控制 | auto_exposure: 1 (Manual Mode)；exposure_time_absolute: 50 |
| Manifest SHA-256 | 2c24709bbe60711c095b5ccbdf4b3582e93e65d98a9538ad2b413136ad942e8b |
| BIT / HWH SHA-256 | a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420 / bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6 |
| WO-0015 Stage 1 收据 | OVERLAY_LOAD_PASS，相同 Boot、Manifest、BIT、HWH |
| WO-0016 Guard / 输出文件 | 启动前均不存在 |
| WO-0016 隔离依赖 | 五个 Python 文件 SHA-256 与板端 WO-0015 隔离包一致 |

## Stage A 单次尝试（2026-10-18）

使用 sudo -n env XILINX_XRT=/usr、板端 PYNQ Python、--max-frames 30、获批 Manifest SHA 和全部单次授权开关。运行器通过 Overlay(download=False) 复用当前 Overlay，没有请求下载 BIT。

运行证据确认：

- status=BOARD_STREAM_FAILED、board_accessed=true、pynq_imported=true，frames_processed=0。
- Checker/Packer 签名分别为 0x574f3134 / 0x574f3135；AFI1 精确回读通过：RD=0x00000001、WR=0x00000f01。
- 两组 TX/RX CMA 物理范围门禁通过。分配后 CmaFree=45324 KiB，高于 32,768 KiB；异常清理后 JSON 记录 CmaFree=35744 KiB、buffers_released=true，未进入 fail-hold。
- 首帧在构造数据包时抛出 TypeError。调用顺序表明异常发生在 before_transfer 及 transfer_async 之前，因此没有启动任何 DMA 帧传输。

    File "/home/xilinx/wo0016/stream_counter.py", line 394, in run
        packet = self.packet_encoder(gray, frame_id)
    TypeError: gray320x240_to_rgb_packet() takes 1 positional argument but 2 were given

原因是 WO-0015 规范适配器将 frame_id 定义为仅限关键字参数，而 WO-0016 通用流把它作为位置参数传入。离线回归测试已复现并修复该接口调用；这不能改变本次板端结果，也不授权重试。

板端 JSON 保存在 /home/xilinx/wo0016/stage-a-30frames-result.json，SHA-256 为 fa53c93db5eae4831f328b80c43a24c32c0ea70d19a365cb6e0c10acb993a651。已消耗 Guard /var/lib/wo0016/stream-counter-consumed.json 的 SHA-256 为 44e312a27b793ab276f8a24c30ba6d1ef6a4f8fddb530c6f272244d7c0ffc671。该 JSON 是本次失败的原始证据，不是通过报告。

## 未完成的物料验收

| 记录项 | 结果 |
|---|---|
| 连续处理帧数 / 30 帧目标 | 0 / 30 |
| DMA / Checker / Packer 每帧通过数 | 0；首帧编码失败，DMA 未启动 |
| 总计数、人工真值、计数误差、准确率 | NOT RUN |
| M6 螺母、垫片及混合物料样本 | NOT RUN |
| 相机实际输入 FPS、端到端处理 FPS、浏览器预览 FPS | NOT RUN |
| 传送带速度、照明、视野及物料过线工况 | NOT RUN |
| 浏览器预览中的实际计数验证 | NOT RUN |

下一次板端运行需要修正版脚本部署、由架构师明确授权的新一次性执行安排，以及不绕过现有已消耗 Guard 的新受控验收流程。必须重新记录独立人工真值；不得把本次 0 帧结果、Mock 分数或 WO-0015 DMA 通过记录写成 WO-0016 计数准确率或实板 FPS。
