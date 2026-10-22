# WO-0017 离线算法、预览与 HUD 改进

日期：2026-10-22。工作区：`C:\wt\w17`，分支 `codex/wo0017-algo-accuracy`。

## 结论

本地实现了 BBox-Gap Union-Find、迟滞带、短时运动预测关联、Gray8 + PL Binary8 Mask HUD、异步预览快照和 8081 许可哈希绑定。代码和安全用例已通过本地 pytest。

**Attempt #5 的 CCL 回放仍为 4 次，未达到用户提出的 5/5 目标。** 现存 165 帧记录不含原始 Gray8 或 Binary8 Mask；Phase 1A 审计也说明 Recovery-2 没有独立逐实体 Ground Truth，配置中的 `expected_count=5` 不能证明 5 颗都进入检测窗口。因此本次不能证明 5/5，也不声称真实物料准确率提升。

Stage B-1 的历史报告记载 115 帧空带、0 次计数，但原始逐帧 JSON 不在当前 worktree 或已检查的证据归档中。本次只用合成全零 Mask 做了 115 帧单元回归；它不等同于历史 B1 回放，也不验证 Capture 写盘期间的板端误报。

## 实现

- `scripts/wo0016/stream_counter.py`
  - 新增按闭区间 BBox 边缘欧氏间距聚类的 Union-Find，阈值可设为 16 px；合并质心按组件面积加权，未合并的原始 CCL 记录仍保留。
  - 保留迟滞越线带；已确认、已武装的 X 轴轨迹可在有限缺帧内按速度预测候选位置。必须重新观察到实体组件才关联或触发事件，推断间隙会写入事件标记，不会凭空生成穿线计数。
  - ROI 行列投影改用 OpenCV reduce，并由 Python 列表转换为结果 tuple，避免逐元素 Python 转换；Foreground 总数从行投影求和。
  - 为预览回调增加 DMA 槽位内的 Gray8 快照；Capture 开启时复用已有 Gray8 快照，不额外分配同尺寸预览副本。
- `scripts/wo0016/capture_dataset.py` / `run_stream_counter.py`
  - 采样开始前用普通 NumPy RAM 预分配 `(N,240,320)` 的 `raw_gray8` 与 `raw_binary8` 数组；每帧完成 S2MM 和状态检查后只用 `np.copyto` 配对拷入，不调用 PYNQ/CMA allocator。
  - 采样循环结束后原子写出 `frames_gray8_binary8.npz`，包含逐数组 SHA-256；另写 `.sha256` sidecar 并在 manifest 记录 NPZ 完整文件 SHA-256。离线 loader 核对完整文件摘要、sidecar、数组摘要以及与逐帧数据的一致性。
  - 预检容量按额外 NPZ 计入，默认上限为 96 MiB，可容纳 220 帧的双数组与逐帧复核记录。
- `scripts/wo0016/mjpeg_preview.py`：将 Gray8 相机帧作为主画面，叠加迟滞带、活动轨迹框与尾迹，并在右上显示 `PL DMA MASK` PIP。JPEG 仍由独立后台线程编码；预览使用最新帧覆盖旧预览，计数和原始采集逐帧处理不跳帧。
- `scripts/wo0016/camera_only_preview.py`：受控相机预览用软件 `THRESHOLD=128` 生成 PIP，画面明确标注 `SIM TH=128` 与 `SIM COUNT`；核验 `CAP_V4L2`、MJPG 640×400@60、手动曝光 50、亮度 0 和只读 DMA idle/error 状态，不启动 DMA、Overlay 或正式采样。X 轴中心为 150，迟滞带为 135–165。
- `scripts/wo0016/run_stream_counter.py`：新增 `--component-cluster-gap`（默认 16 px）和 `--prediction-max-missing`（默认 2）配置；两项进入计数配置摘要及 Stage B-2 Permit 身份哈希。编码回调通过同一 DMA 槽位传递对应 Gray8 与 Binary8。
- `scripts/wo0016/operator_gate_launcher.py`：重做响应式暗色状态/控制页。Stage B-2 非消费 READY Guard 流程必须先绑定已校验 Permit 的 SHA-256；摘要显示在页面，完整摘要仅在只读状态 JSON 返回。口令仍由带外渠道提供，HTML 与状态 JSON 不包含口令，启动仍是一次性且默认 loopback。
- 定向回归覆盖碎片合并、面积加权质心、短缺帧外推、迟滞边界、同槽位图像配对、编码线程阻塞期间 DMA/计数继续、空 Mask 115 帧，以及页面认证和 Permit 绑定。

## 离线回放与帧率测量

