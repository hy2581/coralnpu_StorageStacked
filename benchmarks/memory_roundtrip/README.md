# Memory roundtrip：多字读写与计算

[kernel.cc](kernel.cc) 在 NPU 内生成输入、读入后乘加、再回读检查；支持字数、轮数和跨步访问。

```bash
./run.sh run
./run.sh run --benchmark config/benchmarks/strided.json
```

默认参数见 [config/benchmark.json](../../config/benchmark.json)，修改方法见
[参数与 Benchmark 说明](../../docs/02-参数配置与Benchmark使用.md)。
计算校验在 `scripts/verify.py`，与 SMOKE、LLM 共用协议和原始波形检查。
