# 回归验收记录（2026-09-25）

PASS：8 个设备场景、19 项原生内存测试、在线 C ABI，以及 33 项错误拒绝、边界与参考检查通过。

补充批次：[18:36 回归](../../.cache/validation/test-20260925-183647/report.md)、[19:07 回归](../../.cache/validation/test-20260925-190728/report.md)，均为 8 个场景通过；后者另含 18 项参数、路径、故障处理及自定义项目检查。

以下为当日保存的运行结果。复现时从仓库根目录执行 `./build.sh --test`，以新生成的报告为准。

| 场景 | 输出 | NPU 周期 | 外部事务 | 报告 |
|---|---|---:|---:|---|
| smoke | `42` | 287 | 4 | [查看](../../../user/smoke/result/test-20260925-180245/smoke/report.md) |
| smoke_slow | `42` | 815 | 4 | [查看](../../../user/smoke/result/test-20260925-180245/smoke_slow/report.md) |
| llm | `"blu"` | 320502 | 7111 | [查看](../../../user/llm/result/test-20260925-180245/llm/report.md) |
| llm_slow | `"blu"` | 461980 | 7111 | [查看](../../../user/llm/result/test-20260925-180245/llm_slow/report.md) |
| llm_replay | `"blu"` | 323741 | 7111 | [查看](../../../user/llm/result/test-20260925-180245/llm_replay/report.md) |
| llm_backpressure | `"blu"` | 247047 | 7111 | [查看](../../../user/llm/result/test-20260925-180245/llm_backpressure/report.md) |
| llm_no_cache | `"blu"` | 722751 | 15746 | [查看](../../../user/llm/result/test-20260925-180245/llm_no_cache/report.md) |
| llm_long | `". t"` | 475020 | 10243 | [查看](../../../user/llm/result/test-20260925-180245/llm_long/report.md) |

另外实际验证了：

- 无需登记名称的自定义项目：输入 7，计算 `7 × 3 + 3`，返回 24；将期望改为 25 后拒绝通过。
- JSON 将 UCIe 从 16 lanes 改为 8 lanes 后，实际配置随之改变；SMOKE 周期从 287 增至 332，输出保持 42。
- 将项目复制到另一目录，重建工具环境、平台和两个 ELF，并从仓库外启动 SMOKE：42、287 周期；库隔离检查通过。临时副本已清理，证据已归档。
- 公共存储的独立 SMOKE 通过 6 笔事务。

完整设备证据在 user/<项目>/result/test-20260925-180245/；附加测试证据在 coralnpu/.cache/validation/。

机器摘要：[summary.json](summary.json)；八组回归与错误检查：[regression.json](regression.json)。
