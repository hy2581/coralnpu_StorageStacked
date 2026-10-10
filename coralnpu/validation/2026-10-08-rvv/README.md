# RVV 与单次回归验收

从仓库根目录执行一次：

```bash
./build.sh --test
```

回归执行默认 SMOKE 和默认 LLM 各一次，随后完成原生内存、在线 C ABI 和错误拒绝检查。
单独验收 LLM 时，在 `user/` 中执行 `./run.sh llm --test`。

设备库定义 `ENABLE_RVV`，实例化 `VRvvCoreMiniAxi`；用户程序按
`rv32imf_zve32f_zicsr_zifencei` 编译。TinyLLM 的 7 个线性层使用 RVV FP32，
LayerNorm、注意力、Softmax 和生成控制使用标量运算。

编译阶段检查 `build/program.dis` 中的 `vsetvli`、`vle32.v`、`vfmul.vf`、
`vfadd.vv`、`vse32.v`。设备将 RVV 线性层执行次数写入 `report[9]`，
独立校验器要求它等于 `7 × forward_calls`，同时核对权重、KV、各阶段中间值和生成 token。
默认输入 `"red "`、生成 3 个字符时，期望 `"blu"`、token ID `[4,10,15]`、42 次 RVV 线性层调用。

实际通过结论、周期和事务数以本次运行报告与 `summary.json` 为准。

## 本次运行结果（2026-10-08）

`./build.sh --test` 通过；运行编号 `test-20261008-164539`，平台回归执行一次。
19 项原生内存测试、在线 C ABI、34 项错误拒绝／边界／参考检查通过。
设备 ELF 的 RVV 指令检查与设备库的 RVV 核检查通过。

| 默认场景 | 输出 | NPU 周期 | 外部事务 | 归档摘要 |
|---|---|---:|---:|---|
| SMOKE | `42` | 287 | 4 | [查看](summary.json) |
| LLM | `blu`，ID `[4,10,15]` | 194106 | 4196 | [查看](summary.json) |

默认配置：NPU 500 MHz、AXI 2 ns、HBM4 两通道、KV cache 启用。
LLM 的 42 次 RVV 线性层调用与独立参考一致；最大中间值绝对误差约 `6.98e-6`，
低于本次配置的误差容限。推理至最后一个 token 为 `297030 ns`，包含观测记录访存开销。

归档证据：[回归摘要](summary.json)、[LLM 数值与执行摘要](llm_summary.json)、
[运行输入](input.json)、[ELF 反汇编](program.dis)、[源码基线与文件大小](source_version.json)。
源码版本记录注明本次使用工作区修改；原始 AXI 波形、Flit 和内存记录保存在对应的用户结果目录中。
