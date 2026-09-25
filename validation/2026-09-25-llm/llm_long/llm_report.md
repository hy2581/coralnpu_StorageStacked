# CoralNPU 微型语言模型 benchmark

数值与访存检查通过。完整的 AXI/Flit/内存验收结果以本目录 summary.json 为准。

- 模型：tiny-char-trained-v1，1001 个参数，标量 FP32。
- 输入："one two"
- 生成：". t"，token ID：[2, 1, 14]
- KV cache：True；实际前向位置计算次数：9。

| 阶段 | 仿真时间 |
|---|---:|
| 模型与输入初始化 | 90498.000 ns |
| Prefill 至首 token | 649038.000 ns |
| 推理至最后一个 token | 858724.000 ns |

后续 token 间隔（ns）：[103656.0, 106030.0]。
解码吞吐：9538.071211239663 token/仿真秒；只生成一个 token 时此值为空。

时间由 NPU 发出的完成标记与 token 写回响应测量，包含中间结果记录开销；prefill/decode 不包含模型初始化。
这是小语料训练的字符模型功能基准，不能用来代表通用大模型质量或真实芯片吞吐。

数值详情见 llm_summary.json；逐请求链路页面见 memsim_view.html。
