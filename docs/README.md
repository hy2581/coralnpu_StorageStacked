# CoralNPU 文档入口

“负载”就是让模拟设备执行的任务。第一次使用，建议按下表顺序读，
先看到一份能核对的结果，再深入源码。

| 顺序 | 文档 | 读完能做什么 |
|---|---|---|
| 1 | [环境配置](01-用户环境配置说明.md) | 准备平台，找到实际输入和结果目录 |
| 2 | [配置实验与 USER 负载](05-配置实验与USER负载详解.md) | 改输入、比较慢内存和 KV cache，按阶段排错 |
| 3 | [从零添加 SMOKE](02-用户从0开始添加SMOKE简要指南.md) | 创建自己的任务目录、配置和源码 |
| 4 | [LLM 说明](03-LLM简要说明.md) | 看懂短文本输入、生成过程和检查结果 |
| 5 | [integration 说明](04-integration介绍.md) | 跟踪一笔计算请求怎样变成 AXI 访问 |

## 命令在哪执行

- 仓库根目录：`./build.sh --storage ../axi_StorageStacked --jobs 4`。
- 仓库的 `user/`：`./run.sh smoke` 或 `./run.sh llm`。
- 也可以从根目录执行 `./user/run.sh smoke`；配置和输出仍相对于所选任务目录。

例：`./run.sh smoke --config experiment.json --output result/trial-01`
读取 `user/smoke/experiment.json`，写入 `user/smoke/result/trial-01/`。
新结果目录必须尚不存在。

## 报告先看什么

先看 `report.md` 和 `summary.json`，再看 `smoke_summary.json` 或 `llm_summary.json`。
Vortex 另有 `host_summary.json`，检查模拟 CPU 的执行和线程分工。
CoralNPU 的 `completion.json` 保存设备结束原因和 mailbox 状态。
最终 `summary.json.passed=true` 表示整次检查通过；仅编译成功或设备结束还不够。

当前文档依据工作区源码核对。文中的预期输出是操作指引，
历史结果和本次实际运行结果应分别查看，不能用文档检查替代仿真验收。
