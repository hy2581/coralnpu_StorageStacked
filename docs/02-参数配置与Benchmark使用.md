# 如何修改参数、配置和 Benchmark

日常使用主要改两类 JSON：`architecture` 决定设备、链路和内存怎样运行；`benchmark` 决定设备程序处理多少数据、怎样访问和计算。所有公开配置都放在 `config/`，无需为了调参数去改驱动代码。

下面的命令均在本项目根目录执行。首次使用先运行 `./run.sh setup` 和 `./run.sh build`；已有可用构建时，可以直接运行用例。

## 1. 先选对文件

| 想做的事 | 应修改或选择的位置 |
|---|---|
| 调 NPU 时钟、AXI 时钟、队列或在线内存 | [config/architecture.json](../config/architecture.json) |
| 调数据量、重复次数、地址间隔或计算常数 | [config/benchmark.json](../config/benchmark.json) |
| 使用现成的跨步负载 | [config/benchmarks/strided.json](../config/benchmarks/strided.json) |
| 使用现成的反压配置 | [config/architectures/backpressure.json](../config/architectures/backpressure.json) |
| 用最小负载展示完整链路 | [config/benchmarks/smoke.json](../config/benchmarks/smoke.json)，使用 `./run.sh smoke`；见[详细报告](04-SMOKE全流程与结果分析.md) |
| 运行微型语言模型、修改提示词和生成长度 | [config/benchmarks/tiny_llm.json](../config/benchmarks/tiny_llm.json)，使用 `./run.sh llm` |
| 修改设备程序的计算逻辑 | [benchmarks/memory_roundtrip/kernel.cc](../benchmarks/memory_roundtrip/kernel.cc) |
| 调编译并行度或本项目工具目录 | [config/environment.sh](../config/environment.sh) |
| 修改地址布局或硬件接口 | 需同步修改配置、驱动和设备程序，见本文第 6 节 |

建议把实验配置另存为 `config/architectures/my_case.json` 或 `config/benchmarks/my_case.json`，保留默认配置便于比较。

## 2. 架构参数：调运行方式

当前默认值如下。“在途请求”指已经发出但尚未收到完成响应的请求。

| 字段 | 默认值 | 通俗说明与限制 |
|---|---:|---|
| `device_clock_mhz` | 500 | NPU 时钟频率，单位 MHz；正整数，最大 10000，且必须整除 10⁹，才能换算成整数 fs 周期 |
| `axi.period_ns` | 2 | AXI 每拍时间，单位 ns；1～1000，数值越大时钟越慢 |
| `axi.outstanding` | 16 | 适配器最多保留多少笔在途请求；1～128，满时向 NPU 反压 |
| `axi.planes` | 2 | 协议桥使用的资源平面数，可理解为桥内部的资源分组；1～4，不代表 NPU 核数 |
| `axi.stalls` | `true` | 启用确定性的通道等待，用于检查握手和反压；使用 JSON 布尔值 |
| `axi.replay` | `false` | 是否注入链路数据错误并检查 CRC 重放恢复；使用 JSON 布尔值 |
| `memory.standard` | `"hbm4"` | 内存模型预设；可选 `hbm3`、`hbm4`、`lpddr5`、`lpddr6` |
| `memory.channels` | 2 | 内存通道数；配置校验接受 1～1024，具体模型还需支持该组合且容量足够 |
| `memory.scale` | 1 | 内存时间倍率；1～1024，增大后每个内存 tick 对应更长的全局时间 |
| `memory.queue` | 4 | 内存模型的队列深度参数；1～1024 |
| `memory.slots` | 8 | 内存桥最多接纳的请求槽数；1～1024 |
| `memory.response_hold` | 0 | 内存完成后额外等待多少个内存 tick 再返回；非负整数，用于反压实验 |
| `max_ticks` | 20000000000000 | 仿真时间上限，单位 fs；正整数且不超过 10¹⁷，默认相当于 20 ms 仿真时间 |

这些运行参数修改后，下一次 `run` 生效。`max_ticks` 限制的是模型中的时间，不是电脑上的运行秒数；触及上限会判失败，不能把它当作正常结束条件。

