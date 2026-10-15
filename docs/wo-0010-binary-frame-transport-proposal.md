# WO-0010 · 临时 Binary8 帧到 PS 的接口提案

状态：**原则用途已获用户批准；具体接口选择、DMA配置、驱动和生产接入未获确认，均未实施。**
日期：2026-10-13。临时通路用于参考验证与工程兜底，不替代正式 PL 特征提取目标。
不更改 `docs/02_interfaces.md` 或把像素帧当作接口 B 稀疏特征记录。

## 1. 推荐的候选与待确认选择

优先评估沿用视频 SOF/EOL 的 video frame buffer/VDMA 接口；若采用普通 AXI DMA，则另做
视频流→完整帧包适配和反向回放适配。两种方案仅选择一种验证，不在本轮接入。
普通 DMA 也可按行发包后由 PS 重组，但240行的描述符、归属和完成合同复杂，不作为首版默认建议。

当前模块：TUSER[0]=SOF，TLAST=EOL。普通 AXI DMA 的一次包用最终数据拍的 TLAST 结束，
因此不能把原 EOL 原样直连后假设一次接收就得到320×240完整帧。
VDMA可使用TUSER[0]作帧同步。参见
[PG021 S2MM](https://docs.amd.com/r/en-US/pg021_axi_dma/S2MM) 与
[PG020 Frame Sync](https://docs.amd.com/r/en-US/pg020_axi_vdma/Frame-Sync-Options)。

## 2. 逻辑帧合同（提议，不是已冻结字节布局）

- 格式：Binary8，00背景、FF前景；首版宽320、高240，逐行扫描，每行320有效字节，payload=76800B。
- 内存数组：`uint8[height,width]`；stride显式给出，不允许把含padding的整块缓冲误作连续像素。
- 当前PC参考接受Gray8 PNG并本地阈值化；未来入口为完整Binary8帧时应直接提取区域，避免再次阈值/重复Morph。
  该入口适配尚未实现，不能把当前CLI称为真实DMA接收器。
- 元数据单独绑定同一buffer slot：采集epoch、原始frame_id、捕获时间/其单位、W/H/stride、格式、payload有效长度、
  overflow/reset/partial/error、threshold/Morph配置版本，必要时调试CRC。
- 元数据的总线、CSR/描述符位置和位打包均待确认；这些候选字段不擅自加入接口B或其既有timestamp布局。
- frame_id按采集帧递增，掉帧必须留下间隙，不能改成“收到的第几个帧”。本参考用frame_id差做预测时间。
  reset/回绕通过epoch或明确的序列重建识别；长期累计计数如何保留需单独定义，不能默默清零/重复计数。
- 只提交完整、归属一致的帧给区域/跟踪器。长度、尺寸、ID、状态任一错误都拒收，并计入frame_drop/原因。
  帧中失败不能把半帧特征递交给计数器。

普通DMA适配提议：

```text
PL内部视频TLAST：每行最后一个像素
DMA包TLAST：仅最后一行最后一个像素
原SOF/EOL：适配内部校验/计坐标；不能假定S2MM把TUSER存入像素内存
实际收到字节数：必须等于W*H，或与明确stride/descriptor策略吻合
MM2S反向回放：根据确认W/H重建SOF与每行EOL，不能只用DMA包末TLAST
```

新SOF出现在未完成帧中、缺EOL、行长/行数不符、重置或overflow时，必须中止/标坏帧；
适配器如何排空和恢复到下一完整SOF、DMA超时后如何取消buffer所有权，均需独立验证。
现有Morph只承诺正常光栅与复位恢复，不能将其仿真断言误当成已存在的坏帧修复器。

## 3. 缓冲所有权与缓存一致性

候选采用双/三缓冲并有明确所有权，不由“用了DDR”自动保证一致性。

```text
FREE → PS准备/提交 → DEVICE_OWNED → 完成且状态合法
     → CPU同步与只读处理 → 释放 → FREE
```

- DEVICE_OWNED期间PS禁止读写该槽；PS处理时设备不得覆盖同槽。
- TX在设备读取前执行平台要求的cache clean/flush和提交顺序保障。
- RX提交前避免遗留脏cache覆盖设备写入；DMA完成后按平台要求执行device→CPU同步/invalidate，再读取。
- 不假设内存coherent；具体PYNQ分配器/驱动是否已代做同步，需按目标板软件版本核实。
- base address、stride和allocation按cache line/总线要求对齐；防止同cache line上的邻接CPU对象被误失效。
- DMA完成、有效长度、元数据和slot generation一致才可交付；旧完成回调不得提交已重用的新槽。
- PS慢于输入时，选择完整帧丢弃策略并报告，不覆盖正在处理的帧，也不构造混合帧。
- dropped frame_id仍保留时序间隙；本参考算法不会在缺少观察时凭预测发计数事件。

缓存同步与cache line约束参考 [Linux DMA API HOWTO](https://docs.kernel.org/core-api/dma-api-howto.html)；
具体PYNQ软件栈是否代做同步仍须按实际版本核实。
以上是接口设计要求，不是本轮已验证的PS缓存行为。未调用真实DMA或MMIO，未配置overlay/IP，未测ARM耗时。

## 4. 验证矩阵与现有证据边界

| 阶段/用例 | 要求与判据 | 本轮状态 |
|---|---|---|
| 普通完整320×240帧 | 76800有效字节、正确配置与唯一元数据 | 待实现适配后验证 |
| 误用EOL为DMA包末 | 第一行完成不能被当作整帧；长度错误拒收 | 已确认风险，未硬件测试 |
| SOF/EOL错位、坏长度、reset半帧 | 不计数；恢复下一完整帧并记录原因 | 待适配器仿真 |
| downstream随机stall、尾行刷新 | markers稳定且完整排空 | 现有Morph84项通过；不代表新适配器通过 |
| 重复/倒序frame_id | 不改变已累计结果 | 新Tracker测试已验证特征入口拒收 |
| 帧号跳跃、缺失观察 | 预测使用elapsed帧数，不预测计数；短失踪可恢复 | 新参考测试与H3评估通过；有已知能力边界 |
| 两/三buffer切换、旧完成回调 | 无跨槽元数据串帧、无设备覆盖CPU槽 | 待驱动模型和板端验证 |
| 未invalidate/CPU脏cache注入 | 应发现旧帧或污染；正确同步后CRC/像素一致 | 只能在目标板验证，未执行 |
| packet EOF、TKEEP、最后一拍、stride | byte count和frame framing准确，SOF/EOL回放对齐 | 待选DMA方案后定义与测试 |
| 持续至少30fps与过载 | FPS/延迟分位/CPU/队列水位/drop计数完整 | 未上板，不用PC时延替代 |

## 5. 需要确认的事项

1. video frame path/VDMA，还是普通AXI DMA+明确帧包适配。
2. 元数据位置、frame_id/epoch/捕获时间定义、stride与像素buffer布局、bad-frame状态编码。
3. 缓存同步由何层负责、目标板运行环境版本、缓冲数、丢帧和超时恢复策略。
4. 对临时调试帧通路的接口/ADR记录；它不承诺生产接口B职责已经改变。

批准具体方案前，只保留本候选设计和验证矩阵。原正式接口、RTL与PS/PL分工保持不变。
