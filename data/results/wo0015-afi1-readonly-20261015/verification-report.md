# WO-0015 AFI1 运行时位宽：离线安全审查与准入停止报告

日期：2026-10-15，Asia/Shanghai。状态：**STOPPED_PRECONDITION_UNVERIFIED**。

本轮完成离线证据、文档和读取核心审查；第二阶段未启动。没有连接板卡，没有任何寄存器读取/写入、Overlay/PYNQ 初始化、CMA分配、DMA、复位或shutdown。未改RTL、BIT/HWH或生产验证脚本，未部署、提交或合并。原历史FAIL_STOPPED结论未被更改。

## 1. 运行时结果

| 项目 | 本轮结果 |
|---|---|
| AFI1_WRCHAN_CTRL，0xF8009014 | **未读取**，无原始值或实测时间 |
| AFI1_RDCHAN_CTRL，0xF8009000 | **未读取**，无原始值或实测时间 |
| 与静态32位设计是否一致 | **未知** |
| 当前boot/身份/进程占用 | 未进行实时复核；只审阅历史记录 |
| 当前AXDS复位状态 | **无可信实测证据，访问前置条件未确认** |
| 配置异常 | 尚未证实 |
| 根因假设 | AFI1运行时64位与静态HP1=32不一致，仍为候选 |
| 数据差异根因已证实 | **否** |

停止依据是用户明确要求“任一前置条件无法确认，必须停止”，以及UG585对AFI访问的复位前置说明。没有为了消除证据缺口而写寄存器，亦未自行增加SLCR复位寄存器读取。此状态是安全准入未满足，不能解释为AFI硬件读失败或板卡故障。

## 2. UG585地址、位域及访问约束

已用AMD文档检索服务重新取得官方UG585条目。原始检索响应保存在本目录 `amd-*.json`；搜索结果中的非UG585条目不作为Zynq-7000依据。网页浏览接口访问这两项详情时返回不可访问，未将该失败当成已读到内容；有效依据为AMD文档检索响应和本地生成资料。

| 寄存器 | HP1绝对地址 | 宽度 | 32BitEn |
|---|---|---|---|
| AFI_WRCHAN_CTRL | 0xF8009014 | 32-bit | bit0：1=32-bit，0=64-bit |
| AFI_RDCHAN_CTRL | 0xF8009000 | 32-bit | bit0：1=32-bit，0=64-bit |

官方地址来源：[WR地址](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-axi_hp-AFI_WRCHAN_CTRL)、[RD地址](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-axi_hp-AFI_RDCHAN_CTRL)。字段来源：[WR字段](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-AFI_WRCHAN_CTRL-Details)、[RD字段](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-AFI_RDCHAN_CTRL-Details)。不得拿UG1087中UltraScale+的FABRIC_WIDTH编码替代这里的32BitEn。

AFI控制详情要求相关 `FPGA_RST_CTRL.FPGA_AXDSN_RST` 为0后才能访问。这里只把它视作**必须验证的前置状态**，不是写0授权，也不要求为读取而进行SLCR解锁。