`memory.scale=4` 表示把内存时间轴放慢四倍，不表示 NPU 总运行时间一定变成四倍。NPU 还有计算、总线和链路开销。它也不等同于选中了另一款实际内存器件。

当前端到端验收使用 HBM4。其他标准出现在可选列表中，仅说明配置入口支持选择，不能据此认定所有参数组合都已验收。配置的在途上限为 16，也不表示负载能同时发出 16 笔；现有自检负载实测最大在途数为 1。

## 3. Benchmark 参数：调设备程序做多少工作

本节介绍默认 `memory_roundtrip` 自检。语言模型使用独立的 `tiny_llm` 参数格式，部署与参数见[第三篇说明](03-当前LLM负载与执行位置.md)。

当前默认 [benchmark.json](../config/benchmark.json) 为：

```json
{
  "name": "memory_roundtrip",
  "words": 64,
  "iterations": 1,
  "stride_words": 1,
  "seed": 90,
  "multiplier": 2
}
```

| 字段 | 作用 |
|---|---|
| `name` | 本表为 `memory_roundtrip`；选择 `tiny_llm` 时需使用该负载自己的字段 |
| `words` | 每轮处理多少个 32 位整数；1 个 word 为 4 字节 |
| `iterations` | 完整的“写输入、计算输出、回读检查”重复几轮 |
| `stride_words` | 相邻元素地址间隔几个 word；1 是连续访问，2 是每隔 8 字节访问 |
| `seed` | 输入公式中的异或常数，使测试数据可重复；范围 0～65535 |
| `multiplier` | 计算 `输出 = 输入 × multiplier + 1` 时使用的乘数 |

`words`、`iterations`、`stride_words`、`multiplier` 必须是 1～16384 的整数，还要同时满足：

```text
words × stride_words ≤ 16384
words × iterations ≤ 1000000
```

第一条避免输入、输出缓冲区互相覆盖；第二条限制验收负载规模。数据按无符号 32 位整数计算，溢出按 32 位回绕。

例如，把 `words` 改成 128、`iterations` 改成 2、`stride_words` 改成 2，就得到仓库现有的跨步预设。外部读写事务数为：

```text
读次数 = 2 × words × iterations
写次数 = 2 × words × iterations
总事务数 = 4 × words × iterations
```

因此默认是 256 笔，跨步预设是 1024 笔。完成状态寄存器的访问不计入这些外部内存事务。

## 4. 常用运行方法

```bash
# 使用默认架构与默认 benchmark，自动新建结果目录
./run.sh run

# 四笔外部事务的最小冒烟
./run.sh smoke

# 使用现成的跨步负载
./run.sh run --benchmark config/benchmarks/strided.json

# 使用内置训练权重运行微型语言模型
./run.sh llm

# 只在这次运行中放慢内存，不改配置文件
./run.sh run --scale 4

# 注入链路错误，检查 CRC 重放后数据是否仍正确
./run.sh run --replay

# 不同 NPU/AXI 时钟、单在途请求、小队列的反压场景
./run.sh run --architecture config/architectures/backpressure.json

# 组合选择架构与 benchmark，并明确指定一个新的输出目录
./run.sh run --architecture config/architectures/backpressure.json \
  --benchmark config/benchmarks/strided.json --output results/my-strided-backpressure

# 完整验收，使用自动生成的新目录
./run.sh test
```

`--scale` 和 `--replay` 覆盖本次配置，不写回原 JSON。`--architecture`、`--benchmark` 的相对路径以项目根目录为基准；`--output` 的相对路径以执行命令时所在目录为基准。输出目录必须尚不存在，重复实验请换名字或不指定 `--output`。

组合配置示例说明参数可以怎样搭配，不代表该组合已经包含在现有验收记录中。两个 SMOKE 场景、五个内存自检场景和六个 LLM 场景定义在 [scripts/test.py](../scripts/test.py)。

## 5. 什么情况下需要重新编译

