# SMOKE 与 benchmark 目录重构验收（2026-09-25）

运行命令：`./run.sh test --output results/acceptance-smoke-20260925`。
详细步骤和分析见 [SMOKE 全流程报告](../../docs/04-SMOKE全流程与结果分析.md)。

[完整汇总](summary.json) 为 `passed: true`：13 个场景全部新跑且通过，未复用旧结果；
19 项原生测试、在线 C ABI 与各类负例/配置检查通过。

- `smoke/`：默认 41 加一得到 42，293 个 NPU 周期，四笔外部事务，10 次 AXI 握手。
- `smoke_slow/`：相同负载、memory.scale=4，输出仍为 42，821 个 NPU 周期。
- 两个 SMOKE 目录包含完整原始证据，包括五通道 VCD、双向 Flit、内存/DFI、镜像与 HTML 配套文件，可直接复制查看。
- `smoke-negative.json`：六种错误计算/事务证据和三种非法配置全部拒绝；uint32 两端合法配置校验通过。
- `smoke-setup-check.log`：已有工具与固定版本配置匹配；不是空白机器重新安装的记录。
- `smoke-refactor-build.log`：本次重构后的实际构建记录。
- `native-tests.log`、`environment-check.log`：原生测试和环境隔离记录。
- `standalone/`：最终 `./run.sh smoke --output results/smoke-final-20260925` 的独立复验与控制台结果。
- 其他用例目录：五个原有内存场景、六个 LLM 场景的小型验收记录；完整原始记录留在 `results/`。
- `deployment-files.json`、`raw-evidence-files.json`：来源基线提交、当前部署文件名/大小和原始证据位置/体积。

完整验收目录 `results/acceptance-smoke-20260925/` 还保留原有五个内存场景、六个 LLM 场景和负例检查。
各例 `environment.json` 记录源码来源和运行文件名/大小，`resolved.json` 记录实际参数。
本目录的 `summary.json` 与完整结果目录中最终汇总相同。
