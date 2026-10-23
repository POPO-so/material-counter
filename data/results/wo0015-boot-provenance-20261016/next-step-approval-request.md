# WO-0015 下一轮最小授权申请（仅提案，未执行）

当前继续STOPPED_PRECONDITION_UNVERIFIED。板卡由用户断开，**不请求立即上电或
重连，不安排自动连接**。本轮离线解析只证明本地镜像结构和共同UUID线索，未能
证明实际历史BOOT/FSBL来源，不申请AFI/SLCR/MMIO、Overlay、复位或DMA。

## 建议仅批准B1，一次OS元数据采集

当用户另行明确板卡会话可用时，审批一次固定命令：阶段`os-metadata`，源码
`os_file_proposal.py`，准确SHA-256及完整argv在`future-command-plan.json`与交付
索引。仅采集6个proc文件：boot_id、osrelease、version、cmdline、uptime、
self/mountinfo。没有/boot文件内容、sysfs属性、设备节点或任何MMIO。

执行前必须核验SSH主机密钥及固定源码，主机新生成nonce（当前全零仅审阅示例），
使用已审阅`make_command`生成固定scope命令。sudo-n/解释器/认证/来源/权限任何
不满足立即停止，不换凭据模式、不安装依赖、不重试。原始stdout/stderr先以
bytes独占落盘和hash，之后再解析；不得先strip/换行转换。传输外层超时30秒，
超时保存现场输出并停止，不循环连接。

原boot授权仍为`822a9c29-37c0-438c-81c4-da998d57073b`。若断开期间实际已重启，
第一项不匹配时仅保留该样本停止；不自动改为新boot，不继续读其他文件。新基线
必须单独重授权。即使B1收集完成也仍STOPPED，**不得自动进入B2或AFI检查**。

## B2留待B1后另批

只有根据B1确认固定/boot挂载且已为ro，再另行批准单个`/boot/BOOT.BIN`读取，
严格按`command-audit-and-whitelist.md`。rw或布局不同直接停止，不通过remount
满足条件。当前BOOT与本地候选hash即使一致，也只能证明采集时磁盘字节，不能
倒推过去BootROM/FSBL实际执行，仍需后续独立证据和AMD文档澄清。

若用户希望继续保持板卡完全断开，替代方案是提供已有的SD备份/BOOT.BIN/FSBL
ELF/BIF/链接map/烧录回执或串口启动日志，仅做离线比对，不需要新增板端操作。

SSH/sudo会话可能写系统认证日志；本提案保证不写/替换启动文件，不能保证运行
系统整张SD绝对零写入。若后者是约束，则不执行SSH方案，继续离线。
