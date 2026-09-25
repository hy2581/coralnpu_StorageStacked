# Benchmark 目录

每种负载使用独立目录，`kernel.cc` 是 CoralNPU 实际执行的源码。
配置统一保存在 [`config/`](../config/README.md)，公共构建、运行与验收在 `scripts/`，
生成的设备源码副本和头文件在 `third_party/coralnpu/native/`，运行结果在 `results/`。

```text
benchmarks/
├── README.md
├── smoke/
│   ├── README.md
│   └── kernel.cc
├── memory_roundtrip/
│   ├── README.md
│   └── kernel.cc
└── tiny_llm/
    ├── README.md
    └── kernel.cc
```

| 负载 | 用途 | 默认配置 | 入口 |
|---|---|---|---|
| [SMOKE](smoke/README.md) | 41 加一，四笔外部事务看完整链路 | [smoke.json](../config/benchmarks/smoke.json) | `./run.sh smoke` |
| [Memory roundtrip](memory_roundtrip/README.md) | 多字、多轮、跨步读写计算 | [benchmark.json](../config/benchmark.json) | `./run.sh run` |
| [Tiny LLM](tiny_llm/README.md) | 微型 Transformer 完整推理与 KV cache 对照 | [tiny_llm.json](../config/benchmarks/tiny_llm.json) | `./run.sh llm` |

修改设备源码后先执行 `./run.sh build`；运行器会在配置切换时编译对应 ELF。
所有负载共用 CoralNPU RTL → 原生异步接口 → AXI256 → AXI2Flit → UCIe → 在线 mem_sim，
并校验真实请求、返回字节、五通道波形和 Flit/内存证据。

重构前 `memory_roundtrip.cc`、`tiny_llm.cc` 平铺在这里，另有一个空的 `llm/`。
现在统一为上面的结构，原有 `run`、`llm` 和配置路径保持可用。
