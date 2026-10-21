# WO-0016 Stage B-2 Attempt #5 门禁就绪报告

当前状态：**ATTEMPT5_PRESTART_FAILED_XRT_ENV; WAITING_FOR_NEW_ONE_SHOT_AUTHORIZATION**。Boot ID 仍为 d9f65950-d59b-4ea4-bee4-b28656cb176f，Stage 1/Manifest 与 READY Guard 在启动前均匹配。执行器因 systemd 环境缺少 XILINX_XRT=/usr 在 PYNQ 设备发现阶段退出：未到 Overlay ready 或相机就绪，未接收浏览器点击，处理 0 帧且 frame record 为 0。Attempt #5 consumed marker 已创建并消费；8080/8081 已停止。

下表记录启动前最后一次门禁快照；它不代表失败后的操作页仍处于 READY。最新启动失败与板端回执哈希见文末“最新启动失败”。

## 门禁核验表

| 门禁项 | 当前观测 | 结论 |
|---|---|---|
| 旧 Boot / Attempt #4 归档 | `/var/lib/wo0016/history/boot-6f13f0f0-4433-445f-a023-fa403685aeee/attempt-4/`；83 个文件回读核对；归档清单 SHA-256 `04555586bb0ac8e6df2c6f4d5111ee551429481290499a1f7201f0f751f493e1` | PASS，审计链保留 |
| 旧 Boot Stage 1 / Manifest / READY / Attempt #4 | Stage 1 回执 `0a7f601f…c27dd7d3`；Manifest `c5cb8ebd…dfb3b551`；READY Guard `f1546402…45cd5b9b`；Attempt #4 consumed marker `5d85a1d6…e1f0285` | PASS，均归档保留 |
| 当前 Boot | `d9f65950-d59b-4ea4-bee4-b28656cb176f`；Stage 1、Manifest、READY 与 permit 均绑定此 Boot | PASS |
| 当前 Stage 1 Manifest | `/home/xilinx/wo0016/attempt5-boot-d9f65950/wo0015/run-manifest.json`；SHA-256 `517debdc3a7e94d58dde1d8db32995d498a9fc53acc48623d46dc39061dd08c0`；离线预检 `OFFLINE_PREFLIGHT_PASS` | PASS |
| 持久 BIT/HWH | `/home/xilinx/wo0015/wo15.bit` 是普通文件，SHA-256 `a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420`；`wo15.hwh` 是普通文件，SHA-256 `bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6` | PASS，符合持久基线规则 |
| Stage 1 运行目录 BIT/HWH | 隔离运行目录中的 `wo15.bit`、`wo15.hwh` 软链接分别指向 `/home/xilinx/wo0015/wo15.bit`、`/home/xilinx/wo0015/wo15.hwh`；原候选副本保存在 `source-copies-before-baseline-links/` | PASS，无 `/tmp` 依赖 |
| Stage 1 实板回执 | `OVERLAY_LOAD_PASS`；回执 `/var/lib/wo0015-s2mm/overlay-only-pass.json` SHA-256 `8c79ff081bca5a40cf7bacaa5ab095bdfc0382e16e05604e45b448e6814fd745`；结果 JSON `/home/xilinx/wo0016/attempt5-boot-d9f65950/stage1-overlay-only-result-d9f65950.json` SHA-256 `4933fa6aa3d28a189a53afd65cba696730b96a140e57b700a9930404e0858c8c` | PASS，回执 Boot 与 Manifest 匹配 |
| Stage 1 一次性记录 | `/var/lib/wo0015-s2mm/overlay-only-consumed.json` 已按 one-shot 消耗；SHA-256 `03cc64f337f54bf26ce9f8ea4f2cae2b99793a8526ab131c4c740e854f82ee3d` | PASS，Stage 1 仅执行一次 |
| Overlay / PL 核验 | Checker `0x574f3134`；Packer `0x574f3135`；`AFI1_HP1_32BIT_SYNC_PASS`，RD=`0x00000001`、WR=`0x00000f01`；post-stage audit `PASS` | PASS |
| FPGA / PYNQ 状态 | FPGA Manager `operating / flags=0`；PYNQ global state 指向 `/home/xilinx/wo0015/wo15.bit`，`shutdown_ips={}` | PASS |
| CMA | `CmaTotal=131072 KiB`。授权清缓存一次：清理前 `32104 KiB`、清理后 `130544 KiB`；Stage 1 后复核 `130572 KiB`；最终门禁复核 `94432 KiB` | PASS，最终严格大于 `32768 KiB` |
| 相机服务 / V4L2 | `ov9281-init.service Result=success`、`ExecMainStatus=0`；服务为 oneshot `inactive/dead`；MJPG 640×400、手动曝光模式 1 / 50、防闪烁 1、Gamma 100 | PASS |
| 资源与端口 | 光源调整后最近一次成功复核：`/dev/video0`、`/dev/video1`、`/dev/mem` 无占用；8080/8081/9090 无监听；Jupyter inactive；`CmaFree=76,868 KiB` | PASS；后续 SSH 不稳定，启动前需再核验 |
| 最新执行器 | Runner SHA-256 `8f6c1fe14e3d67b607eb728635076a782f7fa868a713be05b590abf1af3ae158`；Counter SHA-256 `83d3bb62e32f778e8c800eac08a415aa3f85bc27290ead3f458f96daa1e3a8df` | PASS，许可绑定实际部署代码 |
| READY Guard | `/var/lib/wo0016/stageb2-ready-d9f65950-d59b-4ea4-bee4-b28656cb176f.json`；SHA-256 `e7c8a4ddfe93ced8b32ab42626697d235520df1b57d1c51a555b4edf6b56d365`；`guard_status=READY`、`consumed=false` | PASS |
| Attempt #5 permit | `/var/lib/wo0016/attempts/d9f65950-d59b-4ea4-bee4-b28656cb176f/attempt-5/attempt-permit.json`；SHA-256 `54c820a9a25018bb0af30493b74fa02ad70748fb8d5cb7de28d4d5aa0ef06900`；`permit_status=AUTHORIZED`、`consumed=false` | PASS |
| Attempt #5 marker / 采集 | 启动前 marker 不存在；本轮启动后 marker 已存在，SHA-256 93328c883de7d927119c4db6ddb7c9df4fd65dfb63207264b50d66f655e2370e，内容 consumed=true；失败回执 frames_processed=0、frame_records=0 | FAIL-CLOSED：采集未开始，一次性 Attempt #5 槽已消费 |

