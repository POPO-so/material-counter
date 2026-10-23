# 独立审查：WO-0015 POSIX 离线收口

日期：2026-10-16。范围仅限本结果目录内的 B1/B2 模板、mock、契约测试、冻结文本与 hash 清单，以及两份 v2 说明、主报告和复审 brief。冻结文本只按文本读取和静态审查，未执行。

## 结论

未发现 P1/P2 问题。实现与本轮给定的 B1/B2 POSIX 契约及文档一致；mock 对关键调用顺序和失败边界有覆盖。以下结论仅代表本地静态检查和内存 fake 测试，不代表真实 POSIX 目标文件系统、SSH、板卡或硬件验证。

## 审查证据

- B1 在 [remote_template_v2.py](proposals/remote_template_v2.py#L90) 的统一 helper 中先 allowlist，再以精确 `O_RDONLY | O_NOFOLLOW` 打开、fstat 判 regular、以 65536 字节上限读取并在 finally 关闭 fd。共享核心在该 helper 前检查 euid；root 在首个打开前拒绝。首个 boot_id 匹配后才顺序读取其余五个路径，并用同一 helper 尾读 boot_id；失败直接返回，未见重试或自定义 reader 绕过（同文件 L106-L138）。
- B2 在同文件 L145-L217 先核对 boot_id，再钉住 `/boot` dirfd；BOOT.BIN 通过 dirfd 以 `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` 打开。内容读取前依次检查 ST_RDONLY、regular、`0 < size <= 32 MiB`、目录与文件 st_dev 相同，并用 `stat("/", follow_symlinks=False)` 得出 `boot_dev_differs_from_root`。这些检查位于 `_read_bounded` 前；异常均进入 finally 关闭已打开的文件 fd 和目录 fd。读取后还检查 ST_RDONLY、stat 四元组、字节数和 boot_id。
- [proposal_contract_v2.py](proposals/proposal_contract_v2.py#L69) 记录每次 open 的路径、flags 与 dir_fd，并拒绝错误 flags。其非 regular fake 的 open 分支先成功返回 fd，再由 fstat 报出 FIFO、字符/块设备、目录和 socket 类型（L99-L108、L112-L140）。这与“open 成功后由 fstat 拒绝”的检查要求相符。
- [test_proposal_contract_v2.py](proposals/test_proposal_contract_v2.py#L71) 覆盖 B1 root 零打开、boot mismatch 的 fail-fast、七次有序 helper 事务、超限关闭及 EACCES 停止；L148-L303 覆盖 B2 exact openat flags、非 regular、大小、只读、设备号、root-device 标记、前置零 BOOT.BIN 内容读取、双 fd 关闭、短读、stat 变化、EACCES 和 boot_id 变化。静态检查 L305-L397 比对文档选项、模板 hash，以及冻结文件与模板加 wrapper 的字节内容和 hash。
- 冻结 B1/B2 wrapper 仍先要求 `EXECUTION_STATUS == "APPROVED"`，当前常量保持 `NOT_APPROVED_DO_NOT_EXECUTE`；root 检查在 capture 前，失败状态以非零退出。冻结 B2 JSON payload 含 `boot_dev_differs_from_root`（frozen B2 文本 L219-L242）。文档也准确说明 SSH 尚未获批、known_hosts/解释器目标条件未核实，且 ST_RDONLY 不能证明整张 SD 卡零写入。

## 检查运行

按指定命令仅运行四个显式测试文件：

```
C:/fpga/material-counter/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider <out>/manager/test_golden_analysis.py <out>/rootfs/test_ext4_ro.py <out>/fsbl/test_fsbl_audit.py <out>/proposals/test_proposal_contract_v2.py
```

结果：50 passed，0.23 秒。该命令只选取上述四个测试文件；未做目录或通配符收集。

## 记录项与边界

当前 `verification-report.md`（第102、112行）和 `review-brief-v2.md`（第36、52行）均列为 50 个方法通过 + 20 个子测试通过，JUnit 70 条记录包含子测试；`new-targeted-tests.txt` 也记录 50 passed、20 subtests passed。计数现已核对一致，不再是待确认项。本次独立 pytest 汇总显示 50 passed；该 pytest 输出未单独列出子测试数。

未尝试 SSH、real OS backend、冻结文本执行、板卡/MMIO/Overlay/DMA；未读取 restricted-raw 或 REVISION，未读取/哈希大镜像，也未重解析真实 FSBL 的 21 张表。尚未验证真实目标上的文件类型、挂载状态、可读权限、known_hosts 与解释器身份；离线 fake 不能提供这些证据。