[FPGA_RST_CTRL地址条目](https://docs.amd.com/r/en-US/ug585-zynq-7000-SoC-TRM/Register-slcr-FPGA_RST_CTRL)给出0xF8000240。历史记录无该地址的实时读数。本地生成 `ps7_init.html` 将AXDS1名称对应的bit21标为 `reserved_FPGA_AXDS1_RST`、Reserved/Do not modify。这说明尚须澄清文档/芯片版本下可用于证明复位状态的定义；**不把bit21的位置对应直接当作已确认的有效运行时复位判据，也不从其读零推断安全**。

生成 `ps7_post_config_3_0` 中的复位写0和两项AFI置bit0是静态初始化配方，不能证明Linux本会话执行过它。整段Tcl还包括SLCR等写入，禁止用于此次检查。未读取FIFO/debug寄存器，未将针对FIFO的时钟访问条件机械套用成控制寄存器的已核验事实；任何未解决的访问条件仍阻断放行。

## 3. 工件与设计身份对应的可靠性

重新核验本地export：

```
wo15.bit a2f642f884f6b9c883ab70ea9e9b7a225292dfe3c5794785764362daaac21420
wo15.hwh bf82d299912f816c3502e2628c83d957366ef2d8a89d3cc4fe660ad136d4f1d6
```

HWH、BD及构建Tcl一致：DMA memory S2MM=32，stream S2MM=32，hp1_fabric前后32，PS S_AXI_HP1=32，连接为S2MM→hp1_fabric→HP1。内部auto_pc是AXI4→AXI3协议转换；不是静态32→64位宽转换。静态摘录、文件摘要在 `source-and-script-audit.json`，原详证在上轮 `static-bus-audit.json`。

历史 `wo0015-runonly-resume-retry2-20261015` 的板端文件摘要、加载回执、PYNQ全局状态所指bit文件及其SHA1、Checker/Packer签名共同支持“当时测试与锁定WO-0015设计的对应关系”。历史boot为 `822a9c29-37c0-438c-81c4-da998d57073b`，退出FPGA Manager为operating/flags0，无占用发现；这都是**过去的快照**。

软件记录和磁盘文件hash不等于当前PL比特级读回证明；签名也不是这版bit文件的唯一密码学身份。当前是否仍同一boot、是否有其他进程更换PL、当前复位位是否解除，未实时检查。历史链条足以定位静态HP1参数和解释上次测试，**不足以独立放行当前AFI访问**。

本轮重新核验上轮离线审计46个索引文件及最近板测104个索引文件，150个全部摘要一致。完整核验结果在 `local-evidence.json`；没有覆盖历史结果或归档/移除板端Guard。

## 4. 读取候选核心的审计边界

新建本目录 `afi1_readonly_candidate.py`，只供评审，版本 `wo0015-afi1-readonly-candidate-v1`。它不是已部署的上板执行器，没有SSH、CLI或实际live verifier。直接运行文件无硬件动作；无参调用 `read_once()` 返回停止，见 `candidate-default-result.json`。

审阅确认：仅stdlib datetime/mmap/os/struct；没有PYNQ、Overlay、DMA、shutdown或旧执行器导入。通过未来实时核验器的前提下，设备仅 `O_RDONLY|O_SYNC` 打开、映射仅PROT_READ；按WR后RD顺序各解引用4字节一次。映射一页用于地址对齐，代码不会遍历或解引用其他寄存器。不读复位、无写接口、无重试，异常保留已读取项并关闭映射/描述符。关闭这里仅为Python fd/mmap清理，不调用硬件关闭/复位。

**尚未清除的阻断：** verifier目前只是接口，核心检查九个布尔前置项，不能鉴权调用者提供的证据、时间新鲜度或复位定义。任何mock返回True都只属于离线测试，不构成运行授权或真实准入。因此该候选**不得单独用于板端执行**，不能通过手填True、缓存JSON或旧回执补齐门控。后续需先审阅一个具备真实来源与摘要、同会话boot绑定及失败即停能力的固定只读核验器。

14项离线fake测试通过：无verifier、九个前置项分别失败均不打开设备；32/64/混合模式位域解释；精确两次读取及只读参数；第二次读取异常无重试且清理资源、保留第一次结果。测试中的全部/dev/mem与mmap均被fake替换。`READ_COMPLETE`仅代表读取完成，静态一致性另看 `static_32_consistent`，不能将不一致也称为PASS。独立Worker只读复审确认上述边界，未连接板卡。

测试命令：

```
C:/fpga/material-counter/.venv/Scripts/python.exe -m pytest -q data/results/wo0015-afi1-readonly-20261015/test_afi1_readonly_candidate.py
14 passed
```

本轮未改生产逻辑，故仅运行候选核心定向测试。上一轮459项及5项子测试是历史回归结果，本轮没有重跑，不把它们冒充本轮新测试。

## 5. 下一阶段最小风险方案（须另批硬件范围）

1. 先离线澄清UG585相关AXDS复位说明与本地reserved命名的适用版本，明确什么只读状态能权威证明访问安全。若仍不明确，继续停止，不尝试AFI读取。
2. 若权威资料确认可以通过0xF8000240读取取得有效证明，再单独申请将**一次FPGA_RST_CTRL只读前置取证**纳入范围。当前两地址授权不被扩展成任意SLCR访问；该提案不含任何SLCR解锁或写入。若reserved字段不能承担该证明，不以读零强行放行，改为提交其他有来源的最小只读证据方案。
3. 在单次新会话中只读核验boot、SSH身份、锁定文件、当前加载记录、FPGA Manager与占用，记录时间/来源/hash；满足经确认的复位及访问条件后，再执行两项AFI读取一次。全程无PYNQ初始化、无Guard变更、无任何写寄存器，失败立刻终止。主机原始字节保存stdout/stderr、源文件摘要与每次读数时间，禁止采集器strip/换行转换后计算原始摘要。
4. 若两位均1，只能说明检查时AFI1与静态32位一致，不能排除过去配置不同或其他数据路径问题。若任一位0，确认的是**当前配置不一致**；仍不足以证明过去39520差异的因果关系。任何修复或单帧诊断捕获仍须单独审批。

当前不提出自动AFI修复、完整ps7_init、重下载或DMA重跑。本轮无新硬件操作等待清理，所有CMA/句柄状态只描述历史，不宣称本轮验证了板端资源已释放。

## 6. 证据入口

`local-evidence.json`：时标、历史完整性、锁定工件、历史板端摘要、运行时字段null。

`source-and-script-audit.json`：HWH/Tcl/HTML摘录、候选源码摘要及AST调用/导入清单。

`amd-*.json`：官方检索原始响应；`offline-tests.txt/xml`：14项离线结果；`candidate-default-result.json`：无实时证明时默认停止。

`evidence-sha256.json`：本目录交付文件摘要。该目录本身为独立新结果，原开发目录和当前worktree保持未提交、未合并。
