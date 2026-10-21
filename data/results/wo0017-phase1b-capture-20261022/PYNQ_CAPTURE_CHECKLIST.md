# PYNQ-Z2 实板采集前检查清单

此清单是后续独立硬件试验的准备材料；WO-0017 Phase 1B 软件开发和离线测试期间未连接、重启、部署或操作实板。实板试验需另行获得用户明确许可，并沿用现有 WO-0016/Stage B 安全流程。

## 许可与运行状态

- [ ] 收到本次实板采集的明确授权；本清单本身不是授权。
- [ ] 确认目标板、当前 boot ID、已加载 Overlay 身份、READY Guard、CMA 水位、DMA 空闲状态和现有一次性许可均通过当前安全流程；不绕过或删除任何 Guard。
- [ ] 使用当前分支与部署文件哈希；在单次许可中绑定 capture 目录、容量上限、队列深度 2、计数参数和最大帧数。
- [ ] 预览/相机检查与物料采样分开；采样前确认摄像头当前报告的模式、帧率和 320×240 输入变换。

## 存储与采集数据

- [ ] 新建唯一、空的 capture 目录；禁止复用、覆盖或清理任何历史 `data/results/` 归档。
- [ ] 计算预计输出约 `frames × (153,600 + metadata allowance)` 字节；确认磁盘可用空间高于配置上限及必要余量。
- [ ] 输出 `manifest.json` 的 `capacity`、`max_bytes`、`free_bytes_at_preflight` 和 `expected_frames` 与本次帧数一致。
- [ ] 运行时仅在所有现有授权/READY Guard/硬件静态检查通过后开始 DMA 和相机流。
- [ ] 结束后要求 Runner 与数据集 manifest 都为完整状态，Frame ID 从 0 连续到 `expected_frames-1`，无溢出/缺帧，数组和记录哈希可重验。
- [ ] 如果状态为 `INCOMPLETE`、`INCOMPLETE_FAIL_HOLD`、manifest 仍为 `IN_PROGRESS`、队列溢出、写入错误或帧数不匹配，保留数据目录与运行证据并停止；不要把它标成有效样本，也不要自动重试同一个一次性许可。

## 独立真值

- [ ] 运行前指定观察员/独立视频来源，不使用 Web HUD、算法 Track ID 或累计数作为 Ground Truth。
- [ ] 对 A/B/C/D 试验分别记录预期投放数和实际进入检测窗口数。
- [ ] 结束后逐帧人工复核实体与 Track 的身份关系；不能独立确认时保留 `NOT VERIFIED`。
- [ ] 将完成的 Ground Truth 保存为新数据集内 `ground-truth.json`，注明来源、审核人及证据位置。
- [ ] 运行离线 Baseline 回放，复核实体报告；如果后续添加 V2，用完全相同的完整 Mask 数据集运行。

## 限制

- 本采集能够保存相同软件 slot/Frame ID 对应的输入和 S2MM 输出，并记录 DMA 状态及时间；FPGA mask 未回传硬件 Frame ID，因此不能宣称由硬件独立证明帧关联。
- 板端内存、存储吞吐和 capture CPU 开销尚未测量。若容量/速度不足，当前实现应使样本失败并报告，而不是跳帧。
