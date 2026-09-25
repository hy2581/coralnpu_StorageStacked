# Tiny LLM：微型字符级 Transformer

[kernel.cc](kernel.cc) 在 CoralNPU 内完成 prefill、因果注意力、FFN、KV cache 和逐 token 生成。
内置模型为 1001 参数的标量 FP32 decoder。

```bash
./run.sh llm
./run.sh llm --benchmark config/benchmarks/tiny_llm_no_cache.json
```

模型、训练语料与配置统一在 [`config/llm/`](../../config/llm/)，负载预设在
[`config/benchmarks/`](../../config/benchmarks/)。宿主参考、训练与验收分别由
`scripts/llm_model.py`、`scripts/train_tiny_llm.py`、`scripts/verify_llm.py` 提供。
完整说明见 [LLM 负载与执行位置](../../docs/03-当前LLM负载与执行位置.md)。
