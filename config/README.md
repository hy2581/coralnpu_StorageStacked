# 配置说明

`architecture.json` 和选中的 benchmark JSON 是运行输入，`results/.../resolved.json`
是当次实际使用的完整快照。参数拼错、未知字段、非法范围会直接报错。
修改时钟/队列等运行参数后直接 `./run.sh run`，无需修改脚本。

## 共用架构参数

| 字段 | 含义 | 生效方式 |
|---|---|---|
| `device_clock_mhz` | 设备时钟 MHz；必须能整除 10^9 得到整数 fs 周期 | 下一次运行 |
| `axi.period_ns` | AXI 时钟周期，整数 ns | 下一次运行 |
| `axi.outstanding` | NPU 原生请求最大在途数，满时向 RTL 反压 | 下一次运行 |
| `axi.planes` | AoU 资源平面数，1～4 | 下一次运行 |
| `axi.stalls` | 五通道确定性反压 | 下一次运行 |
| `axi.replay` | 注入可重复的物理 Flit 错误并启用 CRC 重放 | 下一次运行 |
| `memory.standard` | `hbm3` / `hbm4` / `lpddr5` / `lpddr6` | 下一次运行 |
| `memory.channels` | 内存通道数；总容量必须覆盖设备窗口 | 下一次运行 |
| `memory.scale` | 内存 tick 相对主仿真时间的倍率；越大越慢 | 下一次运行 |
| `memory.queue` | 原生入口、控制器和响应队列深度 | 下一次运行 |
| `memory.slots` | 内存桥最多同时接纳的 AXI burst 数 | 下一次运行 |
| `memory.response_hold` | 返回时额外等待的内存 tick 数，用于反压测试 | 下一次运行 |
| `max_ticks` | 仿真 watchdog 上限，单位 fs；到上限视为失败 | 下一次运行 |

`--scale N` 和 `--replay` 只覆盖本次运行，不写回 JSON。内存不是离线 trace 回放：
修改 scale 会改变读写返回时间，并反过来改变设备执行周期。
`memory.standard` 选择内置标准，支持列表不代表各个标准都已完成本项目端到端验收；
实际覆盖范围以 `results/.../summary.json` 为准。

AXI 数据宽度固定为 256 bit，WSTRB 为 32 bit，链路帧格式固定；这两项属于接口 ABI，
不能仅改 JSON。`addrmap.json` 保存固定地址 ABI，需与设备运行库/内核共同修改。
`$STORAGE_STACK_ROOT/config/memory/` 是 mem_sim 原生工具的配置文件（`axi_StorageStacked/mem_sim/configs` 是指向它的工程内链接），
用于原生模型测试和独立实验；在线主链路由上述 `memory.*` 参数和标准内置预设构造。
直接改 `memory/*.cfg` 不会自动覆盖在线主链路。

## 环境

`environment.sh` 配置 `.deps` 位置和 `BUILD_JOBS`；默认都在本工程内。
`conda-linux-64.lock` 固定 GCC 13.4、Python 3.12 和构建工具；
`sources.json` 记录基线源码版本及本地适配，`xpu-artifacts.lock.json` 记录相关工具版本、文件名和大小。
修改工具目录后执行 `./run.sh setup && ./run.sh build`。

## CoralNPU

本工程固定一个 CoreMiniAxi RTL 实例；不能像 SimX 一样用 JSON 改变核数或 RTL 数据通路。
修改此类微架构需改 `third_party/coralnpu/hdl/` 并重新执行 build。
设备时钟、完整存储链路和 benchmark 可独立配置。

| benchmark 字段 | 含义 |
|---|---|
| `name` | 本表适用于 `memory_roundtrip`；`tiny_llm` 参数见下节 |
| `words` | 每轮操作的 32 位字数 |
| `iterations` | 重复轮数 |
| `stride_words` | 相邻访问间隔，单位 32 位字 |
| `seed` | 输入模式的异或种子，0～65535 |
| `multiplier` | 输出计算倍数：`out = in * multiplier + 1`，按 uint32_t 回绕 |

每轮 NPU 自行完成：写入 input → 读 input/写 output → 回读 output 并逐字校验。
每轮产生 `2 * words` 次读和 `2 * words` 次写；完成 mailbox 为 `0x600d0000`。
CoralNPU 原生 AXI beat 为 16 字节，标量写用字节掩码选中其中 4 字节；
因此总线传输字节数与有效计算字节数不同。适配后的 AXI256 接口保留这些语义。
Python 校验还会按独立公式核对每笔输入/输出字节、读返回和最终镜像。