| 改动 | 操作 |
|---|---|
| 修改架构 JSON 中的运行参数 | 直接 `./run.sh run` |
| 修改 benchmark JSON 中已有参数 | 直接 `./run.sh run`；运行器自动重编译设备 ELF |
| 修改 `benchmarks/` 中的设备程序 | 先 `./run.sh build`，再运行所需的 `run`、`smoke` 或 `llm` |
| 修改原生驱动、AXI 适配器、内存桥或 RTL | 先 `./run.sh build`，再 `./run.sh test` |
| 修改本项目工具安装位置 | 先 `./run.sh setup`，再 `./run.sh build` |

[scripts/configure.py](../scripts/configure.py) 校验参数，并按负载生成 `third_party/coralnpu/native/` 下的 `benchmark_config.h`、`smoke_config.h` 或 `llm_config.h`，同时复制对应目录的 `kernel.cc`。`scripts/build_device.sh` 编译 ELF，`scripts/run.py` 负责判断参数是否改变。

因此不要直接编辑 `third_party/coralnpu/native/ddr_touch.cc`、`benchmark_config.h` 或复制过去的驱动文件。应修改 `benchmarks/`、`config/` 或 `integration/coralnpu/` 中的原始文件；生成文件会被覆盖。

## 6. 哪些改动不能只靠 JSON

当前固定一个 CoreMiniAxi 实例，AXI 后级固定 256 位，NPU 原生接口固定当前支持的单拍请求形式。核数、RVV、RTL 数据通路、AXI 位宽和链路帧格式都不是现成的 JSON 开关。此类修改需要调整硬件或接口源码，并补充相应验收。

[config/addrmap.json](../config/addrmap.json) 记录地址约定，但运行库和设备程序中也有对应常量。例如输入从 `0x90000000` 开始、输出从 `0x90010000` 开始、mailbox 位于 `0xc0000000`。修改地址时需同步核对 `benchmarks/memory_roundtrip/kernel.cc`、`integration/coralnpu/coralnpu_native.cc`、`simulation/config.hh`、`scripts/configure.py` 和 `scripts/verify.py`。单独修改地址 JSON 不会自动重定位硬件或程序。

`../axi_StorageStacked/config/memory/*.cfg` 服务于 mem_sim 原生工具及独立实验。在线主链路根据架构 JSON 中的 `memory.*` 和内置预设建立模型，直接修改这些 `.cfg` 不会自动覆盖在线主链路。

增加全新 benchmark 时，要同步实现设备程序、构建目标/选择逻辑、配置校验和独立结果检查。`scripts/verify.py` 先检查公共链路，再按 benchmark 分别检查 SMOKE 加一、内存乘加或 LLM 计算；SMOKE 与 LLM 检查分别在 `scripts/verify_smoke.py` 和 `scripts/verify_llm.py`。新增算法需要对应的独立通过条件。

## 7. 怎样确认参数真的生效

每次运行完成后，先看结果目录中的 `summary.json` 是否为 `passed: true`，再查看下面的证据：

| 文件 | 回答什么问题 |
|---|---|
| `resolved.json` | 本次实际选择了哪些架构和 benchmark 参数，包括命令行覆盖 |
| `config.json` | 主程序实际读入的设备周期、AXI 参数与运行方式 |
| `memsim_config.json` | 内存模型实际采用的标准、时间倍率、队列与容量 |
| `completion.json` | 设备何时结束、经历多少周期、完成状态是什么 |
| `protocol_summary.json` | 实际在途数、反压次数、五通道握手数、是否排空 |
| `npu_requests.csv` / `memsim_bridge.csv` | 实际访问地址、字节、响应和时间 |
| `environment.json` | 当次源码版本、使用的产物文件名与大小 |

`./run.sh check` 只检查环境和依赖隔离；它不会代替一次计算与访存验收。`run` 失败时保留结果目录，可查看 `run.log`、`validation-run.log`、`verification.log`；若失败发生在自动编译阶段，则查看 `benchmark-build.log`。

已完成的实验见 [ACCEPTANCE.md](../ACCEPTANCE.md)。改动源码后使用新的验收目录，保留原始波形和内存证据，便于与之前的结果比较。
