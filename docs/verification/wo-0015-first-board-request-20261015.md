# WO-0015 首次上板受控实验申请

状态：2026-10-15 队长已正式重授权当前 boot；旧 boot 准入熔断记录保留于 `data/results/wo0015-first-board-20261015/`。请求队长批准在同一 boot 下按顺序执行一次 Overlay 加载与签名核对、一次 50 MHz 单帧 S2MM 闭环、一次三帧连续闭环。单帧成功后才进入三帧，总计最多四帧；各阶段各一个固定 Guard，无自动重试。队长可一次批准整个顺序，仍须逐步审核上一阶段 PASS，任一异常立即停止推进。

## 固定身份与前置证据

- boot 必须是 `822a9c29-37c0-438c-81c4-da998d57073b`；变化即停止，不能用新 boot 绕过授权。
- 构建 `wo0015-hw-50mhz-20261015`，50 MHz；BIT SHA-256 `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420`；HWH SHA-256 `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6`。
- XSim A/B/C/D、四个随机种子、额外 guard 测试和 Python/Mock 通过；原始日志见离线验收报告。
- WNS +9.009 ns，TNS=0，建立/保持/脉宽 failing endpoints 均为 0。请求接受已披露的厂商 IP DRC/Methodology 警告后进行受控验证，不将这些告警豁免为零，也不将时序通过等同实板通过。

## 准入与停止条件

1. 仅在队长批准后恢复严格指纹 SSH；密码只在原生隐藏提示输入。包上传至新的独立目录，不覆盖既有实验。以下命令是板端终端命令，本轮未执行。
2. 默认离线命令先验证 manifest 和工件；真实阶段还会只读核对实时 boot、CmaTotal/CmaFree、设备树 CMA 范围、进程 FD/maps、manager operating/flags=0、PYNQ global state、dmesg。历史 CmaFree=76,508 KiB 不替代实时检查。两个缓冲页对齐需求合计最低 304 KiB；实际分配仍须成功且完整落入真实 CMA 和 HP 可访问窗口，不能仅凭余量放行。
3. Overlay 前 global shutdown 列表必须空；DMA 前已加载 BIT 路径/hash 必须匹配。DMA 基地址 0x40400000、Checker 0x43c20000，两签名地址为 0x43c20004/0x43c20060。Overlay 阶段只下载并读取这两个签名，不启动 DMA。
4. 两个缓冲各只分配一次：TX230432，8字节对齐；RX76800，4字节对齐；物理范围不重叠，位于各自 HP DDR 窗口内。每帧先 RX Run/DA/LENGTH，再 TX Run/SA/LENGTH；不重置 DMA，不重载 Overlay。
5. 每帧必须双端 Idle+新 IOC 且无错误、S2MM actual length=76800、Packer=76800像素/19200拍/done、Checker=76800像素/240EOL/正确前景校验和、回读零差异；运行结束 CmaFree 恢复原值。前景数由二值 checksum/255 得到。全部后检成功才发 PASS 收据。
6. 固定 `/var/lib/wo0015-s2mm` 下三个 consumed Guard，三帧要求单帧 PASS 收据。不得删除、改名、换 Guard 路径或用新 manifest 重试。错误留下 terminal marker，后续阶段被阻断。
7. DMA 可能在途时进入 FAIL_HOLD，保留 TX/RX 对象和进程，不释放、不复位、不重试；证据写入失败也不得跳过保持。5秒参数只约束能够返回的轮询，不能中断底层同步 MMIO 卡死。遇此情形保留现场，等待队长另行裁决。

## 板端命令

最终打包目录、manifest 固定摘要及可直接粘贴的三阶段命令在本文件下方列出。必须按顺序单次执行；禁止用循环或无条件串联命令自动推进。每次人工核对 JSON 状态和后检再执行下一步。

本地交付包：`data/results/wo0015-first-board-bundle-20261015.zip`。

- ZIP SHA-256：`873d245ad92926b00e9f57b2793220ab14dca0c287f49c213d7af5d36dd14879`
- `run-manifest.json` SHA-256：`817d09ac508e07ab9c156541a0e39cf8c71d361a7dfaaba67951729dd907c674`
- 本地解包目录：`data/results/wo0015-first-board-bundle-20261015/`，内含两个执行器源码、同名 BIT/HWH、三组 packet/golden、build/XSim 摘要及 `SHA256SUMS.json`。
- 已用包内脚本与上述真实工件在 PC 上执行 offline 和 mock，分别为 `OFFLINE_PREFLIGHT_PASS`、`MOCK_DMA_ONCE_PASS`，均 `board_accessed=false, pynq_imported=false`。Mock 的 CmaFree/DMASR 值只是模型状态，不能充当板卡实测值。

批准后将包内容放入 `/home/xilinx/wo0015_first_board_20261015`。先在板端原生终端设置固定路径与身份（此处不含网络连接命令）：

```bash
WO=/home/xilinx/wo0015_first_board_20261015
PY=/usr/local/share/pynq-venv/bin/python
MANIFEST_SHA256=817d09ac508e07ab9c156541a0e39cf8c71d361a7dfaaba67951729dd907c674
sha256sum "$WO/run-manifest.json" "$WO/wo15.bit" "$WO/wo15.hwh"
sudo env XILINX_XRT=/usr "$PY" -u "$WO/board_once.py" --mode offline --manifest "$WO/run-manifest.json" --manifest-sha256 "$MANIFEST_SHA256" --evidence-json "$WO/offline-result.json"
```

