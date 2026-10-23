# 实时只读准入采集器：离线审计

状态：实现与离线测试完成；**没有实机运行，也没有寄存器准入放行**。

源码 `readonly_admission_collector.py`，版本
`wo0015-readonly-admission-v2-offline-reviewed`。这是一个真实OS文件读取后端和
证据收集函数，不是上一轮接收九个布尔值的 verifier 占位接口。没有连接旧
`afi1_readonly_candidate.py`，没有寄存器读取函数、网络/SSH或PYNQ依赖。
源码作为独立结果供审阅，未替换生产执行器，CLI始终提示离线审阅并退出2。

## 已实现的证据采集与拒绝行为

- 固定授权boot、内核版本、PYNQ-Z2/Zynq-7000 OS身份、root/armv7l环境；
  这些证实的是OS所报告的身份，不是板卡唯一硬件身份证明。
- 固定BIT/HWH及7个当前Guard原始SHA-256。样本先保存原始bytes的长度、
  base64、SHA-256和UTC时间，再做文本解析；不对摘要输入strip或转换换行。
- PYNQ全局状态文件仅按JSON读取，不导入任何PYNQ模块；严格校验缓存BIT路径、
  该文件实际SHA-256、缓存SHA1、显式字典类型，拒绝重复键。shutdown_ips仅被
  观察，不触发任何shutdown操作，也不因其非空而执行清理。
- boot与uptime前后采样；全局状态/FPGA Manager状态和flags前后字节一致；
  内核uptime与本进程monotonic窗口均不得超过5秒或倒退。非有限时间拒绝。
- root扫描proc cmdline/maps/FD目标，检测devmem、UIO、FPGA、DRI、zocl句柄及
  已知操作进程；权限不足、扫描中进程消失均视为无法完整证明，停止且不重试。
  cmdline/maps只保存摘要，避免把其他进程参数写进回传证据。
- CmaTotal/CmaFree仅遥测，无分配、释放、差值硬门控或生产CMA逻辑修改。
- 文件路径固定白名单，拒绝/dev/mem、任意debugfs、firmware写入口等；
  `O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK`，fstat拒绝非regular对象，
  原始读取有16MiB单源上限，所有成功open的fd在finally关闭。

即使OS样本全部满足，也始终返回 `STOPPED_PRECONDITION_UNVERIFIED`、
`afi_read_authorized=false`、register_reads/writes=0。复位定义标CONFLICT，
当前复位状态UNKNOWN；当前PL与软件记录的一致性只标INFERRED。
既有Guard保持原位，绝不归档、删除、续执行或重试硬件。

## 离线测试边界

全部输入来自fake FS/monkeypatched os函数，未调用真实后端读取板端或主机
proc/sysfs数据。正常及故障路径检查readonly flags、字节保真、关闭资源、
身份/摘要/时间拒绝、占用与不可见、非法路径及设备拒绝。

`collector-behavior-red.txt`记录接口占位实现未满足预期的失败；
`duplicate-key-red.txt`记录歧义JSON实际被接受的失败，然后用拒绝重复键修复。
最初模块尚不存在时的44个setup errors保留在collector-red日志，**不把该次
setup errors算作有效行为回归证据**。最终测试总数见final-offline-tests日志。

## 已审计但尚不能消除的限制

1. 32字符hex challenge只是绑定本次请求的字段；新鲜随机数必须由未来可信
   主机生成，并由固定SSH主机密钥、源码摘要锁及原始stdout采集共同验证。
   本模块没有传输认证或远程证明协议；不得相信任意上传JSON或backend自报kind。
2. O_NOFOLLOW只保护最终路径组件；fstat在open之后拒绝设备，不能证明打开
   任意被篡改路径绝无驱动open副作用。父目录/挂载与OS须可信。当前实现不具备
   抗恶意root或路径替换的完整隔离能力，不能宣称无条件可部署。实机启用前应
   核验固定目录/挂载与来源，若该信任条件不能接受，再评审dirfd/O_PATH等更严格
   后端；**本轮没有为消除此条件而运行任何板端命令**。
3. proc占用扫描是单窗口快照，不是独占锁；global state和磁盘文件也不是PL
   配置读回。因此它们不能代替复位证明，更不能单独放行AFI。
4. 当前没有实机路径存在性、系统调用权限、sysfs节点适配或耗时验证。内核
   sysfs只读属性的实际实现需在部署评审中与当前镜像对应；本轮未触发这些属性。
5. 无AFI/SLCR读取后端，无可配置地址，也没有自动将文档冲突改成PASS的选项。
   未来任何寄存器读取须另审固定源和独立授权。

结论：本轮完成了采集器的离线实现、测试和审计，明确记录部署前约束；
**不构成当前硬件前置条件已经满足或本轮可以上板的声明**。
