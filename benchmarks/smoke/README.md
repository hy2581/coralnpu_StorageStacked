# SMOKE：最小完整链路

[kernel.cc](kernel.cc) 只做一个 32 位整数加一：NPU 写 41 → 读 41 → 写 42 → 读 42 校验。
这四笔外部事务共用生产链路；mailbox 只报告完成状态，不能替代独立验收。

```bash
./run.sh smoke
# 指定新结果目录
./run.sh smoke --output results/my-smoke
```

配置在 [config/benchmarks/smoke.json](../../config/benchmarks/smoke.json)，公共架构在
[config/architecture.json](../../config/architecture.json)。`input` 可设置为任意 uint32 值，
结果为 `(input + 1) mod 2^32`。

`scripts/verify_smoke.py` 从实际返回字节核对结果，生成四笔事务的链路时间表。
成功以结果目录 `summary.json` 的 `passed: true` 为准；便于阅读的逐笔记录在
`smoke_report.md`，完整原始证据仍在同一目录。

从获取项目到结果分析的详细报告见 [SMOKE 全流程](../../docs/04-SMOKE全流程与结果分析.md)。
