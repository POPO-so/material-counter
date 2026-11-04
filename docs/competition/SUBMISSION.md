# 提交准备与候选工件

[文档目录](../README.md) · [结果与待办](RESULTS.md)

## 已先行完成的离线核对

2026-11-04 对仓库现有 JPEG v8 ZIP 做了逐文件核对：ZIP、manifest、BIT/HWH 的 SHA-256 与发布收据一致；18 个包内源码文件同时匹配其 manifest 和固定源码提交。检查未访问开发板，机器可读结果见[候选绑定清单](../verification/submission-candidates-20261104.json)。

这个核对确定候选包的身份，不选定正式比赛版本，也不证明安装依赖闭包、上板运行、真实物料准确率或当前合并源码已生成新位流。

## 版本对照

| 对象 | 固定身份 | 可引用范围 |
| --- | --- | --- |
| 本轮文档核对的 LJ-01 快照 | `7e3f01da6232d6293f6b8d7267e9a3ca3de8cb28` | 当前源码与展示文档快照，不是比赛硬件定版 |
| JPEG v8 独立候选源码 | `b269ee01c765a43f0cfa6aeca657b63b34f02b2b` | 已验证包内 18 个源码文件与该 Git 提交字节一致 |
| JPEG v8 ZIP | 1,323,908 B；SHA-256 `6939451aa26c943c7a66c1f60ba1b7d5d650e2dd2a758515eb6f16b2dedf817f` | [现存候选包](../../data/results/jpeg-pl-board-20261102/jpeg-pl-runtime-v8.zip) |
| 包内 manifest | SHA-256 `ddf649cfd77e3a3178c90a9272fc9d111dbf14091d2572f1d8189c63e71ae859` | 包内源码与 BIT/HWH 的文件清单 |
| 包内 BIT | SHA-256 `9535be948365ff2278232e13a9dbe2f99cb521ca193aa82b28fc06a401e264ed` | 独立候选工件，保留其构建阶段状态 |
| 包内 HWH | SHA-256 `79b8abcb954667e6791c8d7f0eba3e59ad2215ca3f175ebb8b5e98892dd9968f` | 与候选包内声明一致，不移记为合并源码的新工件 |
| V12 显示集成记录 | 来源 `ddd5174d1304ac901e5fe2ca05247f8c770d89de` | 依[部署记录](../../scripts/standalone/DEPLOYMENT.md)单独核对；本轮未重新校验整个 V12 包 |

## 不可混用的六个源码文件

与 `7e3f01da` 快照比较，JPEG v8 包内 18 个源码中有 12 个相同、6 个不同：

- `scripts/rle/run_counter.py`
- `scripts/wo0016/split_touching.py`
- `scripts/wo0016/stream_counter.py`
- `scripts/rle/components.py`
- `scripts/rle/counter.py`
- `scripts/rle/transport.py`

这不是损坏判定，表示包和默认分支属于不同快照。不能把工作区的这六个文件覆盖到 v8 包后继续沿用原包哈希和验证结论。详细哈希见候选绑定清单。

## 正式提交时仍需固定的内容

正式版本须明确源码 commit/tag、源清单、BIT/HWH、manifest、原生库及依赖、计数/相机/显示配置和对应运行报告。存在多个候选时先记录对照，不自动创建“最终版”标签，也不把旧板测绑定到新源码。

其他资料入口：[硬件与连线](HARDWARE.md) · [第三方来源](THIRD_PARTY.md) · [技能包](../../skill/README.md) · [演示与视频](DEMO.md)。
