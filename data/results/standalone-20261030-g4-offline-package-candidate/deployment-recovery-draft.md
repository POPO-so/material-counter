# G4 部署与恢复审阅草稿

状态：仅静态草拟，供审阅。本文不是部署、停机或回滚操作说明，不授权安装、启动、测试或恢复操作；其中没有可执行命令或步骤。

## 已知状态与未知项

- 候选 package 尚未复制到板端，候选 service 尚未注册或启动。实际部署前的 service、环境文件及 `current` 符号链接状态均未知。
- 当前没有已确认的旧 G4 release。因此，本文所称的回滚目标仅为“恢复部署前实测记录的系统/service/env 状态”，不表示存在可重新启动的旧 G4 release。由于部署前状态尚未实测和保存，目前无法定义具体恢复值。
- 候选 service 文件静态配置了 `WorkingDirectory=/opt/material-counter/current` 和 `EnvironmentFile=/etc/material-counter/package.env`。候选环境文件的 `PACKAGE_ROOT` 为 `/opt/material-counter/releases/standalone-20261030-g4-offline-package-candidate`，当前对应 deployment index SHA-256 为 `96b4661b8e00f27de35be53236d108717aec80b23d9ff26ee63265e77afa3088`；此前的 `63216dc2014621bc5f03e5201fcdc87dd33e4b620fe420317d660d266f09d56d` 和更早的 `3addc403eacdb17caf7fb699a712dbbb90b047ec1f7049c47df2b84bf1eccc2a` 均为刷新前历史摘要。
- 后续若获准部署，须先证明 `current` 的代码树与 `PACKAGE_ROOT` 指向同一 release。当前没有验证这条路径关系；不得据候选配置推断板端链接目标或目录内容。

## 停止配置与硬件安全边界

候选 unit 文件记录了 `Restart=no`、`KillMode=process`、`SendSIGKILL=no`、`TimeoutStopSec=infinity`。这些只是文件配置事实；实际停止行为尚未在板端验证。它们不能证明进程可被强制停止、服务会自动恢复，或超时后能安全收尾。

停机状态仍须在同一轮证据中核查子进程、唯一硬件 owner、相机与缓冲资源、DMA idle、VDMA halted 及错误状态。仅观察到主服务进程退出，不足以证明资源已安全释放。

如进入 `FAIL_HOLD`、读回异常或资源所有权未知，应保持隔离，保留证据和资源引用；不得尝试 kill、restart 或重载 overlay 来恢复。等待唯一 owner/Manager 后续处置。这遵循 [部署文档已有的硬件所有权和回退安全门](../../../scripts/standalone/DEPLOYMENT.md#硬件所有权和回退安全门)。

## 未来部署前的记录要求

任何未来部署审查开始前，须采集并保存下列部署前实测基线；未知值应明确记为待实测，不以候选文件或推断补值：

- service 的 enable 与 active 状态；
- unit 与环境文件的哈希；
- `current` 符号链接的实际目标；
- release、deployment index、manifest、BIT、HWH 与 native library 的身份和哈希；
- boot ID；
- owner 与相关进程状态。

只有安全停止已核实，且目标制品配对与哈希已核实后，才可由 Manager/唯一 owner 讨论是否恢复此前记录的配置。恢复目标必须来自已保存的部署前实测基线；这不预设存在旧 G4 release。

## 本次工作边界

本次仅做静态草拟：未安装候选包、未注册或启动 service、未测试、未执行回滚或板端操作。G4 未验收。本稿本身不构成部署、测试、停机或恢复授权。