启动和 ELF 装载在本地 TCM 进行，外部内存没有预填充或同步旁路。

input 起址 `0x90000000`，output 起址 `0x90010000`，
因此要求 `words * stride_words <= 16384`。mailbox 在 `0xc0000000`，属于设备控制状态。
benchmark JSON 变化后，运行器自动生成 `benchmark_config.h` 并只重编译 ELF，不重复生成 RTL 仿真库。
修改 benchmark C++ 源码则先执行 `./run.sh build`。

```bash
./run.sh run --benchmark config/benchmarks/strided.json
```

该预设为 128 字、2 轮、跨 2 字访问，用于证明配置改变了实际地址和请求数量。
新增计算 benchmark 时，从 `benchmarks/memory_roundtrip/kernel.cc` 出发，
同步 `scripts/configure.py` 的参数校验和 `scripts/verify.py` 的计算验收规则。

`--architecture config/architectures/backpressure.json` 使用不同 NPU/AXI 时钟、单个在途请求、单深度内存队列和额外响应等待，验证反压与调度。`test` 自动覆盖该场景。

## 最小 SMOKE benchmark

`./run.sh smoke` 选择 `config/benchmarks/smoke.json`：

```json
{"name": "smoke", "input": 41}
```

`input` 为 0～4294967295 的整数，设备输出为 `(input + 1) mod 2^32`。
固定产生四笔外部事务：写输入、读输入、写输出、读输出；不增加循环或模型依赖。
源码在 `benchmarks/smoke/kernel.cc`，生成头文件为 `smoke_config.h`，产物为 `build/coralnpu/smoke.elf`。
`--architecture`、`--scale` 和输出规则与其他负载相同，配置改变后自动重编译 ELF。
详细部署、链路与实测结果见 [SMOKE 全流程](../docs/04-SMOKE全流程与结果分析.md)。

## 微型语言模型 benchmark

`./run.sh llm` 默认选择 `config/benchmarks/tiny_llm.json`。模型在 CoralNPU 内执行浮点计算，
权重、KV cache、输入 token 与输出证据均经现有 AXI/UCIe/在线 mem_sim 通路访问。

| 字段 | 默认值 | 含义 |
|---|---|---|
| `name` | `tiny_llm` | 选择微型 decoder 程序 |
| `model` | `config/llm/tiny_char_v1.json` | 本项目内的模型文件，含版本、结构、词表和训练权重 |
| `prompt` | `"red "` | 字符级输入；必须非空，全部字符必须在模型词表中 |
| `generated_tokens` | 3 | 固定生成的字符 token 数，1～8；不使用提前停止 |
| `kv_cache` | `true` | 复用历史 K/V；为 false 时每次生成重新计算整个前缀 |

必须满足 `字符数(prompt) + generated_tokens - 1 <= 16`，因为最后生成的 token 不再送入模型。
当前支持固定的 1 层、dim=8、heads=2、FFN=16、context=16 模型格式，词表最多 32 字符。
修改层数或维度需要扩展模型格式、NPU 程序、存储布局和参考验收，不能只修改 JSON 数字。

`tiny_llm_no_cache.json` 使用相同输入和权重关闭缓存；`tiny_llm_long.json` 使用 `"one two"` 检查较长上下文。
修改这些 benchmark 参数或模型文件后，运行器会重新生成 `llm_config.h` 并编译 `tiny_llm.elf`。
修改 `benchmarks/tiny_llm/kernel.cc` 后先执行 `./run.sh build`；下一次 LLM 运行会编译新的设备程序。
生成文件在 `third_party/coralnpu/native/`，请编辑原始源码与配置。

`llm/training.json` 和 `llm/corpus.txt` 用于可选的本地重训练，不是常规运行依赖。
部署、数值校验与计时口径见 [LLM 说明](../docs/03-当前LLM负载与执行位置.md)。

## 独立存储依赖

`storage_dependency.json` 声明公共项目版本和 AXI 接口。默认使用同级 `axi_StorageStacked`，
可通过 `STORAGE_STACK_ROOT` 指定其他位置。存储原生配置仅存在于公共项目；
本驱动的架构/运行配置仍决定本次在线链路参数。修改公共 C++ 源码后对使用它的驱动执行 build。