阶段 1：一次 Overlay 加载和两个签名只读核对，要求 `OVERLAY_LOAD_PASS`：

```bash
sudo env XILINX_XRT=/usr "$PY" -u "$WO/board_once.py" --mode overlay-only --manifest "$WO/run-manifest.json" --manifest-sha256 "$MANIFEST_SHA256" --execute --authorize-stage overlay-only --approve-once --timeout-s 5 --evidence-json "$WO/overlay-only-result.json"
```

阶段 2：仅在阶段 1 PASS 后执行一次单帧，要求 `DMA_SINGLE_PASS`：

```bash
sudo env XILINX_XRT=/usr "$PY" -u "$WO/board_once.py" --mode dma-single --manifest "$WO/run-manifest.json" --manifest-sha256 "$MANIFEST_SHA256" --execute --authorize-stage dma-single --approve-once --timeout-s 5 --evidence-json "$WO/dma-single-result.json"
```

阶段 3：仅在阶段 2 PASS 且后检通过后执行一次三帧，要求 `DMA_THREE_PASS`：

```bash
sudo env XILINX_XRT=/usr "$PY" -u "$WO/board_once.py" --mode dma-three --manifest "$WO/run-manifest.json" --manifest-sha256 "$MANIFEST_SHA256" --execute --authorize-stage dma-three --approve-once --timeout-s 5 --evidence-json "$WO/dma-three-result.json"
```

以上三条是待批准操作，并未在本轮执行。解释器路径、严格 SSH 指纹、只读环境准入任何一项不匹配时停止，不能临时替换环境或关闭门控继续。

## 2026-10-15 retry1 重授权配置

队长正式批准将 boot 绑定更新为 `822a9c29-37c0-438c-81c4-da998d57073b`。原 ZIP 保持原摘要，解压后仅应用隔离的 `board_preflight.py` 授权常量、`run-manifest.json` boot 字段及由此派生的摘要更新；功能代码、工件、Goldens、打包逻辑、Guard 与判定标准不变。原文以上历史命令的旧 manifest 摘要仅供追溯，本次使用以下新绑定：

- 新部署目录：`/home/xilinx/wo0015_first_board_retry1_20261015`。
- 新 manifest SHA-256：`ceac16e28b968bc28d3e4f30828402447c9dbe8c566f9800a789478a435a3d8b`。
- 配置变更逐项证据：`data/results/wo0015-first-board-retry1-20261015/authorization-rebind.json`。
- 三阶段命令、一次只读初态快照与逐步 PASS 审核均记录在本次结果目录；不得对旧命令直接使用旧 manifest 摘要执行。

本次实际三阶段命令（传输层通过原生隐藏输入提供 sudo 凭据；每条分别核验后执行）：

```bash
sudo -S -p "" env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -u /home/xilinx/wo0015_first_board_retry1_20261015/board_once.py --mode overlay-only --manifest /home/xilinx/wo0015_first_board_retry1_20261015/run-manifest.json --manifest-sha256 ceac16e28b968bc28d3e4f30828402447c9dbe8c566f9800a789478a435a3d8b --execute --authorize-stage overlay-only --approve-once --timeout-s 5 --evidence-json /home/xilinx/wo0015_first_board_retry1_20261015/overlay-only-result.json
sudo -S -p "" env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -u /home/xilinx/wo0015_first_board_retry1_20261015/board_once.py --mode dma-single --manifest /home/xilinx/wo0015_first_board_retry1_20261015/run-manifest.json --manifest-sha256 ceac16e28b968bc28d3e4f30828402447c9dbe8c566f9800a789478a435a3d8b --execute --authorize-stage dma-single --approve-once --timeout-s 5 --evidence-json /home/xilinx/wo0015_first_board_retry1_20261015/dma-single-result.json
sudo -S -p "" env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -u /home/xilinx/wo0015_first_board_retry1_20261015/board_once.py --mode dma-three --manifest /home/xilinx/wo0015_first_board_retry1_20261015/run-manifest.json --manifest-sha256 ceac16e28b968bc28d3e4f30828402447c9dbe8c566f9800a789478a435a3d8b --execute --authorize-stage dma-three --approve-once --timeout-s 5 --evidence-json /home/xilinx/wo0015_first_board_retry1_20261015/dma-three-result.json
```

补充初态只读核对：Checker/Packer 全寄存器与 DMA CR/SR/地址/长度，要求 Halted=0、Err=0；首次 Idle=0 可接受。此快照不写寄存器、不启动 DMA。RTL 无单独版本寄存器，以 WO14 Checker、WO15 Packer 签名记录设计身份。

## retry1 执行结果

新 boot 准入与 BIT/HWH 板端摘要通过；授权配置 SFTP 覆盖遇到 root 文件权限拒绝，已 FAIL_STOPPED，未执行 Overlay 或 DMA。完整证据见 `data/results/wo0015-first-board-retry1-20261015/verification-report.md`。
