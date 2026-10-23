# 下一阶段最小授权申请（未执行）

维持STOPPED_PRECONDITION_UNVERIFIED。当前不申请AFI读取、任何写入、SLCR解锁、
初始化、Overlay、复位、DMA或新Bitstream。原Guard不归档、不移除。

## 建议A：仅OS文件层的启动/身份来源取证

先完成固定传输和路径/挂载的离线部署评审，再申请一次独立SSH会话，以摘要锁定
的 `readonly_admission_collector.py` 只读固定白名单OS文件：boot/uptime、OS内核/
DT型号与compatible、FPGA Manager state/flags、meminfo、原BIT/HWH、7个Guard、
PYNQ global state及proc占用。输出原始字节、SHA、来源及时间。不得调用旧AFI
候选，即使os_precheck为VERIFIED也停在STOPPED。不在板端创建或修改文件。

固定源码摘要以本目录 `evidence-sha256.json` 为准；当前CLI故意不启用实机采集。
未来传输须校验SSH主机密钥与固定源码摘要，由主机生成新的128-bit challenge，
严格保留stdout/stderr原始bytes后计算hash；不得从任意返回JSON接受PASS字段。
部署审阅若不能证明OS属性读取和固定父路径可信，则不启用后端、继续停止。

启动链归属的进一步证据建议优先由用户提供现有离线板卡镜像/BOOT.BIN/FSBL/
启动日志/硅版本来源，而非为补证运行PS初始化。当前collector不读取/boot目录，
也不自动枚举启动介质；若需新增文件读取，先列明确只读路径和大小限制另批。
silicon revision不能用CPU核心revision或Vivado版本替代。

## 并行的离线澄清

对AMD的具体问题已准备在 `amd-clarification-question.md`。未发送。取得权威
解释并匹配实际silicon/启动证据后，才能重新制定AFI只读前置条件。

## 不建议先做的操作

单次0xF8000240读取有SLCR文档层的只读依据，但bit21是保留位时不能提供所缺
的reset证明。当前新增该读取收益有限，因此不并入建议A。若用户需要它作为
原始诊断，可另批一次32-bit只读，明确不解锁、不写寄存器且读后仍停止，
**不得根据bit21=0自动继续AFI**。读取实现和当前访问环境仍须先审阅。

本文件只是具体范围申请，不是已批准的板端操作。任何硬件操作仍需单独授权。