## Attempt #5 已绑定配置

- 5 颗 `M6_nut_no_washer`，`target_count=5`，165 帧，稳姿 3.0 秒，THRESHOLD=128。
- 计数线 `X=160`；组件合并 `dx=24, dy=12`；跨线冷却 3 帧、纵向走廊 15 px；边缘缺帧容忍 4；关联横向门限 18 px。
- 许可绑定 Runner、Counter、Manifest、BIT/HWH、READY Guard SHA 和 counting configuration，使用 --no-consume-guard；该参数只让 READY Guard 保持 consumed=false。执行器在 PYNQ/Overlay/相机初始化之前创建 Attempt 专属 consumed.json，因此启动等待页会先消费一次性 Attempt 槽。本轮 marker 已消费，禁止删除、重置或重试。

## 下一步

本轮执行器未到相机就绪，8081 等待页未保持运行。板端交互登录环境有 XILINX_XRT=/usr，但 systemd-run 启动环境没有继承；WO-0016 README 明确要求以 sudo -n env XILINX_XRT=/usr 启动。原 Attempt #5 marker 已消费，不能复用。后续若要重试，需先保留现有 marker 和失败证据，并取得新的唯一一次性许可授权。

Attempt #5 的浏览器流程仍由操作者亲自点击：按钮就绪后点击才进入 15 秒准备、3 秒稳姿；出现 FRAME_0_CAPTURE_START_FEED_NOW 后 0–3.5 秒内约 0.75 秒/颗投完 5 颗。注意当前执行器在按钮等待态之前已消费 Attempt marker。本轮初始化失败，没有到达按钮 READY，也没有采样。

## 相机遥测预览

- 当前 Boot：`d9f65950-d59b-4ea4-bee4-b28656cb176f`；systemd transient unit `wo0016-camera-telemetry-preview.service` 已停止，原 PID `7883` 已退出。
- 预览地址 `http://192.168.2.99:8080/`；最近一次成功复核时 `/dev/video0` 和 TCP 8080 已释放。相机保留 MJPG 640×400、brightness `0`、手动曝光 `50`、防闪烁 `1`、Gamma `100`。
- 已停止的 HUD 曾显示 ROI 灰度均值/峰值、阈值前景像素、面积不少于 12 px 的连通域、预览 FPS/帧间隔、相机读取与上一帧处理耗时、JPEG 大小。最新一次执行器启动失败，Attempt #5 marker 已消费但处理 0 帧；详见文末失败记录。
- 遥测预览脚本 SHA-256：`0b5be3cbd9252e92af25d0c081c664f7ee40cb0b37c2d39c93561b770856fb2a`。

结构化最终审计快照见 `board-final-gate-pass.json`；此前的 `attempt5-preparation-status.json`、`board-final-gate.json`、`board-post-jupyter-readonly.json` 和 `ATTEMPT5-PERMIT-NOT-ISSUED.json` 是签发前的历史快照，保留用于追溯。

## 最新启动失败：2026-10-21

- 当前 Boot 为 d9f65950-d59b-4ea4-bee4-b28656cb176f；Stage 1、Manifest、READY Guard、许可及执行器哈希在启动前核验通过。READY Guard 的 consumed 仍为 false。
- 执行器以 systemd transient unit 启动，但 systemd 环境未设置 XILINX_XRT。交互登录 shell 中该值为 /usr；项目板端说明要求启动时显式提供 XILINX_XRT=/usr。
- PYNQ 设备发现失败并返回 RuntimeError: No Devices Found，unit exit code 2。Milestone 只有 PROCESS_START；没有 OVERLAY_READY_DOWNLOAD_FALSE、CAMERA_OPENED、CAMERA_OPEN_READY_FOR_OPERATOR 或 FRAME_0_CAPTURE_START_FEED_NOW。处理帧数 0，frame record 数 0，未接收点击，未启动采集。
- 结果 JSON SHA-256：e11402ff1d707c711627c92fdd60502cd42514a31d6d5fd188c9e7dae19c4d27。consumed marker SHA-256：93328c883de7d927119c4db6ddb7c9df4fd65dfb63207264b50d66f655e2370e；marker 内容 consumed=true。milestones、exit-status 和 operator events 的 SHA 分别为 50ff12b22cddf393f597285e52cda68a1f951e3ba1a193ce1785f19b09ee3edb、d914a9a46002de134eebc2267bc8a142695b92853332b932abce3a5a4d3593eb、311ecc15dfc8c85db1a6e2bbf7da018a758680efe51cb431fa15884de113c77c。
- 失败后只读状态：Boot 未变，CmaFree=65960 KiB，FPGA Manager operating / flags=0，/dev/video0 无持有者，8080/8081/9090 无监听，operator unit inactive/dead。
- 按一次性 fail-closed 约束，保留 marker 与所有失败证据，不删除 marker、不重试、不重签当前 Attempt #5 许可。若要继续，需要用户明确授权一个新的唯一一次性 permit；下一次启动须显式传入 XILINX_XRT=/usr。
