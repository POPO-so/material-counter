# Gray8 离线交付与回放

当前唯一输入协议是 Gray8。旧 RGB 构建、回放和板端诊断执行器已经退役；历史 data/results 报告及其原始字节保留，不作为当前源码的验收结果。

检查当前制品与源码：

    python scripts/delivery/verify_delivery.py

准备可移动的自包含 XSim 源码包（目录须不存在或为空）：

    python scripts/delivery/prepare_replay.py --out data/results/gray8-replay-new

在 Vivado 2026.1 Tcl 中执行包内 scripts/gray8/run_sim.tcl。源码相对脚本定位，不依赖旧工程路径；可通过 GRAY8_OUT 环境变量指定新的结果目录。

测试台自带激励、黄金比对、watchdog 和 PASS 条件，覆盖 Gray8 协议、错误包、反压、阈值边界及 320×240 S2MM 输出。离线 PASS 不等于板测、长期稳定性或物料准确率验收。
