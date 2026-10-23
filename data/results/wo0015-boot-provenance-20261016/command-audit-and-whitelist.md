# 下一轮 OS 文件只读取证：精确白名单与命令审计

本文件和 `future-command-plan.json` 仅为离线提案。**本轮没有执行其中任何远端
命令，也没有连接板卡。** 用户已说明板卡断开连接；不安排自动重连/轮询/上电。

## 分开的授权范围

**建议下一轮仅批准 B1：os-metadata，一次。** 精确内容读取白名单如下：

| 路径 | 用途 | 上限/次数 |
|---|---|---|
| /proc/sys/kernel/random/boot_id | 授权会话，采集前后完全一致 | 1MiB上限，前后各读1次 |
| /proc/sys/kernel/osrelease | 当前OS报告的内核版本 | 1MiB，1次 |
| /proc/version | 当前内核编译标识 | 1MiB，1次 |
| /proc/cmdline | 当前启动参数/根文件系统线索 | 1MiB，1次 |
| /proc/uptime | 运行时长辅助证据 | 1MiB，1次 |
| /proc/self/mountinfo | 实际挂载源、只读标志和/boot路径 | 1MiB，1次 |

没有sysfs属性回调、/dev节点、CMA、Guard、PYNQ全局状态、进程maps或启动分区
文件读取。此范围专门缩小为启动来源前置取证，不沿用旧硬件执行器。

**B2：boot-bin，须根据B1结果另批。** 仅增加 `/boot/BOOT.BIN` 一项，64MiB硬
上限，boot_id前后两次、mountinfo前后两次。必须已存在唯一 `/boot` 挂载：
根目录`/`、设备号179:1、源`/dev/mmcblk0p1`、vfat、mount与superblock选项均ro；
再用被钉住文件fd的fstatvfs前后确认ST_RDONLY。任何不符均停止，**不remount、
不mount、不umount、不修复文件系统、不尝试其他路径/设备**。

这些具体挂载值是待B1核对的保守预期，不是已经证实的当前布局。若真实布局不同，
应先评审新的精确白名单，不能自动扩展。本地镜像虽显示MBR type0x0C，但BPB为
FAT16；Linux mountinfo中的vfat是通用FAT驱动名，两者不能混作实际挂载已证据。

阶段B2目前不包含REVISION（含敏感信息）、BOOT.PY、boot.scr、image.ub、uEnv.txt、
FSBL ELF、BIF或物理设备。若BOOT哈希与候选相同，可以建立当前磁盘内容对应关系；
若要进一步识别启动脚本/内核/库，需要再列固定文件另批。

## 可审阅的精确命令

`prepare_os_commands.py` 只在主机生成命令数据，不运行命令或连接网络。
`future-command-plan.json` 保存两个阶段的完整argv、POSIX shell引用后的完整
remote_shell_command、源文件和wrapper摘要。形式为：

```
/usr/bin/sudo -n -- /usr/local/share/pynq-venv/bin/python -I -S -B -u -c '<固定源码摘要验证包装器>'
```

包装器只检查root/Linux/armv7l，验证内嵌原始源码SHA-256后执行该已审阅模块的
collect(PinnedReadOnlyFiles(), stage=固定阶段, nonce=本次随机值)。数据字节不被
当作脚本执行。包装器不执行BOOT、FSBL、BOOT.PY、boot.scr或PS初始化。取证结果
输出到stdout，未在板端创建文件；只有host端保存结果。

命令JSON的全零nonce只是审阅示例，**不能用于实际取证**。新批准执行时由主机
生成fresh 128-bit随机nonce，再调用已审阅make_command；固定阶段及nonce均有
白名单验证和shlex引用，不能注入其他命令。返回nonce不能独立证明来源，必须
同时绑定已核验SSH主机密钥、源文件摘要、这一条固定命令和本次传输记录。

`sudo -n`需要已具备无交互授权；若失败，直接停止，不自动改用-S、交互输密码、
登录root或运行环境设置脚本。若需新的凭据方式，应先明确另批，不在聊天/日志
保存密码。PYNQ Python路径由历史记录支持，不证明当前仍存在；缺失也直接停止。

## 隐式副作用审计

- `-I -S -B`避免用户PYTHON环境、site启动钩子和pyc写入；只使用Python标准库，
  不导入PYNQ/Overlay，不调用subprocess、mmap、ioctl、shutdown或硬件服务。
- `O_PATH|O_NOFOLLOW|O_CLOEXEC`先钉住inode，拒绝symlink、字符/块设备、目录、
  FIFO后才通过本进程`/proc/self/fd/N`只读重开；比较inode/dev/mode，避免把最终
  路径替换后当作设备打开。该内部fd路径不是开放的额外文件白名单。
- 可读fd使用O_RDONLY|O_NOATIME|O_CLOEXEC|O_NONBLOCK。O_NOATIME失败不降级，
  全部fd用finally关闭；不对任何硬件句柄作清理。O_NONBLOCK不保证regular-file
  读取不阻塞，未来传输应有30秒外层超时，超时停止、不重试。
- B1仅proc内核元数据读取，无驱动MMIO节点；B2另要求启动文件所在文件系统已经
  readonly。O_NOATIME并非适用于所有文件系统，故不靠它替代B2只读挂载门控。
- O_PATH避免对非regular对象进行正常readable open，但仍依赖可信Linux/procfs/
  命名空间；不声称抵抗恶意root、内核或并发重新挂载。B2前后mountinfo/fstatvfs
  是检查证据，不是不可竞争的全系统独占锁。
- SSH登录、sudo/PAM/audit/journald本身可能产生系统日志、进程和调度/cache变化；
  不将“命令只读”夸大为“系统/介质绝无写入”。若要求整张SD绝对零写入，不能
  使用运行中的SSH会话，应维持离线镜像取证。该方案不写/替换启动文件。
- 本轮未调用真实后端；其Linux系统调用可用性和当前环境匹配仍UNKNOWN。

参考：[Linux open(2) O_PATH/O_NOATIME](https://man7.org/linux/man-pages/man2/open.2.html)。

## 失败、证据与停止

任一认证/摘要/nonce/权限/缺失文件/对象类型/大小/只读挂载/boot变化/IO/编码异常
立即停止，保存已返回样本与stderr/exit code，无自动重试、无回退命令、不补装
依赖。主机先按原始bytes写独占文件和计算SHA，再解析JSON；禁止先strip或换行
转换，禁止覆盖历史。当前模块即使采集完成也只输出COLLECTED_OS_FILES_ONLY，
顶层始终STOPPED_PRECONDITION_UNVERIFIED、afi_authorized=false。

如果断开连接仅是网络变化，不预先假设boot变化；若实际上已断电/重启，旧授权
boot `822a9c29-37c0-438c-81c4-da998d57073b` 不自动适用于新会话。第一项boot读数
不匹配时只保留该原始读数，其他文件不读，等待新基线授权。没有任何AFI准入。