- Attempt #5 Recovery-2：使用新的 16 px BBox Gap + 2 帧预测配置回放全部 165 条 **CCL 几何记录**，得到 4 次事件（帧 64、84、118、150），与历史板端累计数相同；本组输入没有原始像素，结果不是图像级重分割回放。输入 SHA-256：`4c533b1b70a8337820cf31343cd790008958ff4b8670496a6269ef12fe6c56ec`。
- B1 像素级回放不可用。生成式单元回归的 115 张全零 Binary8 Mask 得到 0 个组件、0 个检测、0 个事件；这是软件合成输入，不是 B1 数据，也不是板端 Capture 误报验收。
- 主机合成基准详见 [benchmark.json](benchmark.json)，脚本为 [benchmark_offline_pipeline.py](benchmark_offline_pipeline.py)：
  - 49×320 ROI 投影从旧实现中位数 51.559 μs 降到 24.609 μs，约 2.095×，投影输出完全相同。
  - 165 对合成 Gray8/Binary8 输入的同步计数+JPEG 循环中位数 40.245 ms（4099.9 帧/秒）；异步 JPEG 下的计数/生产循环 26.562 ms（6211.8 帧/秒），约 1.515×。该主机数字不是 V4L2、DMA 或 PYNQ 帧率；编码后台完成时间另为 127.453 ms 中位数。最新帧预览允许合并掉过期画面，所有样本仍逐帧计数。
  - 上述是 Windows 11 / Python 3.12 / NumPy 2.5.3 / OpenCV 5.0.0 的合成基准，不是板端帧率。板端 `-02` 旧版 camera-only 预览在 2026-10-22 改码部署前的本轮只读快照为约 21.66 FPS（1.523 秒流样本），其启动记录为 180 帧 / 8.516827 秒 = 21.017 FPS；这些读数不能代表新版 HUD。

新配置的 Attempt #5 回放产物位于 [attempt5-ccl-bbox16-prediction2](attempt5-ccl-bbox16-prediction2/)。历史回放摘要仍保存在 `data/results/wo0017-phase1a-audit-20261022/baseline-replay.json`；该目录未改写。

## 板端 `-03` 预览尝试（未启动）

- 当前 Boot ID 与目标一致：`35983d66-bb1d-4a8a-85e5-802170825515`。停止旧预览前的只读门禁记录为 `CmaFree=47,200 KiB`、MM2S/S2MM `DMASR=0x00000001`（错误位均为 0）；同一 Boot ID 下的 `stage1-attempt-root-02` 收据为 `OVERLAY_STAGE1_PASS`，Manifest SHA-256 为 `4493e4dab601e0560002ec089fb43c2f333a5ed18897b25500e7b0b1d1890a1c`。该 stage1 guard 已标记 consumed，本次没有尝试重新消费。
- 核验旧 PID 7297 的命令行为目标 Boot ID 下 `-02/camera_only_preview.py` 后发送 TERM。进程退出后，`fuser /dev/video0` 与 8080 监听查询均为空，表明旧预览和相机占用已释放。
- 停止后再次确认 Boot ID 未变，`CmaFree=68,816 KiB`。随后的只读寄存器命令在 `sudo -n` 下触发 `Bus error`：命令把 S2MM 映射为 `0x40410000`，而本项目驱动定义使用 DMA 基址 `0x40400000` 加 S2MM 状态偏移 `0x34`。因此停止后的 MM2S/S2MM 门禁读数不完整；遵循一次异常即停止规则，没有重试或继续板端操作。
- 结果：没有部署文件、没有创建或启动 `-03`、没有打开新版 HUD；未启动 DMA、未重载 Overlay、未开始正式采样。故没有新版 HUD FPS 或画面状态可报告。需在新的获准操作中用正确的 DMASR 地址重新完成门禁后再部署和启动。

## 验证和边界

`C:\fpga\material-counter\.venv\Scripts\python.exe -m pytest scripts\wo0016 -q`：**159 passed**（2026-10-22，本地完整测试）。包括 N 帧预分配、采样后 NPZ 写出、完整/逐数组 SHA-256 校验、篡改拒绝、软件阈值 PIP 标记及 220 帧容量预检。

要验证 5/5 图像级回放和 Capture 开启时的 B1 0/115，需要提供完整、可校验的 Attempt #5 原始 Gray8/Binary8 Mask 数据及 B1 115 帧原始 Mask；要称为实体准确率，还需要独立录像或观察记录确认每颗实体实际进入检测窗口的帧区间和身份关联。当前算法修改已改变计数器、runner 与 8081 launcher 的哈希；旧 Permit 不再匹配新源码/配置，未来任何实板动作仍需重新核验并单独授权。
